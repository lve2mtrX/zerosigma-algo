"""Phase 9B — review the local paper portfolio ledger (READ-ONLY).

Inspects portfolio runs written by scripts.run_portfolio_forward. Pure read-only:
no execution, no orders, no order preview, no process management, no broker calls.

  python -m scripts.review_portfolio_forward --latest
  python -m scripts.review_portfolio_forward --list
  python -m scripts.review_portfolio_forward --run RUN_ID
  python -m scripts.review_portfolio_forward --open RUN_ID
  python -m scripts.review_portfolio_forward --closed RUN_ID
  python -m scripts.review_portfolio_forward --events RUN_ID --limit 20
  python -m scripts.review_portfolio_forward --reconcile RUN_ID

RUN_ID accepts the alias "latest". A missing run exits 1 with a helpful message.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _fmt(v) -> str:  # type: ignore[no-untyped-def]
    return "—" if v is None or v == "" else str(v)


def _print_run(run_ref: str, root) -> int:  # type: ignore[no-untyped-def]
    from src.paper import ledger
    man = ledger.load_manifest(run_ref, root)
    if man is None:
        sys.stderr.write(f"portfolio run not found: {run_ref!r}\n")
        return 1
    summ = ledger.load_summary(run_ref, root) or {}
    hb = ledger.load_heartbeat(run_ref, root) or {}
    print(f"=== portfolio run: {man.get('portfolio_run_id')} ===")
    print(f"  profiles:        {', '.join(man.get('profiles') or [])}")
    print(f"  status:          {man.get('status')}  (heartbeat: {hb.get('status')})")
    print(f"  started/ended:   {man.get('started_at')}  ->  {man.get('ended_at')}")
    print(f"  interval_seconds:{man.get('interval_seconds')}   "
          f"market_hours_only: {man.get('market_hours_only')}")
    lc = man.get("lifecycle_config") or {}
    print(f"  lifecycle:       contracts={lc.get('contracts')} "
          f"TP={lc.get('take_profit_pct')} SL={lc.get('stop_loss_pct')} "
          f"EOD={lc.get('eod_exit_time') if lc.get('exit_on_eod') else 'off'}")
    print(f"  open/closed:     open={summ.get('open_trade_count')} "
          f"closed={summ.get('closed_trade_count')}")
    print(f"  P&L:             realized={summ.get('realized_pnl')} "
          f"unrealized={summ.get('unrealized_pnl')} total={summ.get('total_pnl')}")
    print(f"  wins/losses:     {summ.get('wins')}/{summ.get('losses')} "
          f"win_rate={summ.get('win_rate')}")
    print(f"  max_open_seen:   {summ.get('max_open_trades_seen')}   "
          f"dup_skipped={summ.get('duplicate_skipped_count')}   "
          f"blocked={summ.get('blocked_by_limits_count')}")
    print(f"  no_execution:    {summ.get('no_execution', man.get('no_execution'))}")
    print("---")
    return 0


def _print_trades(rows: list[dict], title: str) -> int:
    print(f"=== {title} ({len(rows)}) ===")
    for r in rows:
        print(f"  {_fmt(r.get('paper_trade_id')):16} {_fmt(r.get('profile_id')):28} "
              f"{_fmt(r.get('side')):11} {_fmt(r.get('short_strike'))}/{_fmt(r.get('long_strike'))} "
              f"exp={_fmt(r.get('selected_expiry'))} credit={_fmt(r.get('entry_credit'))} "
              f"mark={_fmt(r.get('current_mark'))} uPnL={_fmt(r.get('unrealized_pnl'))} "
              f"rPnL={_fmt(r.get('realized_pnl'))} exit={_fmt(r.get('exit_reason'))} "
              f"ticks={_fmt(r.get('ticks_held'))}")
    print("---")
    return 0


def _print_events(rows: list[dict], limit: int) -> int:
    rows = rows[-limit:]
    print(f"=== paper_trade_events (last {len(rows)}) ===")
    for e in rows:
        print(f"  {_fmt(e.get('timestamp'))}  {_fmt(e.get('event_type')):18} "
              f"{_fmt(e.get('profile_id')):28} {_fmt(e.get('paper_trade_id')):16} "
              f"reason={_fmt(e.get('reason'))}")
    print("---")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    from src.paper import ledger

    parser = argparse.ArgumentParser(
        description="Review the local paper portfolio ledger (read-only — no execution)",
    )
    parser.add_argument("--output-dir", "--portfolio-root", dest="root", default=None,
                        help="portfolio ledger root (default outputs/portfolio_forward)")
    parser.add_argument("--limit", type=int, default=20)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--latest", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--run", metavar="RUN_ID")
    g.add_argument("--open", dest="open_run", metavar="RUN_ID")
    g.add_argument("--closed", dest="closed_run", metavar="RUN_ID")
    g.add_argument("--events", metavar="RUN_ID")
    g.add_argument("--reconcile", metavar="RUN_ID")
    args = parser.parse_args(argv)
    root = args.root

    if args.list:
        summaries = ledger.list_portfolio_run_summaries(limit=args.limit, root=root)
        print(f"=== portfolio runs ({len(summaries)}) ===")
        print(f"  {'portfolio_run_id':32} {'status':10} {'open':>4} {'closed':>6} {'total_pnl':>10}")
        for s in summaries:
            print(f"  {_fmt(s['portfolio_run_id']):32} {_fmt(s['status']):10} "
                  f"{_fmt(s['open']):>4} {_fmt(s['closed']):>6} {_fmt(s['total_pnl']):>10}")
        print("---")
        return 0

    if args.latest:
        return _print_run("latest", root)
    if args.run:
        return _print_run(args.run, root)

    if args.open_run:
        if ledger.resolve_portfolio_run_dir(args.open_run, root) is None:
            sys.stderr.write(f"portfolio run not found: {args.open_run!r}\n")
            return 1
        return _print_trades(ledger.load_open_trades(args.open_run, root), "open paper trades")
    if args.closed_run:
        if ledger.resolve_portfolio_run_dir(args.closed_run, root) is None:
            sys.stderr.write(f"portfolio run not found: {args.closed_run!r}\n")
            return 1
        return _print_trades(ledger.load_closed_trades(args.closed_run, root), "closed paper trades")
    if args.events:
        if ledger.resolve_portfolio_run_dir(args.events, root) is None:
            sys.stderr.write(f"portfolio run not found: {args.events!r}\n")
            return 1
        return _print_events(ledger.load_events(args.events, root), args.limit)
    if args.reconcile:
        report = ledger.reconcile_run(args.reconcile, root=root)
        if report is None:
            sys.stderr.write(f"portfolio run not found: {args.reconcile!r}\n")
            return 1
        print(f"=== reconciliation: {report['portfolio_run_id']} ===")
        print(f"  ok:              {report['ok']}")
        print(f"  mode:            {report['reconciliation_mode']}")
        print(f"  open/closed/evt: {report['open_count']}/{report['closed_count']}/{report['event_count']}")
        print(f"  broker_position_reconciliation: {report['broker_position_reconciliation']}")
        if report["issues"]:
            print(f"  issues ({len(report['issues'])}):")
            for iss in report["issues"]:
                print(f"    - {iss}")
        else:
            print("  issues:          none")
        print("---")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
