"""Phase 10 (prep) — read-only replay snapshot loader (SCAFFOLD).

Maps SAVED ZerσSigma exposure snapshots into the SAME `StructureSnapshot` the
live provider produces, by delegating to
`ZeroSigmaApiStructureProvider.build_snapshot_from_payload` — so replay and live
share ONE mapping (no structure/strategy fork; this is the whole point of the
Phase 9H mapper extraction).

PURE: no network, no writes. This scaffold deliberately does NOT run the scanner
or the selector — it only turns a saved payload into a `StructureSnapshot` that
the EXISTING scanner path can consume. The Phase 10 adapter will feed these
snapshots through `run_scanner.main(argv)` exactly like live paper testing.

Nothing here executes, places, or previews an order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.providers.structure.types import StructureSnapshot
from src.providers.structure.zerosigma_api import ZeroSigmaApiStructureProvider

_REPO_ROOT = Path(__file__).resolve().parents[2]

# A no-network provider instance reused purely for its payload→snapshot mapping
# (auth_mode='none' + empty base_url means it never builds an HTTP client here).
_MAPPER: ZeroSigmaApiStructureProvider | None = None


def _mapper() -> ZeroSigmaApiStructureProvider:
    global _MAPPER
    if _MAPPER is None:
        _MAPPER = ZeroSigmaApiStructureProvider(base_url="", auth_mode="none")
    return _MAPPER


def map_payload_to_snapshot(
    snap_payload: dict[str, Any],
    vol_series: dict[str, Any] | None = None,
    *,
    symbol: str,
    source: str = "replay",
) -> StructureSnapshot:
    """Map a saved `/market/snapshot` payload (+ optional `/exposure/series`)
    into a `StructureSnapshot`, tagged `source='replay'`. Reuses the live
    provider mapping — including the Phase 9H 10K wings + gamma clusters."""
    return _mapper().build_snapshot_from_payload(
        snap_payload, vol_series, symbol=symbol, source=source,
    )


def load_snapshot_record(
    record: dict[str, Any], *, default_symbol: str = "SPX", source: str = "replay",
) -> StructureSnapshot:
    """Accept either a raw `/market/snapshot` payload OR a capture bundle
    ``{snapshot, exposure_series, symbol, captured_at}`` and return a snapshot."""
    if not isinstance(record, dict):
        raise ValueError("snapshot record must be a JSON object")
    if "snapshot" in record or "exposure_series" in record:
        snap = record.get("snapshot") or {}
        vol = record.get("exposure_series") or record.get("vol_series")
        symbol = record.get("symbol") or default_symbol
    else:
        snap = record
        vol = None
        symbol = record.get("symbol") or default_symbol
    return map_payload_to_snapshot(snap, vol, symbol=symbol, source=source)


def load_snapshot_file(path: str | Path) -> StructureSnapshot:
    """Read a saved snapshot JSON file → `StructureSnapshot`. Raises on bad JSON
    (callers in a batch loop should catch + skip)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return load_snapshot_record(data)


# Default directories a saved-snapshot capture might live in (read-only globs).
DEFAULT_SNAPSHOT_ROOTS: tuple[str, ...] = (
    "outputs/snapshots", "outputs/replay", "data/snapshots", "data/replay",
    "snapshots", "replay",
)


def discover_snapshot_files(
    roots: tuple[str, ...] | list[str] | None = None, *, repo_root: str | Path | None = None,
) -> list[str]:
    """Read-only: list candidate saved-snapshot JSON files under known roots.
    Returns [] when none exist (expected today — capture is a Phase 10 task)."""
    base = Path(repo_root) if repo_root else _REPO_ROOT
    roots = roots if roots is not None else DEFAULT_SNAPSHOT_ROOTS
    found: list[str] = []
    for r in roots:
        d = base / r
        if d.is_dir():
            found.extend(sorted(str(p) for p in d.glob("**/*.json")))
    return found
