"""Phase 11A deterministic backtest learning CLI.

Runs the existing replay path, then writes research-only feature tables,
empirical summaries, an assumption audit, and learned optimization hypotheses.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from src.backtesting.learning import (
    LearningConfig,
    research_latest_dir,
    research_run_dir,
    run_learning,
    write_learning_reports,
)
from src.backtesting.replay_runner import resolve_profiles, run_backtest


def _configure_cli_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    _configure_cli_encoding()
    parser = argparse.ArgumentParser(
        description="Deterministic backtest feature learning and hypothesis generation."
    )
    parser.add_argument("--symbol", required=True, choices=["SPX", "SPY", "QQQ"])
    parser.add_argument("--dte", required=True, type=int, choices=[0, 1])
    dates = parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--all-data", action="store_true")
    dates.add_argument("--latest-days", type=int)
    dates.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--profiles", default="all-main")
    parser.add_argument("--starting-balance", type=float, default=10000.0)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--trading-root", default=None)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    if args.start and not args.end:
        parser.error("--start requires --end")
    if args.end and not args.start:
        parser.error("--end requires --start")
    if args.output_root:
        os.environ["OUTPUT_DIR"] = args.output_root
    profiles = resolve_profiles(args.profiles)
    try:
        replay = run_backtest(
            symbol=args.symbol,
            profile_ids=profiles,
            start=args.start,
            end=args.end,
            latest_days=0 if args.all_data else (args.latest_days or 0),
            dte=args.dte,
            trading_root=args.trading_root,
            run_label=args.run_label,
            starting_balance=args.starting_balance,
            contracts=args.contracts,
        )
        learned = run_learning(
            replay,
            LearningConfig(
                symbol=args.symbol,
                dte=args.dte,
                profiles=tuple(profiles),
                run_label=args.run_label,
                starting_balance=args.starting_balance,
                contracts=args.contracts,
                date_mode=(
                    "all_data" if args.all_data else "date_range" if args.start else "latest_days"
                ),
            ),
        )
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + args.run_label
    out_dirs = [research_latest_dir(), research_run_dir(run_id)]
    write_learning_reports(learned, out_dirs)
    print("ZeroSigma Algo - backtest learning review (research only)")
    print(f"trades analyzed       : {len(learned.trade_features)}")
    print(f"candidates analyzed   : {len(learned.candidate_features)}")
    print(f"no-trade rows analyzed: {len(learned.no_trade_features)}")
    print(f"hypotheses generated  : {len(learned.hypotheses)}")
    print(f"learned parameter sets: {len(learned.learned_parameter_sets)}")
    print(f"output (latest)       : {out_dirs[0]}")
    print(f"output (run)          : {out_dirs[1]}")
    print("Research only. No live strategy behavior changed.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
