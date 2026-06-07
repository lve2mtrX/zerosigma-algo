"""Phase 10H repeatable optimization robustness review CLI."""

from __future__ import annotations

import argparse
import sys

from src.backtesting.robustness_review import (
    build_robustness_review,
    robustness_latest_dir,
    robustness_run_dir,
    write_robustness_review,
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
        description="Review optimization split robustness and benchmark a candidate."
    )
    parser.add_argument("--optimization-run-dirs", nargs="+", required=True)
    parser.add_argument("--expanded-run-dirs", nargs="*", default=[])
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--candidate-hash", default=None)
    parser.add_argument("--trading-root", default=None)
    args = parser.parse_args(argv)
    try:
        result = build_robustness_review(
            args.optimization_run_dirs,
            run_label=args.run_label,
            candidate_hash=args.candidate_hash,
            trading_root=args.trading_root,
            expanded_run_dirs=args.expanded_run_dirs,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    run_id = str(result.run_config["review_run_id"])
    out_dirs = [robustness_latest_dir(), robustness_run_dir(run_id)]
    write_robustness_review(result, out_dirs)
    recommendation = result.freeze_recommendation
    print("ZerσSigma Algo — optimization robustness review (research only)")
    print(f"reviewed split runs : {len(result.split_sensitivity_summary)}")
    print(f"candidate hash      : {recommendation['parameter_hash']}")
    print(
        f"freeze criteria     : {recommendation['passed_criteria']}/"
        f"{recommendation['total_criteria']}"
    )
    print(f"recommendation      : {recommendation['recommendation']}")
    print(f"output (latest)     : {out_dirs[0]}")
    print(f"output (run)        : {out_dirs[1]}")
    print("No profile was written by this review command.")
    print("No broker, order preview, or execution path is used.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
