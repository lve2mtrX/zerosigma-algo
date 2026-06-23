"""Generate offline operator status and multi-profile readiness artifacts."""

from __future__ import annotations

import argparse
import json
import sys

from src.reviews.operator_command import (
    build_operator_status,
    build_profile_readiness_matrix,
    write_operator_command_artifacts,
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
        description="Build an offline-only strategy-profile readiness matrix."
    )
    parser.add_argument("--profiles", default="", help="optional comma-separated profile IDs")
    parser.add_argument("--output-dir", default="outputs/reviews")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    selected = {value.strip() for value in args.profiles.split(",") if value.strip()} or None
    matrix = build_profile_readiness_matrix(profile_ids=selected)
    status = build_operator_status(matrix)
    paths = write_operator_command_artifacts(matrix, status, output_root=args.output_dir)
    result = {"operator_status": status, "profile_readiness_matrix": matrix, "paths": paths}
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print("Phase 11H-A offline profile readiness review")
        print(f"  profiles: {matrix['profile_count']} valid / {matrix['invalid_profile_count']} invalid")
        print(f"  primary benchmark: {matrix['benchmark_profile_ids'][0]}")
        print(f"  secondary benchmark: {matrix['benchmark_profile_ids'][1]}")
        print("  RTH evidence: none claimed")
        print(f"  artifacts: {paths['latest_dir']}")
    print("Offline review only. No broker, order preview, notification send, or promotion.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
