"""One-shot scanner pass.

Phase 1: runs a single tick — load config, fetch a structure snapshot (stub),
ask each enabled strategy for candidates, apply risk filters, score, select,
log the decision. Then exits.

Phase 3+: add a `--loop` flag that re-runs on the scanner cadence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src` importable when running directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="ZerσSigma algo scanner — one-shot tick")
    parser.add_argument("--profile", default="default", help="risk profile name (config/risk_profiles.yaml)")
    parser.add_argument("--strategy", default=None, help="restrict to one strategy_id (default: all enabled)")
    parser.add_argument("--dry-run", action="store_true", help="do not write decision_log or paper trades")
    args = parser.parse_args()

    # Lazy imports so --help works without optional deps installed
    from src.utils.config import load_config
    from src.utils.logging import get_logger

    log = get_logger("scanner")
    cfg = load_config(REPO_ROOT)

    log.info("Loaded config: %d strategies registered", len(cfg.strategies))
    log.info("Risk profile: %s", args.profile)
    log.info("Provider modes: structure=%s quotes=%s execution=%s",
             cfg.providers.structure_active,
             cfg.providers.quotes_active,
             cfg.providers.execution_active)

    # TODO Phase 1+: wire StructureProvider → Strategy.generate_candidates →
    # risk.filter → Strategy.score → Strategy.select → decision_log + paper.
    log.warning("scan loop not yet implemented — Phase 1 scaffold only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
