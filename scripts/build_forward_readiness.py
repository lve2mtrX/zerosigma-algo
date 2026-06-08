"""Build the Phase 10I forward-paper readiness report."""

from __future__ import annotations

import json
import sys

from src.backtesting.forward_readiness import (
    build_forward_readiness,
    write_forward_readiness,
)
from src.backtesting.stress_review import stress_latest_dir


def _configure_cli_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    del argv
    _configure_cli_encoding()
    stress_dir = stress_latest_dir()
    recommendation: dict = {}
    profile: dict = {}
    snapshot_path = stress_dir / "candidate_profile_snapshot.json"
    if snapshot_path.is_file():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        profile = snapshot.get("profile") or {}
    narrative_path = stress_dir / "narrative_summary.md"
    if narrative_path.is_file():
        text = narrative_path.read_text(encoding="utf-8")
        if "```json" in text:
            recommendation = json.loads(text.split("```json", 1)[1].split("```", 1)[0])
    report = build_forward_readiness(
        stress_recommendation=recommendation,
        stress_profile=profile,
    )
    out = write_forward_readiness(report)
    print("ZerσSigma Algo — forward paper readiness report")
    print(f"candidate count : {report['candidate_count']}")
    print(f"output          : {out}")
    print("No production approval. No broker execution. No order preview.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
