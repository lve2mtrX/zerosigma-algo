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
    parser.add_argument("--dry-run",  action="store_true",
                        help="do not write decision_log / ranked_candidates")
    args = parser.parse_args()

    from src.app.session_state import SessionConfig
    from src.providers.quotes.mock_provider import MockQuoteProvider
    from src.providers.structure.stub import StubStructureProvider
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

    # Acquire BOTH providers — explicit separation
    structure_provider = StubStructureProvider()
    quote_provider     = MockQuoteProvider()
    structure = structure_provider.get_snapshot(symbol)
    chain     = quote_provider.get_option_chain(symbol, expiry=structure.expiry)
    if chain is None:
        log.error("QuoteProvider returned no chain for %s — aborting tick.", symbol)
        return 3
    quote_provider.get_spot(symbol)  # heartbeat
    quote_status = quote_provider.status()

    log.info(
        "scan tick: symbol=%s profile=%s strategies=%s structure=%s quotes=%s",
        symbol, profile.name, list(strategies.keys()),
        structure.source, chain.provider_name,
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


if __name__ == "__main__":
    raise SystemExit(main())
