"""Phase 10G deterministic strategy optimization CLI.

Research-only: generates in-memory profiles, reuses historical replay, and
writes chronological train/validation/holdout reports. No broker or execution.
"""

from __future__ import annotations

import argparse
import os
import sys

from src.backtesting.optimization import (
    GRID_SPECS,
    OptimizationConfig,
    optimization_latest_dir,
    optimization_run_dir,
    run_optimization,
    write_optimization_reports,
)


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
        description="Repeatable train/validation/holdout backtest optimization (Phase 10G)."
    )
    parser.add_argument("--symbol", required=True, choices=["SPX", "SPY", "QQQ"])
    parser.add_argument("--dte", required=True, type=int, choices=[0, 1])
    dates = parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--all-data", action="store_true")
    dates.add_argument("--latest-days", type=int)
    dates.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--starting-balance", required=True, type=float)
    parser.add_argument("--contracts", required=True, type=int)
    parser.add_argument("--grid", required=True, choices=sorted(GRID_SPECS))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--train-pct", type=int, default=60)
    parser.add_argument("--validation-pct", type=int, default=20)
    parser.add_argument("--holdout-pct", type=int, default=20)
    parser.add_argument("--train-end", default=None)
    parser.add_argument("--validation-end", default=None)
    parser.add_argument("--max-combinations", type=int, default=12)
    parser.add_argument("--profile-ids", nargs="+", default=[])
    parser.add_argument("--trading-root", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument(
        "--from-research",
        default=None,
        help="generated_strategy_hypotheses.json or containing directory for learned_hypotheses",
    )
    args = parser.parse_args(argv)
    if args.start and not args.end:
        parser.error("--start requires --end")
    if args.end and not args.start:
        parser.error("--end requires --start")
    if args.output_root:
        os.environ["OUTPUT_DIR"] = args.output_root

    config = OptimizationConfig(
        symbol=args.symbol,
        dte=args.dte,
        start=args.start,
        end=args.end,
        latest_days=args.latest_days or 0,
        all_data=args.all_data,
        starting_balance=args.starting_balance,
        contracts=args.contracts,
        grid=args.grid,
        run_label=args.run_label,
        max_combinations=args.max_combinations,
        profile_ids=tuple(args.profile_ids),
        train_pct=args.train_pct,
        validation_pct=args.validation_pct,
        holdout_pct=args.holdout_pct,
        train_end=args.train_end,
        validation_end=args.validation_end,
        trading_root=args.trading_root,
        from_research=args.from_research,
    )
    print("ZerσSigma Algo — optimization research harness (no broker/live API)")
    print(
        f"symbol={config.symbol} dte={config.dte} grid={config.grid} "
        f"max_combinations={config.max_combinations}"
    )
    print(
        f"split={config.train_pct}/{config.validation_pct}/{config.holdout_pct} "
        f"starting_balance=${config.starting_balance:,.2f} contracts={config.contracts}"
    )
    try:
        result = run_optimization(config)
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    run_id = str(result.run_config["optimizer_run_id"])
    out_dirs = [optimization_latest_dir(), optimization_run_dir(run_id)]
    write_optimization_reports(result, out_dirs)
    print(f"variants evaluated : {len(result.rankings)}")
    print(f"promotion candidates: {len(result.promotion_candidates)}")
    print(f"overfit warnings   : {len(result.overfit_warnings)}")
    for row in result.rankings[:5]:
        print(
            f"  #{row['rank']} {row['profile_id']}: score={row['robust_score']:.2f}, "
            f"validation_exp=${float(row['validation_expectancy_dollars'] or 0):,.2f}, "
            f"holdout_exp=${float(row['holdout_expectancy_dollars'] or 0):,.2f}, "
            f"status={row['promotion_status']}"
        )
    print(f"output (latest): {out_dirs[0]}")
    print(f"output (run)   : {out_dirs[1]}")
    print("Optimization is research only. It does not change live strategy behavior.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
