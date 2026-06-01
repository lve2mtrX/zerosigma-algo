"""One-shot scanner tick.

Phase 1: a single tick uses the stub StructureProvider, generates candidates
from every enabled strategy under the active session config, applies risk
filters, scores, and writes:

    outputs/latest/ranked_candidates.csv          (overwrites)
    outputs/latest/decision_log.jsonl             (append; truncated to today's run)
    outputs/runs/{date}/ranked_candidates.csv     (append)
    outputs/runs/{date}/decision_log.jsonl        (append)

No broker, no ZS API, no live execution.
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
    provider = StubStructureProvider()
    snap = provider.get_snapshot(symbol)

    log.info("scan tick: symbol=%s profile=%s strategies=%s",
             symbol, profile.name, list(strategies.keys()))

    output_root = cfg.output_dir
    latest = latest_dir(output_root)

    # Accumulate every candidate across every strategy so the per-tick
    # ranked_candidates CSV captures the full view.
    ranked_rows: list[dict] = []
    ts = now_et()

    for sid, strat in strategies.items():
        params = {**(strat.default_parameters or {}), **session.to_filter_params()}
        candidates = strat.generate_candidates(snap, params)
        apply_filters(candidates, session.to_filter_params())
        for c in candidates:
            strat.score(c, snap, params)
        decision = strat.select(candidates, params)

        for c in candidates:
            ranked_rows.append(_candidate_row(sid, c, session, ts, decision.decision))

        snapshot_summary = {
            "spot": snap.spot,
            "maxvol": snap.exposures.maxvol,
            "put_ceiling_2k": snap.exposures.put_ceiling_2k,
            "put_ceiling_5k": snap.exposures.put_ceiling_5k,
            "call_floor_2k":  snap.exposures.call_floor_2k,
            "call_floor_5k":  snap.exposures.call_floor_5k,
            "gamma_regime":   snap.exposures.gamma_regime,
        }

        if not args.dry_run:
            log_decision(output_root, decision, snapshot_summary, ts)
            # mirror to outputs/latest/decision_log.jsonl for the cockpit
            log_decision_to_file(
                latest / "decision_log.jsonl", decision, snapshot_summary, ts,
            )

        log.info("strategy=%s decision=%s explanation=%s",
                 sid, decision.decision, decision.explanation)

    if not args.dry_run:
        # per-day CSV (append) + latest snapshot (overwrite)
        run_path = ranked_candidates_path(output_root)
        latest_path = latest / "ranked_candidates.csv"
        fieldnames = list(ranked_rows[0].keys()) if ranked_rows else _DEFAULT_RANKED_FIELDS
        write_csv_snapshot(latest_path, ranked_rows, fieldnames)
        for row in ranked_rows:
            append_csv_row(run_path, row, fieldnames)

        log.info("wrote %d candidates to %s and %s", len(ranked_rows), run_path, latest_path)
        log.info("decision logs at %s", decision_log_path(output_root))

    return 0


_DEFAULT_RANKED_FIELDS = [
    "ts", "strategy_id", "decision", "side", "short_strike", "long_strike",
    "credit", "spread_width", "max_risk", "reward_risk", "breakeven",
    "distance_from_spot", "score", "rejected", "rejection_reasons",
    "planned_loss_dollars", "theoretical_max_loss_dollars",
]


def _candidate_row(strategy_id: str, c, session, ts: datetime, decision_str: str) -> dict:
    # local import to avoid top-level coupling; runner is a thin orchestrator
    from src.risk.limits import planned_loss_dollars, theoretical_max_loss_dollars
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
                c.credit, c.max_risk, session.default_stop_variant, session.contracts_per_trade
            ), 2),
        "theoretical_max_loss_dollars": round(
            theoretical_max_loss_dollars(c.max_risk, session.contracts_per_trade), 2),
    }


if __name__ == "__main__":
    raise SystemExit(main())
