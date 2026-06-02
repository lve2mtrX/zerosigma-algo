"""Review forward runs — Phase 8 (read-only). NO execution, NO orders, NO secrets.

  python -m scripts.review_forward --latest
  python -m scripts.review_forward --list [--limit N]
  python -m scripts.review_forward --run RUN_ID
  python -m scripts.review_forward --signals RUN_ID [--limit N]
  python -m scripts.review_forward --no-trades RUN_ID [--limit N]
  python -m scripts.review_forward --ticks RUN_ID [--limit N]
  python -m scripts.review_forward --export-summary RUN_ID --output PATH

RUN_ID may be a concrete run id or the alias 'latest'. A missing run exits
non-zero with a helpful message (no traceback).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _fmt(v) -> str:  # type: ignore[no-untyped-def]
    return "—" if v is None else str(v)


def _print_summary(s: dict) -> None:
    print(f"=== forward run: {s['run_id']} ===")
    print(f"  path:            {s.get('run_path')}")
    print(f"  profile:         {_fmt(s.get('profile_id'))}  ({_fmt(s.get('profile_name'))})")
    print(f"  profile_hash:    {_fmt(s.get('profile_hash'))}")
    print(f"  status:          {_fmt(s.get('status'))}  (heartbeat: {_fmt(s.get('latest_heartbeat_status'))})")
    print(f"  started/ended:   {_fmt(s.get('started_at'))}  ->  {_fmt(s.get('ended_at'))}")
    print(f"  interval_seconds:{_fmt(s.get('interval_seconds'))}   selector: {_fmt(s.get('daily_selector'))}"
          f"   target_dte: {_fmt(s.get('target_dte'))}   quotes: {_fmt(s.get('quote_provider'))}")
    print(f"  counts:          ticks={s.get('tick_count', 0)}  signals={s.get('signal_count', 0)}"
          f"  duplicate_signals={s.get('duplicate_signal_count', 0)}"
          f"  no_trade={s.get('no_trade_count', 0)}  errors={s.get('error_count', 0)}")
    print(f"  latest:          tick_time={_fmt(s.get('latest_tick_time'))}  decision={_fmt(s.get('latest_decision'))}"
          f"  selected_trade={s.get('latest_selected_trade', False)}")
    if s.get("latest_no_trade_reason"):
        print(f"  latest_no_trade: {s['latest_no_trade_reason']}")
    sigs = s.get("selected_trade_summaries") or []
    if sigs:
        print(f"  selected_trade_summaries ({len(sigs)}):")
        for sig in sigs[:10]:
            print(f"    - tick {sig.get('tick_id')}: {sig.get('side')} "
                  f"{sig.get('short_strike')}/{sig.get('long_strike')} @ {sig.get('credit')} "
                  f"(score {sig.get('score')}, {sig.get('selected_expiry')})")
    print(f"  no_execution:    {s.get('no_execution', True)}")
    print("---")


def main(argv: list[str] | None = None) -> int:
    from src.forward import review

    parser = argparse.ArgumentParser(
        description="Review ZerσSigma forward runs (read-only — no execution)",
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--latest", action="store_true", help="summarize the most recent run")
    g.add_argument("--list", action="store_true", help="list recent runs (newest first)")
    g.add_argument("--run", metavar="RUN_ID", help="summarize a specific run (or 'latest')")
    g.add_argument("--signals", metavar="RUN_ID", help="show selected-signal rows")
    g.add_argument("--no-trades", dest="no_trades", metavar="RUN_ID", help="show no-trade reasons")
    g.add_argument("--ticks", metavar="RUN_ID", help="show tick statuses")
    g.add_argument("--export-summary", dest="export_summary", metavar="RUN_ID",
                   help="write the run summary JSON to --output")
    parser.add_argument("--output", default=None, help="output path for --export-summary")
    parser.add_argument("--limit", type=int, default=20, help="max rows for list/signals/no-trades/ticks")
    parser.add_argument("--forward-root", default=None, help="override outputs/forward root")
    args = parser.parse_args(argv)
    root = args.forward_root

    # ── --list ──
    if args.list:
        summaries = review.list_run_summaries(limit=args.limit, root=root)
        if not summaries:
            print("(no forward runs found under outputs/forward/runs)")
            return 0
        hdr = (f"{'run_id':<40} {'status':<10} {'ticks':>5} {'sig':>4} {'dup':>4} "
               f"{'noT':>4} {'err':>4}  profile")
        print(hdr)
        print("-" * len(hdr))
        for s in summaries:
            print(f"{s['run_id']:<40} {_fmt(s.get('status')):<10} "
                  f"{s.get('tick_count', 0):>5} {s.get('signal_count', 0):>4} "
                  f"{s.get('duplicate_signal_count', 0):>4} {s.get('no_trade_count', 0):>4} "
                  f"{s.get('error_count', 0):>4}  {_fmt(s.get('profile_id'))}")
        return 0

    # ── --latest / --run ──
    if args.latest or args.run:
        ref = "latest" if args.latest else args.run
        s = review.summarize_run(ref, root=root)
        if s is None:
            sys.stderr.write(f"review_forward: no forward run found for {ref!r}. "
                             f"Try --list, or run `python -m scripts.run_forward --profile <id> --once`.\n")
            return 1
        _print_summary(s)
        return 0

    # ── row views: --signals / --no-trades / --ticks ──
    if args.signals or args.no_trades or args.ticks:
        ref = args.signals or args.no_trades or args.ticks
        if review.resolve_run_dir(ref, root) is None:
            sys.stderr.write(f"review_forward: no forward run found for {ref!r}.\n")
            return 1
        if args.signals:
            rows = review.load_signal_log(ref, root)
            print(f"=== signals: {ref} ({len(rows)}) ===")
            for r in rows[-args.limit:]:
                print(f"  tick {r.get('tick_id')}: {r.get('side')} "
                      f"{r.get('short_strike')}/{r.get('long_strike')} @ {r.get('credit')} "
                      f"score={r.get('score')} expiry={r.get('selected_expiry')} "
                      f"selector={r.get('daily_selector_mode')} reason={r.get('selector_reason')}")
        elif args.no_trades:
            rows = review.load_no_trade_log(ref, root)
            print(f"=== no-trades: {ref} ({len(rows)}) ===")
            for r in rows[-args.limit:]:
                print(f"  tick {r.get('tick_id')}: reason={r.get('no_trade_reason')} "
                      f"selector={r.get('daily_selector')} blockers={r.get('selector_blockers')}")
        else:  # ticks
            rows = review.load_tick_log(ref, root)
            print(f"=== ticks: {ref} ({len(rows)}) ===")
            for r in rows[-args.limit:]:
                print(f"  tick {r.get('tick_id')}: status={r.get('status')} "
                      f"rc={r.get('scanner_return_code')} decision={r.get('post_selector_decision')} "
                      f"selected={r.get('selected_trade')} dup={r.get('duplicate_selected_signal')}")
        return 0

    # ── --export-summary ──
    if args.export_summary:
        s = review.summarize_run(args.export_summary, root=root)
        if s is None:
            sys.stderr.write(f"review_forward: no forward run found for {args.export_summary!r}.\n")
            return 1
        out = Path(args.output) if args.output else (
            review.forward_root(root) / f"summary_{s['run_id']}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2, default=str)
        print(f"wrote {out}")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
