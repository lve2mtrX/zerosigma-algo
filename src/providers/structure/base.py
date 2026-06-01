"""StructureProvider interface.

Any source of pre-computed options structure (ZS API, local cache, replay)
implements this protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.providers.structure.types import StructureSnapshot


@runtime_checkable
class StructureProvider(Protocol):
    """Returns a `StructureSnapshot` for a symbol.

    Implementations should be cheap to call (cache internally if needed).
    `is_fresh` lets the scanner decide whether to skip a tick.
    """

    name: str

    def get_snapshot(self, symbol: str) -> StructureSnapshot: ...

    def is_fresh(self, symbol: str, max_age_seconds: int) -> bool: ...

    def last_refresh_ts(self, symbol: str) -> float | None: ...
