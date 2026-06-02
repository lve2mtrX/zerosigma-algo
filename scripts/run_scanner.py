"""One-shot scanner tick — Phase 1.5 provider-separated data flow.

  StructureProvider.get_snapshot(symbol)  →  StructureSnapshot (context)
  QuoteProvider.get_option_chain(symbol)  →  OptionChainSnapshot (prices)
                          │
                          ▼
  Strategy.generate_candidates(structure, chain, params)
            → apply_filters → score → select → log + CSV

Writes to BOTH `outputs/latest/` (snapshot view) and `outputs/runs/{date}/`
(append-only history). The decision log carries the names + timestamps of
both providers + the spot from the quote provider so the audit trail makes
clear which data drove which decision.

No broker. No ZS API. No live execution.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="ZerσSigma algo scanner — one-shot tick")
    parser.add_argument("--profile", default=None,
                        help="risk profile name (defaults to active_profile in YAML)")
    parser.add_argument("--strategy", default=None,
                        help="restrict to one strategy_id (default: all enabled)")
    parser.add_argument("--symbol",   default=None, help="symbol to scan (default: SPX)")
    parser.add_argument("--structure-provider", dest="structure_provider", default=None,
                        choices=["stub", "zerosigma_api"],
                        help="override active structure provider (default: from config/.env)")
    parser.add_argument("--quote-provider", dest="quote_provider", default=None,
                        choices=["mock", "null", "tastytrade"],
                        help="override active quote provider (default: from QUOTE_PROVIDER env "
                             "or config/.env; mock if neither). Phase 4: tastytrade requires "
                             "TASTY_* OAuth env vars — set up via scripts.probe_tastytrade first.")
    parser.add_argument("--dry-run",  action="store_true",
                        help="do not write decision_log / ranked_candidates")
    # Phase 4.1 — target-DTE plumbing (default 0 = today, behavior unchanged)
    parser.add_argument("--target-dte", dest="target_dte", type=int, default=None,
                        help="target days-to-expiry: 0=today, 1=tomorrow, ... "
                             "(default from TARGET_DTE env or scanner.expiry YAML or 0)")
    parser.add_argument("--dte-mode", dest="dte_mode", default=None,
                        choices=["calendar_days", "trading_days"],
                        help="DTE counting mode (default from DTE_MODE env or "
                             "scanner.expiry YAML or trading_days)")
    parser.add_argument("--allow-after-hours-roll", dest="allow_after_hours_roll",
                        action="store_true", default=None,
                        help="if past after_hours_cutoff_et AND target_dte=0, roll to next day "
                             "(default from ALLOW_AFTER_HOURS_EXPIRY_ROLL env or YAML or false)")
    # Phase 4.1 — per-candidate audit print (no truncation)
    parser.add_argument("--print-candidates", dest="print_candidates", action="store_true",
                        help="print per-candidate audit blocks to stdout (Phase 4.1)")
    args = parser.parse_args()

    import os as _os

    from src.app.session_state import SessionConfig
    from src.providers.quotes.factory import build_quote_provider
    from src.providers.quotes.tastytrade_provider import TastytradeConfigurationError
    from src.providers.quotes.types import QuoteRequest
    from src.providers.structure.factory import build_structure_provider
    from src.reporting.decision_log import log_decision, log_decision_to_file
    from src.risk.filters import apply_filters
    from src.risk.limits import load_profile
    from src.storage.csv_writer import append_csv_row, write_csv_snapshot
    from src.storage.paths import (
        decision_log_path,
        latest_dir,
        ranked_candidates_path,
    )
    from src.strategies.registry import load_strategies
    from src.utils.config import load_config
    from src.utils.expiry import ExpiryDecision, pick_target_expiry
    from src.utils.logging import get_logger
    from src.utils.time import now_et

    log = get_logger("scanner")
    cfg = load_config(REPO_ROOT)

    # ── Phase 4.1 — resolve target_dte knobs (CLI > env > YAML > default) ──
    expiry_cfg = (cfg.scanner.get("expiry") or {}) if isinstance(cfg.scanner, dict) else {}

    def _env_bool(name: str, default: bool) -> bool:
        v = _os.environ.get(name)
        if v is None or v == "":
            return default
        return v.strip().lower() in {"true", "1", "yes", "on"}

    if args.target_dte is not None:
        target_dte = int(args.target_dte)
    else:
        env_td = _os.environ.get("TARGET_DTE")
        if env_td not in (None, ""):
            try:
                target_dte = int(env_td)
            except ValueError:
                target_dte = int(expiry_cfg.get("target_dte", 0))
        else:
            target_dte = int(expiry_cfg.get("target_dte", 0))

    if args.dte_mode is not None:
        dte_mode = args.dte_mode
    else:
        dte_mode = _os.environ.get("DTE_MODE") or expiry_cfg.get("dte_mode") or "trading_days"
    if dte_mode not in ("calendar_days", "trading_days"):
        dte_mode = "trading_days"

    if args.allow_after_hours_roll is not None:
        allow_ah_roll = bool(args.allow_after_hours_roll)
    else:
        allow_ah_roll = _env_bool(
            "ALLOW_AFTER_HOURS_EXPIRY_ROLL",
            bool(expiry_cfg.get("allow_after_hours_roll", False)),
        )

    after_hours_cutoff_et = str(expiry_cfg.get("after_hours_cutoff_et", "16:00"))

    profile_name = args.profile or cfg.active_risk_profile
    profile = load_profile(cfg.risk_profiles, profile_name)
    session = SessionConfig.from_profile(profile)

    strategies = load_strategies(cfg)
    if args.strategy:
        strategies = {k: v for k, v in strategies.items() if k == args.strategy}
    if not strategies:
        log.error("No strategies to scan (profile=%s).", profile.name)
        return 2

    symbol = args.symbol or cfg.scanner.get("symbols", ["SPX"])[0]

    # Acquire BOTH providers — explicit separation. Both selectable via
    # CLI overrides. Quote provider falls back to mock by default; selecting
    # tastytrade requires `TASTY_*` OAuth env vars to be set.
    structure_provider, resolved_structure_name = build_structure_provider(
        cfg, override=args.structure_provider,
    )
    try:
        quote_provider, resolved_quote_name = build_quote_provider(
            override=args.quote_provider,
            yaml_active=cfg.providers.quotes_active,
            fallback_on_misconfig=False,    # fail loudly per Phase 4 spec
        )
    except TastytradeConfigurationError as exc:
        log.error("Tastytrade quote provider unavailable: %s", exc)
        return 4
    try:
        structure = structure_provider.get_snapshot(symbol)
    except Exception as exc:
        log.error("StructureProvider %s failed: %s — falling back to stub.",
                  resolved_structure_name, type(exc).__name__)
        from src.providers.structure.stub import StubStructureProvider
        structure_provider = StubStructureProvider()
        resolved_structure_name = "stub"
        structure = structure_provider.get_snapshot(symbol)

    # ── Phase 4.1 — discover available expiries from the broker chain when
    # possible. For tasty: probe.get_option_chain_summary. For mock/null/
    # stub providers: fall back to [structure.expiry] (single-element). The
    # pick_target_expiry call below will short-circuit to structure.expiry
    # at target_dte=0 for byte-identical default behavior.
    available_expiries: list[str] = []
    if resolved_quote_name == "tastytrade" and hasattr(quote_provider, "_probe"):
        try:
            summary = quote_provider._probe.get_option_chain_summary(symbol)  # type: ignore[attr-defined]
            if summary.get("ok"):
                ae: set[str] = set()
                for r in (summary.get("roots") or []):
                    for e in (r.get("expirations") or []):
                        if isinstance(e, str):
                            ae.add(e)
                available_expiries = sorted(ae)
        except Exception as exc:                # never fail the tick on probe issues
            log.warning("probe.get_option_chain_summary failed: %s — falling back",
                        type(exc).__name__)
    if not available_expiries and structure.expiry:
        available_expiries = [structure.expiry]

    # Decide which expiry to actually request.
    decision_now = now_et()
    expiry_decision: ExpiryDecision = pick_target_expiry(
        decision_now,
        target_dte,
        mode=dte_mode,
        allow_after_hours_roll=allow_ah_roll,
        available_expiries=available_expiries,
        after_hours_cutoff_et=after_hours_cutoff_et,
    )
    eff_expiry = expiry_decision.expiry or structure.expiry

    # Build a QuoteRequest from the structure so synthesis providers
    # (the mock) center their chain on real ZS levels rather than the
    # hardcoded 5800 default.
    required_strikes = _collect_required_strikes(strategies, structure)
    spot_hint, spot_hint_source = _pick_spot_hint(structure, required_strikes)
    quote_request = QuoteRequest(
        symbol=symbol,
        expiry=eff_expiry,
        spot_hint=spot_hint,
        required_strikes=tuple(required_strikes),
        spot_hint_source=spot_hint_source,
    )
    chain = quote_provider.get_option_chain(symbol, expiry=eff_expiry, request=quote_request)
    if chain is None:
        log.error(
            "QuoteProvider returned no chain for %s @ %s (target_dte=%d, src=%s) "
            "— aborting tick.",
            symbol, eff_expiry, target_dte, expiry_decision.source,
        )
        return 3
    quote_provider.get_spot(symbol)  # heartbeat
    quote_status = quote_provider.status()

    # Diagnostics: which required strikes did the chain actually contain?
    chain_strikes = {q.strike for q in chain.quotes}
    missing_required_quote_strikes = sorted(s for s in required_strikes if s not in chain_strikes)
    chain_min = min(chain_strikes) if chain_strikes else None
    chain_max = max(chain_strikes) if chain_strikes else None

    structure_missing = (structure.raw or {}).get("missing_fields") or []
    log.info(
        "scan tick: symbol=%s profile=%s strategies=%s structure=%s quotes=%s "
        "quote_provider=%s quote_root=%s quote_ts=%s "
        "missing_structure_fields=%d spot_hint=%.2f source=%s required=%d missing_quote_strikes=%d",
        symbol, profile.name, list(strategies.keys()),
        structure.source, chain.provider_name,
        resolved_quote_name, chain.resolved_root_symbol or "-",
        chain.quote_ts.isoformat(),
        len(structure_missing),
        spot_hint or 0.0, spot_hint_source, len(required_strikes),
        len(missing_required_quote_strikes),
    )

    output_root = cfg.output_dir
    latest = latest_dir(output_root)
    ranked_rows: list[dict] = []
    ts = now_et()

    for sid, strat in strategies.items():
        params = {**(strat.default_parameters or {}), **session.to_filter_params()}
        candidates = strat.generate_candidates(structure, chain, params)
        apply_filters(candidates, session.to_filter_params())
        for c in candidates:
            strat.score(c, structure, chain, params)
        decision = strat.select(candidates, params)

        # Phase 2.6 — refine the NO_TRADE explanation when the generic
        # `select()` couldn't see why zero candidates were generated.
        decision = _refine_decision_explanation(
            decision,
            required_strikes=required_strikes,
            missing_required_quote_strikes=missing_required_quote_strikes,
            structure=structure,
        )

        for c in candidates:
            row = _candidate_row(
                sid, c, session, ts, decision.decision, chain=chain,
                target_dte=target_dte,
                available_expiries=available_expiries,
                expiry_selection_reason=expiry_decision.source,
            )
            ranked_rows.append(row)
            if args.print_candidates:
                _print_candidate_audit(row, c, chain)

        snapshot_summary: dict = {
            "structure_provider": structure.source,
            "structure_ts":       structure.quote_ts.isoformat(),
            "quote_provider":     chain.provider_name,
            "quote_ts":           chain.quote_ts.isoformat(),
            "spot":               chain.spot,
            "structure_spot":     structure.spot,
            "maxvol":             structure.exposures.maxvol,
            "put_ceiling_2k":     structure.exposures.put_ceiling_2k,
            "put_ceiling_5k":     structure.exposures.put_ceiling_5k,
            "call_floor_2k":      structure.exposures.call_floor_2k,
            "call_floor_5k":      structure.exposures.call_floor_5k,
            "gamma_regime":       structure.exposures.gamma_regime,
            "structure_missing_fields": list(structure_missing),
            "structure_subscription_active": (structure.raw or {}).get("subscription_active"),
            # Phase 2.6 — quote alignment audit fields
            "required_strikes":      list(required_strikes),
            "quote_chain_min_strike": chain_min,
            "quote_chain_max_strike": chain_max,
            "missing_required_quote_strikes": list(missing_required_quote_strikes),
            "quote_spot_source":     spot_hint_source,
            "quote_spot_hint":       spot_hint,
            # Phase 4 — broker quote-provider audit fields
            "resolved_quote_provider":       resolved_quote_name,
            "quote_chain_root":              chain.resolved_root_symbol,
            "quote_root_resolution_source":  chain.root_resolution_source,
            # Phase 4.1 — target-DTE plumbing audit
            "target_dte":                    target_dte,
            "dte_mode":                      dte_mode,
            "selected_expiry":               eff_expiry,
            "expiry_selection_source":       expiry_decision.source,
            "expiry_selection_reason":       expiry_decision.reason,
            "expiry_root_symbol":            expiry_decision.root_hint,
            "expiry_days_out":               expiry_decision.days_out,
            "available_expiries_count":      len(available_expiries),
        }
        if structure.expiry and eff_expiry and eff_expiry != structure.expiry:
            snapshot_summary["expiry_override"] = {
                "from":                              structure.expiry,
                "to":                                eff_expiry,
                "source":                            expiry_decision.source,
                "reason":                            expiry_decision.reason,
                "root_hint":                         expiry_decision.root_hint,
                "structure_expiry_matches_quote_expiry": False,
            }

        if not args.dry_run:
            log_decision(output_root, decision, snapshot_summary, ts)
            log_decision_to_file(
                latest / "decision_log.jsonl", decision, snapshot_summary, ts,
            )

        log.info("strategy=%s decision=%s explanation=%s",
                 sid, decision.decision, decision.explanation)

    if not args.dry_run:
        run_path = ranked_candidates_path(output_root)
        latest_path = latest / "ranked_candidates.csv"
        fieldnames = list(ranked_rows[0].keys()) if ranked_rows else _DEFAULT_RANKED_FIELDS
        write_csv_snapshot(latest_path, ranked_rows, fieldnames)
        for row in ranked_rows:
            append_csv_row(run_path, row, fieldnames)

        log.info("wrote %d candidates to %s and %s", len(ranked_rows), run_path, latest_path)
        log.info("decision logs at %s (status=%s)",
                 decision_log_path(output_root), quote_status.connected)

    return 0


# Score components VW emits today (per src/strategies/vertical_wing/scoring.py).
# When new strategies land, their components will join this list — anything not
# listed still rides along inside `score_breakdown_json`.
_SCORE_COMPONENT_COLUMNS = [
    "credit_size",
    "credit_to_risk",
    "distance_from_spot",
    "structure_strength",
    "maxvol_alignment",
    "gamma_regime",
    "bid_ask_quality",
    "time_decay_headroom",
]

_DEFAULT_RANKED_FIELDS = [
    "ts", "strategy_id", "decision", "side", "short_strike", "long_strike",
    "credit", "spread_width", "max_risk", "reward_risk", "breakeven",
    "distance_from_spot",
    # scoring observability (Phase 2.7)
    "score", "final_score", "no_trade_threshold", "score_gap_to_threshold",
    "rejection_type", "weak_components",
    *(f"score_{c}" for c in _SCORE_COMPONENT_COLUMNS),
    "score_breakdown_json",
    # anchor observability (Phase 2.8)
    "anchor_source", "anchor_volume", "anchor_volume_source",
    "structure_strength_source",
    # filter / risk
    "rejected", "rejection_reasons",
    # dollar risk under session profile
    # `planned_loss_dollars` = planned stop risk $ under default_stop_variant
    "planned_loss_dollars", "theoretical_max_loss_dollars",
    # leg quotes
    "short_bid", "short_ask", "short_mid", "long_bid", "long_ask", "long_mid",
    "bid_ask_quality",
    # Phase 4 — quote-provider observability
    "quote_provider", "quote_timestamp", "quote_age_seconds",
    "quote_chain_root", "quote_root_resolution_source",
    "short_validation_passed", "short_rejection_reason",
    "long_validation_passed",  "long_rejection_reason",
    "quote_validation_passed", "quote_rejection_reason",
    # ── Phase 4.1 — score-edge observability ─────────────────────────────
    "score_edge", "score_edge_passed", "marginal_score",
    # Phase 4.1 — spread bid/ask/mid + width metrics
    "spread_bid", "spread_ask", "spread_mid", "spread_width_pct_of_mid",
    "worst_leg_bid_ask_abs", "worst_leg_bid_ask_pct_of_mid",
    "quote_quality_bucket", "quote_quality_reason",
    # Phase 4.1 — structured risk-rejection fields
    "risk_rejection_type",
    "planned_stop_risk_dollars", "planned_stop_risk_cap_dollars",
    "planned_stop_risk_pct", "planned_stop_risk_passed",
    "theoretical_loss_cap_dollars", "theoretical_loss_passed",
    "risk_rejection_reason",
    # Phase 4.1 — selector readiness flags
    "candidate_passes_score_threshold", "candidate_passes_score_edge",
    "candidate_passes_trade_filters", "candidate_passes_risk_filters",
    "candidate_passes_quote_filters", "candidate_is_marginal",
    "selector_eligible_base", "selector_blockers", "selector_readiness_note",
    # Phase 4.1 — target-DTE plumbing
    "target_dte", "selected_expiry", "candidate_dte", "expiry_selection_reason",
]  # used only when no candidate rows exist — keep in sync with _candidate_row()


def _candidate_row(
    strategy_id: str, c, session, ts: datetime, decision_str: str,
    chain=None,                                  # type: ignore[no-untyped-def]
    *,
    target_dte: int = 0,
    available_expiries: list[str] | None = None,
    expiry_selection_reason: str | None = None,
) -> dict:
    import json as _json
    import os as _os
    from datetime import datetime as _dt

    from src.risk.limits import planned_loss_dollars, theoretical_max_loss_dollars
    from src.selector.readiness import compute_readiness

    short = c.meta.get("short_leg") or {}
    long_ = c.meta.get("long_leg")  or {}
    breakdown = c.score_breakdown or {}

    # ── Phase 4 quote-provider observability ─────────────────────────────
    short_passed = short.get("validation_passed")
    long_passed  = long_.get("validation_passed")
    short_reason = short.get("validation_rejection_reason")
    long_reason  = long_.get("validation_rejection_reason")

    # Overall = True ONLY when BOTH legs are explicitly True. None on either
    # side (= "not validated", as with mock) leaves overall = None so the
    # CSV can't be misread as "passed".
    if short_passed is None and long_passed is None:
        overall_passed = None
    elif short_passed is True and long_passed is True:
        overall_passed = True
    else:
        overall_passed = False
    combined_reason = "; ".join(r for r in (short_reason, long_reason) if r) or None

    # Per-leg quote_time → age vs `ts` (in ET). Pick the OLDEST of the two
    # so a stale leg can't hide behind a fresh one.
    quote_age_seconds: float | None = None
    for leg in (short, long_):
        qt_iso = leg.get("quote_time")
        if not qt_iso:
            continue
        try:
            qt = _dt.fromisoformat(qt_iso)
        except ValueError:
            continue
        if qt.tzinfo is None or ts.tzinfo is None:
            continue
        age = (ts - qt).total_seconds()
        if quote_age_seconds is None or age > quote_age_seconds:
            quote_age_seconds = age

    row: dict = {
        "ts": ts.isoformat(),
        "strategy_id": strategy_id,
        "decision": decision_str,
        "side": c.side,
        "short_strike": c.short_strike,
        "long_strike": c.long_strike,
        "credit": round(c.credit, 4),
        "spread_width": round(c.max_risk + c.credit, 4),
        "max_risk": round(c.max_risk, 4),
        "reward_risk": round(c.reward_risk, 4),
        "breakeven": round(c.breakeven, 4),
        "distance_from_spot": round(c.distance_from_spot, 4),
        # ── scoring observability (Phase 2.7) ──
        "score":              round(c.score, 4),
        "final_score":        round(breakdown.get("final_score", c.score), 4),
        "no_trade_threshold": c.score_threshold,
        "score_gap_to_threshold": (
            round(c.score_gap_to_threshold, 4)
            if c.score_gap_to_threshold is not None else None
        ),
        "rejection_type":  c.rejection_type,
        "weak_components": "; ".join(c.weak_components),
    }
    for k in _SCORE_COMPONENT_COLUMNS:
        v = breakdown.get(k)
        row[f"score_{k}"] = round(v, 4) if isinstance(v, (int, float)) else None
    row["score_breakdown_json"] = _json.dumps(breakdown, default=float)

    row.update({
        # Phase 2.8 — anchor observability
        "anchor_source":             c.meta.get("anchor_source"),
        "anchor_volume":             c.meta.get("anchor_volume"),
        "anchor_volume_source":      c.meta.get("anchor_volume_source"),
        "structure_strength_source": c.meta.get("structure_strength_source"),
        "rejected":          c.rejected,
        "rejection_reasons": "; ".join(c.rejection_reasons),
        "planned_loss_dollars": round(
            planned_loss_dollars(
                c.credit, c.max_risk, session.default_stop_variant, session.contracts_per_trade,
            ), 2),
        "theoretical_max_loss_dollars": round(
            theoretical_max_loss_dollars(c.max_risk, session.contracts_per_trade), 2),
        "short_bid": short.get("bid"), "short_ask": short.get("ask"), "short_mid": short.get("mid"),
        "long_bid":  long_.get("bid"), "long_ask":  long_.get("ask"), "long_mid":  long_.get("mid"),
        "bid_ask_quality": round(c.meta.get("bid_ask_quality", 0.0), 3),
        # ── Phase 4 — quote-provider observability ───────────────────────
        "quote_provider":               (chain.provider_name if chain else None),
        "quote_timestamp":              (chain.quote_ts.isoformat() if chain else None),
        "quote_age_seconds":            (round(quote_age_seconds, 2)
                                          if quote_age_seconds is not None else None),
        "quote_chain_root":             (chain.resolved_root_symbol if chain else None),
        "quote_root_resolution_source": (chain.root_resolution_source if chain else None),
        "short_validation_passed":      short_passed,
        "short_rejection_reason":       short_reason,
        "long_validation_passed":       long_passed,
        "long_rejection_reason":        long_reason,
        "quote_validation_passed":      overall_passed,
        "quote_rejection_reason":       combined_reason,
    })

    # ── Phase 4.1 — score-edge + spread + risk-rejection + readiness ──
    try:
        min_score_edge = float(_os.getenv("MIN_SCORE_EDGE", "0.02"))
    except (TypeError, ValueError):
        min_score_edge = 0.02
    row["score_edge"] = (
        round(c.score_edge, 4) if isinstance(c.score_edge, (int, float)) else None
    )
    row["score_edge_passed"] = c.score_edge_passed
    row["marginal_score"]    = c.marginal_score
    # Spread bid/ask/mid + width metrics
    sb = c.meta.get("spread_bid")
    sa = c.meta.get("spread_ask")
    sm = c.meta.get("spread_mid")
    swp = c.meta.get("spread_width_pct_of_mid")
    wla = c.meta.get("worst_leg_bid_ask_abs")
    wlp = c.meta.get("worst_leg_bid_ask_pct_of_mid")
    row["spread_bid"]                = (round(sb, 4) if isinstance(sb, (int, float)) else None)
    row["spread_ask"]                = (round(sa, 4) if isinstance(sa, (int, float)) else None)
    row["spread_mid"]                = (round(sm, 4) if isinstance(sm, (int, float)) else None)
    row["spread_width_pct_of_mid"]   = (round(swp, 4) if isinstance(swp, (int, float)) else None)
    row["worst_leg_bid_ask_abs"]     = (round(wla, 4) if isinstance(wla, (int, float)) else None)
    row["worst_leg_bid_ask_pct_of_mid"] = (round(wlp, 4) if isinstance(wlp, (int, float)) else None)
    # Selector readiness (also stamps quote_quality_bucket / risk_rejection_type)
    readiness = compute_readiness(
        c,
        session=session,
        threshold=(c.score_threshold or 0.60),
        min_score_edge=min_score_edge,
        target_dte=target_dte,
        available_expiries=available_expiries,
        today_et=ts.date(),
        expiry_selection_reason=expiry_selection_reason,
    )
    # Stamp readiness onto candidate.meta too, so the Streamlit per-candidate
    # expander can read the same values without re-deriving.
    c.meta["_readiness"] = dict(readiness)
    row["quote_quality_bucket"]        = readiness["quote_quality_bucket"]
    row["quote_quality_reason"]        = readiness["quote_quality_reason"]
    row["risk_rejection_type"]         = readiness["risk_rejection_type"]
    psr = c.meta.get("planned_stop_risk_dollars")
    psrc = c.meta.get("planned_stop_risk_cap_dollars")
    psr_passed = c.meta.get("planned_stop_risk_passed")
    tld = c.meta.get("theoretical_loss_cap_dollars")
    tlp = c.meta.get("theoretical_loss_passed")
    row["planned_stop_risk_dollars"]    = (round(psr, 2) if isinstance(psr, (int, float)) else None)
    row["planned_stop_risk_cap_dollars"]= (round(psrc, 2) if isinstance(psrc, (int, float)) else None)
    row["planned_stop_risk_pct"]        = (
        round(readiness["planned_stop_risk_pct"], 4)
        if readiness["planned_stop_risk_pct"] is not None else None
    )
    row["planned_stop_risk_passed"]     = psr_passed
    row["theoretical_loss_cap_dollars"] = (round(tld, 2) if isinstance(tld, (int, float)) else None)
    row["theoretical_loss_passed"]      = tlp
    row["risk_rejection_reason"]        = readiness["risk_rejection_reason"]
    # Selector flags + base
    row["candidate_passes_score_threshold"] = readiness["candidate_passes_score_threshold"]
    row["candidate_passes_score_edge"]      = readiness["candidate_passes_score_edge"]
    row["candidate_passes_trade_filters"]   = readiness["candidate_passes_trade_filters"]
    row["candidate_passes_risk_filters"]    = readiness["candidate_passes_risk_filters"]
    row["candidate_passes_quote_filters"]   = readiness["candidate_passes_quote_filters"]
    row["candidate_is_marginal"]            = readiness["candidate_is_marginal"]
    row["selector_eligible_base"]           = readiness["selector_eligible_base"]
    row["selector_blockers"]                = "; ".join(readiness["selector_blockers"])
    row["selector_readiness_note"]          = readiness["selector_readiness_note"]
    # Target-DTE plumbing
    row["target_dte"]               = readiness["target_dte"]
    row["selected_expiry"]          = readiness["selected_expiry"]
    row["candidate_dte"]            = readiness["candidate_dte"]
    row["expiry_selection_reason"]  = readiness["expiry_selection_reason"]
    return row


def _collect_required_strikes(strategies: dict, structure) -> list[float]:  # type: ignore[no-untyped-def]
    """Union of every enabled strategy's `required_quote_strikes`."""
    union: set[float] = set()
    for strat in strategies.values():
        if not hasattr(strat, "required_quote_strikes"):
            continue
        try:
            strikes = strat.required_quote_strikes(structure, strat.default_parameters or {})
        except Exception:                     # never fail the tick on a strategy bug
            continue
        for s in strikes or ():
            if s is not None:
                union.add(float(s))
    return sorted(union)


def _pick_spot_hint(structure, required_strikes: list[float]) -> tuple[float | None, str]:  # type: ignore[no-untyped-def]
    """Choose spot_hint per the documented precedence:
        structure.spot if > 0  →  structure.maxvol  →  midpoint(required)
        →  None (= mock_default).
    Returns (value, source_label).
    """
    s = getattr(structure, "spot", None)
    if isinstance(s, (int, float)) and s > 0:
        return (float(s), "structure_spot")
    mv = structure.exposures.maxvol if structure.exposures else None
    if mv is not None:
        return (float(mv), "maxvol")
    if required_strikes:
        xs = sorted(required_strikes)
        return (xs[len(xs) // 2], "structure_midpoint")
    return (None, "mock_default")


def _print_candidate_audit(row: dict, c, chain=None) -> None:  # type: ignore[no-untyped-def]
    """Phase 4.1 — per-candidate audit block to stdout. No truncation.

    Grouped: Identity / Risk / Score / Quote / Selector. ONE key=value per
    line so the operator can search visually without column truncation.
    Output is BUILT from the same row dict the CSV writer uses + the
    candidate's meta — there is no path here that leaks env vars, tokens,
    Authorization headers, or the raw HTTP response.
    """
    out = print
    side = row.get("side") or "—"
    sk = row.get("short_strike")
    lk = row.get("long_strike")
    header = f"=== {side} {sk}/{lk} === decision={row.get('decision')}  ts={row.get('ts')}"
    out(header)

    # Identity
    out("--- identity ---")
    for k in ("strategy_id", "symbol" if "symbol" in row else "side", "selected_expiry",
              "target_dte", "candidate_dte", "expiry_selection_reason",
              "quote_chain_root", "quote_root_resolution_source"):
        v = row.get(k) if k in row else getattr(c, k, None)
        out(f"  {k}={v!r}")

    # Risk
    out("--- risk ---")
    for k in ("rejected", "rejection_reasons",
              "risk_rejection_type", "risk_rejection_reason",
              "planned_loss_dollars", "planned_stop_risk_dollars",
              "planned_stop_risk_cap_dollars", "planned_stop_risk_pct",
              "planned_stop_risk_passed",
              "theoretical_max_loss_dollars", "theoretical_loss_cap_dollars",
              "theoretical_loss_passed"):
        out(f"  {k}={row.get(k)!r}")

    # Score
    out("--- score ---")
    for k in ("score", "final_score", "no_trade_threshold",
              "score_gap_to_threshold", "score_edge", "score_edge_passed",
              "marginal_score", "weak_components", "score_breakdown_json"):
        out(f"  {k}={row.get(k)!r}")

    # Quote
    out("--- quote ---")
    for k in ("quote_provider", "quote_timestamp", "quote_age_seconds",
              "bid_ask_quality", "quote_quality_bucket", "quote_quality_reason",
              "spread_bid", "spread_ask", "spread_mid",
              "spread_width", "spread_width_pct_of_mid",
              "worst_leg_bid_ask_abs", "worst_leg_bid_ask_pct_of_mid",
              "short_bid", "short_mid", "short_ask",
              "long_bid", "long_mid", "long_ask",
              "short_validation_passed", "short_rejection_reason",
              "long_validation_passed", "long_rejection_reason",
              "quote_validation_passed", "quote_rejection_reason"):
        out(f"  {k}={row.get(k)!r}")

    # Selector
    out("--- selector ---")
    for k in ("candidate_passes_score_threshold", "candidate_passes_score_edge",
              "candidate_passes_trade_filters", "candidate_passes_risk_filters",
              "candidate_passes_quote_filters", "candidate_is_marginal",
              "selector_eligible_base", "selector_blockers",
              "selector_readiness_note"):
        out(f"  {k}={row.get(k)!r}")

    out("---")


def _refine_decision_explanation(
    decision,                                  # type: ignore[no-untyped-def]
    *,
    required_strikes: list[float],
    missing_required_quote_strikes: list[float],
    structure,                                 # type: ignore[no-untyped-def]
):                                             # type: ignore[no-untyped-def]
    """When `select()` says all-rejected but actually NOTHING was generated,
    the cockpit / log should distinguish three causes:

        no_structure_anchors         — ceiling/floor both None upstream
        quote_chain_missing_legs     — anchors present but chain didn't quote them
        all_candidates_rejected      — filters dropped real candidates
    """
    if decision.decision != "NO_TRADE":
        return decision
    if decision.all_candidates:
        return decision  # something WAS generated; explanation is correct
    # No candidates generated at all — refine.
    e = structure.exposures
    has_any_anchor = any([
        e.put_ceiling_2k, e.put_ceiling_5k,
        e.call_floor_2k, e.call_floor_5k,
    ])
    if not has_any_anchor:
        decision.explanation = (
            "NO_TRADE — no structure anchors. StructureProvider returned no "
            "PUT_CEILING / CALL_FLOOR (likely an unauthenticated public-only "
            "read with the single wings.* field missing, or a stale snapshot)."
        )
    elif missing_required_quote_strikes:
        decision.explanation = (
            f"NO_TRADE — quote chain missing required structure strikes "
            f"{missing_required_quote_strikes}. "
            f"Required={required_strikes}. The mock quote chain may need a "
            f"spot_hint that aligns with live structure (see scanner logs)."
        )
    else:
        decision.explanation = (
            "NO_TRADE — anchors present and chain covers them, but the "
            "strategy still produced zero candidates (likely missing leg "
            "bid/ask; check QuoteProvider output)."
        )
    return decision


if __name__ == "__main__":
    raise SystemExit(main())
