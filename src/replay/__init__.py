"""Phase 10 (prep) — replay / backtest scaffold.

Read-only loaders that turn SAVED ZerσSigma exposure snapshots into the SAME
`StructureSnapshot` the live provider produces, so the existing scanner /
strategy / selector / paper-lifecycle path can replay history WITHOUT a fork.

Nothing here executes, places, or previews an order. See
``docs/phase10_backtest_plan.md`` for the full plan.
"""

from src.replay.snapshot_loader import (
    DEFAULT_SNAPSHOT_ROOTS,
    discover_snapshot_files,
    load_snapshot_file,
    load_snapshot_record,
    map_payload_to_snapshot,
)

__all__ = [
    "DEFAULT_SNAPSHOT_ROOTS",
    "discover_snapshot_files",
    "load_snapshot_file",
    "load_snapshot_record",
    "map_payload_to_snapshot",
]
