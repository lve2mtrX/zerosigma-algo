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
    parser.add_argument("--dry-run",  action="store_true",
                        help="do not write decision_log / ranked_candidates")
    args = parser.parse_args()

    from src.app.session_state import SessionConfig
    from src.providers.quotes.mock_provider import MockQuoteProvider
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
    from src.utils.logging import get_logger
    from src.utils.time import now_et

    log = get_logger("scanner")
    cfg = load_config(REPO_ROOT)

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

    # Acquire BOTH providers — explicit separation. Structure provider can be
    # overridden by --structure-provider; quote provider is mock for Phase 2.
    structure_provider, resolved_structure_name = build_structure_provider(
        cfg, override=args.structure_provider,
    )
    quote_provider = MockQuoteProvider()
    try:
        structure = structure_provider.get_snapshot(symbol)
    except Exception as exc:
        log.error("StructureProvider %s failed: %s — falling back to stub.",
                  resolved_structure_name, type(exc).__name__)
        from src.providers.structure.stub import StubStructureProvider
        structure_provider = StubStructureProvider()
        resolved_structure_name = "stub"
        structure = structure_provider.get_snapshot(symbol)
    # Build a QuoteRequest from the structure so synthesis providers
    # (the mock) center their chain on real ZS levels rather than the
    # hardcoded 5800 default.
    required_strikes = _collect_required_strikes(strategies, structure)
    spot_hint, spot_hint_source = _pick_spot_hint(structure, required_strikes)
    quote_request = QuoteRequest(
        symbol=symbol,
        expiry=structure.expiry,
        spot_hint=spot_hint,
        required_strikes=tuple(required_strikes),
        spot_hint_source=spot_hint_source,
    )
    chain = quote_provider.get_option_chain(symbol, expiry=structure.expiry, request=quote_request)
    if chain is None:
        log.error("QuoteProvider returned no chain for %s — aborting tick.", symbol)
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
        "missing_structure_fields=%d spot_hint=%.2f source=%s required=%d missing_quote_strikes=%d",
        symbol, profile.name, list(strategies.keys()),
        structure.source, chain.provider_name, len(structure_missing),
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
            ranked_rows.append(_candidate_row(sid, c, session, ts, decision.decision))

        snapshot_summary = {
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


_DEFAULT_RANKED_FIELDS = [
    "ts", "strategy_id", "decision", "side", "short_strike", "long_strike",
    "credit", "spread_width", "max_risk", "reward_risk", "breakeven",
    "distance_from_spot", "score", "rejected", "rejection_reasons",
    "planned_loss_dollars", "theoretical_max_loss_dollars",
    "short_bid", "short_ask", "short_mid", "long_bid", "long_ask", "long_mid",
    "bid_ask_quality",
]


def _candidate_row(strategy_id: str, c, session, ts: datetime, decision_str: str) -> dict:
    from src.risk.limits import planned_loss_dollars, theoretical_max_loss_dollars

    short = c.meta.get("short_leg") or {}
    long_ = c.meta.get("long_leg")  or {}
    return {
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
        "score": round(c.score, 4),
        "rejected": c.rejected,
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
    }


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
