"""Render offline notification and voice previews without delivery."""

from __future__ import annotations

import argparse
import json
import sys

from src.reviews.notification_dry_run import (
    build_notification_dry_run,
    collect_notification_events,
    write_notification_dry_run,
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
        description="Build offline push/voice previews without invoking a backend."
    )
    parser.add_argument("--fixture", choices=("sample",), default=None)
    parser.add_argument("--alert-output-root", default="outputs")
    parser.add_argument("--output-dir", default="outputs/reviews")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    events, source = collect_notification_events(
        output_root=args.alert_output_root, fixture=args.fixture
    )
    report = build_notification_dry_run(events, input_source=source)
    paths = write_notification_dry_run(report, output_root=args.output_dir)
    result = {"notification_dry_run": report, "paths": paths}
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print("Phase 11H-A notification / voice dry-run preview")
        print(f"  source: {report['input_source']}")
        print(f"  events: {report['event_count']} ({report['suppressed_event_count']} suppressed)")
        print(f"  preview rows: {report['preview_row_count']}")
        print("  sent/spoken: 0")
        print(f"  artifacts: {paths['latest_dir']}")
    print("Dry-run only. No notification backend, voice playback, broker, or order path invoked.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
