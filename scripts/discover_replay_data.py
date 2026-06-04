"""Phase 10 (prep) — read-only discovery of saved exposure snapshots for replay.

Scans the known capture roots for saved ZerσSigma exposure snapshots and reports
what it finds (counts, sample paths, top-level keys). When a snapshot is found it
maps ONE through the shared loader as a smoke check. Read-only: never writes,
never hits the network, never places an order.

Usage:
    python -m scripts.discover_replay_data
    python -m scripts.discover_replay_data --root outputs/replay --root data/snapshots
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.replay import snapshot_loader as sl


def _peek_keys(path: str) -> list[str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return sorted(data.keys()) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only replay-data discovery (Phase 10 prep).")
    ap.add_argument("--root", action="append", default=None,
                    help="Override snapshot root(s) to scan (repeatable).")
    args = ap.parse_args(argv)

    roots = tuple(args.root) if args.root else sl.DEFAULT_SNAPSHOT_ROOTS
    print("ZerσSigma Algo — replay data discovery (read-only)")
    print(f"scanning roots: {', '.join(roots)}")
    files = sl.discover_snapshot_files(roots)
    print(f"saved snapshot files found: {len(files)}")

    if not files:
        print("\nNo saved exposure snapshots found yet.")
        print("Phase 10 needs a capture step that writes /market/snapshot (+ optional")
        print("/exposure/series) payloads to one of these roots, e.g.")
        print("  outputs/replay/SPX/2026-06-03T15-15-00.json")
        print("See docs/phase10_backtest_plan.md for the snapshot schema + capture plan.")
        return 0

    print("\nsample files:")
    for p in files[:10]:
        print(f"  {p}   keys={_peek_keys(p)}")

    # Smoke-map the first file through the SHARED loader (same mapping as live).
    try:
        snap = sl.load_snapshot_file(files[0])
        ex = snap.exposures
        print(f"\nmapped {files[0]} → StructureSnapshot(symbol={snap.symbol}, spot={snap.spot}, "
              f"source={snap.source})")
        print(f"  wings 2K/5K/10K put_ceiling: "
              f"{ex.put_ceiling_2k}/{ex.put_ceiling_5k}/{ex.put_ceiling_10k}")
        print(f"  gamma primary/secondary: {ex.gamma_primary}/{ex.gamma_secondary}")
    except (OSError, ValueError) as exc:
        print(f"\ncould not map {files[0]}: {exc}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
