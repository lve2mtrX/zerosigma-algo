"""Phase 10E local historical strategy-profile comparison CLI.

Research-only. Reuses the existing replay runner and never calls a broker, live
API, order preview, or execution path.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from src.backtesting.comparison import (
    comparison_latest_dir,
    comparison_run_dir,
    resolve_comparison_profiles,
    write_comparison_reports,
)
from src.backtesting.replay_runner import run_backtest


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
        description="Compare local historical backtests across strategy profiles (Phase 10E)."
    )
    parser.add_argument("--symbol", default="SPX", choices=["SPX", "SPY", "QQQ"])
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["all-main"],
        help=(
            "all-main, dynamic-only, controls-only, all, custom, or explicit "
            "space/comma-separated profile ids"
        ),
    )
    parser.add_argument("--dte", type=int, default=0, choices=[0, 1])
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--latest-days", type=int, default=0)
    parser.add_argument("--all-data", action="store_true")
    parser.add_argument("--starting-balance", type=float, default=10000.0)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--run-label", default="compare")
    parser.add_argument("--trading-root", default=None)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)

    if args.output_root:
        os.environ["OUTPUT_DIR"] = args.output_root
    if args.all_data and (args.start or args.end or args.latest_days):
        parser.error("--all-data cannot be combined with --start, --end, or --latest-days")

    profile_ids = resolve_comparison_profiles(args.profiles)
    if not profile_ids:
        print("ERROR: no profiles resolved for comparison")
        return 1

    print("ZerσSigma Algo — strategy backtest comparison (research-only, no broker/live API)")
    print(f"symbol={args.symbol}  dte={args.dte}  profiles={profile_ids}")
    print(f"starting_balance=${args.starting_balance:,.2f}  contracts={args.contracts}")
    try:
        result = run_backtest(
            symbol=args.symbol,
            profile_ids=profile_ids,
            start=None if args.all_data else args.start,
            end=None if args.all_data else args.end,
            dte=args.dte,
            latest_days=0 if args.all_data else args.latest_days,
            trading_root=args.trading_root,
            run_label=args.run_label,
            starting_balance=args.starting_balance,
            contracts=args.contracts,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dirs = [
        comparison_latest_dir(),
        comparison_run_dir(stamp, f"{args.symbol}_{args.run_label}"),
    ]
    write_comparison_reports(result, out_dirs, stamp=stamp)

    from src.backtesting.comparison import build_comparison_reports

    rankings = build_comparison_reports(result)["profile_rankings"]
    print(f"dates evaluated : {result.counters.get('dates_evaluated', 0)}")
    print(f"profiles compared: {len(profile_ids)}")
    print(f"trades selected : {result.counters.get('selected_trades', 0)}")
    for row in rankings[:5]:
        print(
            f"  #{row['rank']} {row['profile_name']}: score={row['ranking_score']:.2f}, "
            f"trades={row['total_trades']}, expectancy=${float(row['expectancy_dollars'] or 0):,.2f}, "
            f"return={float(row['return_pct'] or 0):.2f}%, "
            f"max_dd={float(row['max_drawdown_pct'] or 0):.2f}%, "
            f"status={row['promotion_status']}"
        )
    print(f"output (latest): {out_dirs[0]}")
    print(f"output (run)   : {out_dirs[1]}")
    print("Research labels only — no profile execution changes, no order preview, no execution.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
