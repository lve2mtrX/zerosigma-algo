"""Control the local forward runner — Phase 9A (start / stop / status).

LOCAL PROCESS CONTROL ONLY — manages a background `scripts.run_forward` monitor.
NO broker execution, NO orders, NO order preview, NO secrets in output.

  python -m scripts.control_forward status
  python -m scripts.control_forward command --profile PROFILE_ID --interval-seconds 60 --market-hours-only
  python -m scripts.control_forward start   --profile PROFILE_ID --interval-seconds 60 --market-hours-only
  python -m scripts.control_forward start   --profile PROFILE_ID --once
  python -m scripts.control_forward start   --profile PROFILE_ID --max-ticks 5 --interval-seconds 60
  python -m scripts.control_forward stop [--force]
  python -m scripts.control_forward cleanup-stale

Windows PowerShell: `start` launches a DETACHED background process using the same
Python/venv as this CLI; logs go under outputs/forward/control/logs/. No admin
required. `stop` writes a graceful stop sentinel the runner polls each tick;
`stop --force` additionally terminates ONLY the PID stored in our control state.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _add_run_args(p) -> None:  # type: ignore[no-untyped-def]
    p.add_argument("--profile", required=True, help="strategy run-profile id or path")
    p.add_argument("--interval-seconds", dest="interval_seconds", type=float, default=None)
    p.add_argument("--max-ticks", dest="max_ticks", type=int, default=None)
    p.add_argument("--once", action="store_true")
    p.add_argument("--market-hours-only", dest="market_hours_only", action="store_true")
    p.add_argument("--quote-provider", dest="quote_provider", default=None,
                   choices=["mock", "null", "tastytrade"])
    p.add_argument("--structure-provider", dest="structure_provider", default=None,
                   choices=["stub", "zerosigma_api"])


def _print_status(st: dict) -> None:
    print("=== forward runner control status ===")
    for k in ("active", "status", "pid", "pid_alive", "run_id", "profile_id",
              "profile_name", "profile_hash", "started_at", "last_seen_at",
              "latest_decision", "latest_tick_time", "latest_selected_trade",
              "stop_requested", "no_execution", "execution_mode"):
        if k in st:
            print(f"  {k}={st.get(k)!r}")
    if st.get("latest_heartbeat_path"):
        print(f"  latest_heartbeat_path={st['latest_heartbeat_path']}")
    if st.get("message"):
        print(f"  note: {st['message']}")
    print("---")


def main(argv: list[str] | None = None) -> int:
    import argparse

    from src.forward import control

    parser = argparse.ArgumentParser(
        description="Control the ZerσSigma forward runner (local process control — no execution)",
    )
    parser.add_argument("--forward-root", dest="forward_root", default=None,
                        help="override outputs/forward root")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show control status")
    sub.add_parser("stop", help="graceful stop (writes stop sentinel)").add_argument(
        "--force", action="store_true", help="also terminate the stored PID if alive")
    sub.add_parser("cleanup-stale", help="remove stale pid/state files (only if not alive)")
    _add_run_args(sub.add_parser("start", help="launch a background forward runner"))
    _add_run_args(sub.add_parser("command", help="print a safe run command (does NOT launch)"))

    args = parser.parse_args(argv)
    root = args.forward_root

    if args.cmd == "status":
        _print_status(control.status(root))
        return 0

    if args.cmd == "cleanup-stale":
        ok, msg = control.cleanup_stale(root)
        print(("cleaned: " if ok else "no-op: ") + msg)
        return 0

    if args.cmd == "stop":
        ok, msg = control.stop(root, force=bool(args.force))
        print(("stop: " if ok else "stop (no-op): ") + msg)
        return 0 if ok else 1

    if args.cmd == "command":
        argvc = control.build_command(
            args.profile, interval_seconds=args.interval_seconds, once=args.once,
            max_ticks=args.max_ticks, market_hours_only=args.market_hours_only,
            quote_provider=args.quote_provider, structure_provider=args.structure_provider,
        )
        print("# Safe forward-run command (copy into a terminal — NOT launched here):")
        print(" ".join(argvc))
        return 0

    if args.cmd == "start":
        ok, msg, _pid = control.start(
            args.profile, root=root, interval_seconds=args.interval_seconds,
            once=args.once, max_ticks=args.max_ticks,
            market_hours_only=args.market_hours_only,
            quote_provider=args.quote_provider, structure_provider=args.structure_provider,
        )
        if not ok:
            sys.stderr.write(f"start failed: {msg}\n")
            return 1
        print(f"start: {msg}")
        _print_status(control.status(root))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
