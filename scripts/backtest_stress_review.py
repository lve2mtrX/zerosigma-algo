"""Phase 10I near-miss candidate stress-review CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.backtesting import robustness_review
from src.backtesting.stress_review import (
    CANDIDATE_HASH,
    build_stress_review,
    stress_latest_dir,
    stress_run_dir,
    write_stress_review,
)


def _configure_cli_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _default_optimization_run_dir() -> str:
    latest = robustness_review.robustness_latest_dir() / "run_config.json"
    if not latest.is_file():
        raise ValueError("no robustness run_config found; pass --optimization-run-dir")
    import json

    config = json.loads(latest.read_text(encoding="utf-8"))
    runs = config.get("split_sensitivity_runs") or config.get("source_optimization_runs") or []
    if not runs:
        raise ValueError("robustness config does not list optimization runs")
    return str(runs[0])


def main(argv: list[str] | None = None) -> int:
    _configure_cli_encoding()
    parser = argparse.ArgumentParser(
        description="Stress-review one near-miss optimization candidate (research only)."
    )
    parser.add_argument("--optimization-run-dir", default=None)
    parser.add_argument("--candidate-hash", default=CANDIDATE_HASH)
    parser.add_argument("--run-label", default="phase10i_stress_review")
    parser.add_argument("--trading-root", default=None)
    args = parser.parse_args(argv)
    try:
        run_dir = args.optimization_run_dir or _default_optimization_run_dir()
        result = build_stress_review(
            Path(run_dir),
            candidate_hash=args.candidate_hash,
            trading_root=args.trading_root,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    out_dirs = [stress_latest_dir(), stress_run_dir(args.run_label)]
    write_stress_review(result, out_dirs)
    print("ZerσSigma Algo — near-miss candidate stress review (research only)")
    print(f"candidate hash  : {result.recommendation.get('criteria') and args.candidate_hash}")
    print(
        "stress criteria : "
        f"{result.recommendation['passed_criteria']}/"
        f"{result.recommendation['total_criteria']}"
    )
    print(f"recommendation  : {result.recommendation['recommendation']}")
    print(f"output (latest) : {out_dirs[0]}")
    print(f"output (run)    : {out_dirs[1]}")
    print("No broker, order preview, or execution path is used.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
