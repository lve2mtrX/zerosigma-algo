"""Phase 10A (prep) — read-only SPX_RAW → StructureSnapshot inspection CLI.

Prints the available dates under the SPX Daily-Exposures folder and a SAMPLE
mapped structure (spot, 2K/5K/10K wings, and the Phase 9J Wing-Dominance read)
for one timestamp — using the SHARED live mapper via
`src/replay/spx_raw_loader.py`. This validates the backtest data mapping BEFORE a
full runner is built.

Paths are HOME/env-derived (no hardcoded username): `--dir` → `$ZSA_TRADING_ROOT`/
TOS Data/Daily Exposures/SPX → `~/Dropbox/Trading/TOS Data/Daily Exposures/SPX`.

Read-only: no writes, no network, no execution.

Usage:
    python -m scripts.backtest_spx_raw
    python -m scripts.backtest_spx_raw --date 2025-10-31
    python -m scripts.backtest_spx_raw --dir "D:/SPX" --date 2025-11-03 --timestamp "2025-11-03 11:10:00"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import src.app.cockpit_helpers as ch
from scripts.discover_backtest_sources import trading_root
from src.replay import spx_raw_loader as sl


def _default_spx_dir(cli_dir: str | None) -> Path:
    if cli_dir:
        return Path(cli_dir).expanduser()
    return trading_root(None) / "TOS Data" / "Daily Exposures" / "SPX"


def _print_sample(csv_path: Path, timestamp: str | None) -> None:
    tss = sl.available_timestamps(csv_path)
    print(f"  file: {csv_path.name}  ·  timestamps: {len(tss)}"
          + (f"  ({tss[0]} → {tss[-1]})" if tss else ""))
    if not tss:
        print("  (no RTH rows found)")
        return
    # Default to a MIDDAY tick — the open rarely has 10K-volume wings formed yet.
    ts = timestamp or tss[len(tss) // 2]
    snap = sl.snapshot_at(csv_path, ts)
    ex = snap.exposures
    print(f"  mapped @ {ts}: spot={snap.spot} source={snap.source}")
    print(f"    wings put_ceiling 2K/5K/10K: "
          f"{ex.put_ceiling_2k}/{ex.put_ceiling_5k}/{ex.put_ceiling_10k}")
    print(f"    wings call_floor  2K/5K/10K: "
          f"{ex.call_floor_2k}/{ex.call_floor_5k}/{ex.call_floor_10k}")
    wd = ch.wing_dominance(ex, snap.spot)
    if wd["wds_source"] == "true":
        print(f"    WDS: dominant={wd['dominant_wing_label']} @ {wd['dominant_wing_strike']} "
              f"WDS {wd['dominant_wing_wds_pct']} Tier {wd['dominant_wing_tier']}")
        print(f"         {wd['wds_reason']}")
    else:
        print(f"    WDS: unavailable — {wd['wds_reason']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only SPX_RAW → StructureSnapshot inspection (Phase 10A).")
    ap.add_argument("--dir", default=None, help="SPX Daily-Exposures dir (default: env/home-derived).")
    ap.add_argument("--date", default=None, help="Specific date (YYYY-MM-DD). Default: latest available.")
    ap.add_argument("--timestamp", default=None, help="Specific intraday timestamp to map. Default: first.")
    args = ap.parse_args(argv)

    spx_dir = _default_spx_dir(args.dir)
    print("ZerσSigma Algo — SPX_RAW backtest mapping inspection (read-only)")
    print(f"SPX dir: {spx_dir}  ({'exists' if spx_dir.is_dir() else 'NOT FOUND'})")
    dates = sl.available_dates(spx_dir)
    print(f"available dates: {len(dates)}"
          + (f"  ({dates[0]} → {dates[-1]})" if dates else ""))
    if not dates:
        print("\nNo SPX_RAW_*.csv files found. Run `python -m scripts.discover_backtest_sources` "
              "or pass --dir. See docs/phase10_backtest_plan.md.")
        return 0

    chosen = args.date or dates[-1]
    csv_path = sl.file_for_date(spx_dir, chosen)
    if csv_path is None:
        print(f"\nNo SPX_RAW file for date {chosen}. Available: {dates[-5:]}")
        return 0
    print(f"\nsample for {chosen}:")
    try:
        _print_sample(csv_path, args.timestamp)
    except (OSError, ValueError) as exc:
        print(f"  could not map {csv_path.name}: {exc}")
    print("\nLoader-only scaffold — no scanner/selector/lifecycle run yet, no execution.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
