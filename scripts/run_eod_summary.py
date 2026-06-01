"""Generate the end-of-day summary from today's outputs/.

Reads:
  outputs/runs/{date}/decision_log.jsonl
  outputs/runs/{date}/manual_trades.csv
  outputs/runs/{date}/paper_trades.csv
  outputs/runs/{date}/paper_equity_curve.csv

Writes:
  outputs/daily/{date}/eod_summary.md
  outputs/daily/{date}/eod_summary.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="ZerσSigma algo EOD summary generator")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="date in YYYY-MM-DD (defaults to today, ET)",
    )
    args = parser.parse_args()

    from src.reporting.eod import generate_eod_summary
    from src.utils.logging import get_logger

    log = get_logger("eod")
    out = generate_eod_summary(REPO_ROOT, args.date)
    log.info("EOD summary written: %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
