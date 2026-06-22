"""Write the deterministic Phase 11B cross-grid smoke summary."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.backtesting.phase11b_review import latest_run_for_label, write_phase11b_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize Phase 11B bounded research grids.")
    parser.add_argument("--expansion-run", type=Path)
    parser.add_argument("--robustness-run", type=Path)
    parser.add_argument("--dynamic-run", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    runs = {
        "call_only_expansion": args.expansion_run or latest_run_for_label("call_only_expansion_spx"),
        "call_only_robustness": args.robustness_run or latest_run_for_label("call_only_robustness_spx"),
        "dynamic_repair": args.dynamic_run or latest_run_for_label("dynamic_repair_spx"),
    }
    output = write_phase11b_review(runs, output_dir=args.output_dir)
    print(f"Phase 11B smoke summary written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
