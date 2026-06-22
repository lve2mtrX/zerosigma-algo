"""Generate a read-only RTH local-paper soak review."""

from __future__ import annotations

import argparse
import json
import sys

from src.reviews.rth_soak import (
    collect_rth_soak_review,
    sample_fixture_review,
    write_rth_soak_review,
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
        description="Review existing RTH local-paper journals without sending alerts or orders."
    )
    parser.add_argument("--fixture", choices=("sample",), default=None)
    parser.add_argument("--run", default="latest")
    parser.add_argument("--portfolio-root", default="outputs/portfolio_forward")
    parser.add_argument("--alert-output-root", default="outputs")
    parser.add_argument("--output-dir", default="outputs/reviews")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    report = (
        sample_fixture_review()
        if args.fixture == "sample"
        else collect_rth_soak_review(
            portfolio_root=args.portfolio_root,
            alert_output_root=args.alert_output_root,
            run_ref=args.run,
        )
    )
    destination = write_rth_soak_review(report, args.output_dir)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print("RTH local-paper soak review (read-only, no secrets)")
        print(f"  run: {report.get('run_id') or 'not available'}")
        print(f"  alerts: {report['alert_summary']['alert_count']}")
        print(f"  paper trades: {report['paper_summary']['paper_trade_count']}")
        print(f"  regime transitions: {report['regime_summary']['transition_count']}")
        print(f"  Greek status: {report['greek_summary']['latest_status']}")
        print(f"  next action: {report['next_action']}")
        print(f"  artifacts: {destination.resolve()}")
    print("No alerts sent. No broker execution. No order preview.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
