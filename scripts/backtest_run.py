"""Phase 10B — historical replay runner CLI.

Runs ZerσSigma Algo run-profiles across local raw snapshot dates and writes
trade-level + summary reports under repo-local ``outputs/backtests/``.

READ-ONLY: no broker, no order preview, no Tastytrade, no ZerσSigma live API.
Paths are HOME/env-derived (no hardcoded username).

Usage:
    python -m scripts.backtest_run --symbol SPX --profile morning_5k_dynamic_tp75 \
        --start 2026-01-01 --end 2026-06-03 --dte 0 --run-label test
    python -m scripts.backtest_run --symbol SPX --profile all-main --latest-days 20 \
        --dte 0 --run-label smoke
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from src.backtesting import mappers as M
from src.backtesting import reports
from src.backtesting.replay_runner import resolve_profiles, run_backtest


def _configure_cli_encoding() -> None:
    """Keep Unicode banners printable in default Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    _configure_cli_encoding()
    ap = argparse.ArgumentParser(description="ZerσSigma Algo historical replay runner (Phase 10B).")
    ap.add_argument("--symbol", default="SPX", help="SPX / SPY / QQQ")
    ap.add_argument("--profile", default="all-main",
                    help="profile id, 'all-main' (4 primary), or 'all' (primary + controls)")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--dte", type=int, default=0, choices=[0, 1])
    ap.add_argument("--run-label", dest="run_label", default="run")
    ap.add_argument("--limit", type=int, default=0, help="max dates from the start of the range")
    ap.add_argument("--latest-days", dest="latest_days", type=int, default=0,
                    help="restrict to the most recent N dates")
    ap.add_argument("--entry", default=None, help="override entry target (e.g. 11:00 / 15:15)")
    ap.add_argument("--include-controls", dest="include_controls", action="store_true",
                    help="with --profile all-main, also run the control/regime/observe presets")
    ap.add_argument("--trading-root", dest="trading_root", default=None)
    ap.add_argument("--output-root", dest="output_root", default=None,
                    help="override the outputs root (default: repo outputs/; honors OUTPUT_DIR)")
    ap.add_argument("--starting-balance", dest="starting_balance", type=float, default=10000.0,
                    help="account starting balance for account-adjusted reports")
    ap.add_argument("--contracts", type=int, default=1,
                    help="fixed contracts/lots per selected spread")
    args = ap.parse_args(argv)

    if args.output_root:
        os.environ["OUTPUT_DIR"] = args.output_root

    symbol = (args.symbol or "SPX").strip().upper()
    profile_ids = resolve_profiles(args.profile, include_controls=args.include_controls)

    print("ZerσSigma Algo — historical replay backtest (read-only, no broker/live API)")
    print(f"symbol={symbol}  dte={args.dte}  profiles={profile_ids}  entry={args.entry or 'profile'}")
    print(f"starting_balance=${args.starting_balance:,.2f}  contracts={args.contracts}")

    try:
        result = run_backtest(
            symbol=symbol, profile_ids=profile_ids, start=args.start, end=args.end,
            dte=args.dte, entry_override=args.entry, limit=args.limit,
            latest_days=args.latest_days, trading_root=args.trading_root,
            run_label=args.run_label, starting_balance=args.starting_balance,
            contracts=args.contracts,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"  ERROR: {exc}")
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")  # local run-dir label only
    label = f"{symbol}_{args.run_label}"
    out_dirs = [M.latest_dir(), M.run_dir(stamp, label)]
    reports.write_reports(result, out_dirs, stamp=stamp)

    c = result.counters
    m = reports.metrics(
        result.trades,
        starting_balance=args.starting_balance,
        contracts=args.contracts,
    )
    print(f"  files found            : {c['files_found']}")
    print(f"  dates evaluated        : {c['dates_evaluated']} / {c['dates_in_range']} in range")
    print(f"  valid entry snapshots  : {c['valid_entry_snapshots']}")
    print(f"  candidates constructed : {c['candidates']}")
    print(f"  trades selected        : {c['selected_trades']}")
    print(f"  skipped candidates     : {c['skipped_candidates']}")
    print(f"  no-trade rows          : {c['no_trade_rows']}")
    print(f"  starting balance       : ${m['starting_balance']:,.2f}")
    print(f"  ending balance         : ${m['ending_balance']:,.2f}")
    print(f"  contracts              : {m['contracts']}")
    print(f"  total P&L              : ${m['total_pnl_dollars']:,.2f}")
    print(f"  return                 : {m['return_pct']:.2f}%" if m["return_pct"] is not None
          else "  return                 : —")
    print(f"  win rate               : {m['win_rate']:.2%}" if m["win_rate"] is not None
          else "  win rate               : —")
    print(f"  TP/SL/EOD              : {m['tp_count']}/{m['sl_count']}/{m['eod_count']}")
    print(f"  max drawdown           : ${m['max_drawdown_dollars']:,.2f} / "
          f"{m['max_drawdown_pct']:.2f}% (dur {m['max_drawdown_duration_trades']} trades)")
    print(f"  output (latest)        : {out_dirs[0]}")
    print(f"  output (run)           : {out_dirs[1]}")
    print("Historical simulation only — no broker, no order preview, no execution.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
