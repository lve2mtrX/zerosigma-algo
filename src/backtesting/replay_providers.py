"""Phase 10B — replay providers that wrap Phase 10A mapped snapshots.

These satisfy the SAME provider shape the live scanner expects
(``StructureProvider.get_snapshot`` / ``QuoteProvider.get_option_chain`` /
``get_spot`` / ``status``) but source their data from a SAVED raw daily file at
one selected timestamp — so the existing strategy / selector consume them
exactly like the live ``zerosigma_api`` + ``tastytrade`` providers, with NO
network, NO broker, NO live API.

Each provider is pinned to a single entry timestamp (the snapshot the runner
selected via the Phase 10A entry-window logic). The mapping itself is the
SHARED ``mappers.map_structure`` / ``mappers.map_option_chain`` — no fork.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.backtesting import mappers
from src.providers.quotes.types import (
    OptionChainSnapshot,
    QuoteProviderStatus,
    QuoteRequest,
    SpotQuote,
)
from src.providers.structure.types import StructureSnapshot

_PROVIDER_NAME = "backtest_raw"


class ReplayStructureProvider:
    """StructureProvider over a saved raw snapshot at one timestamp."""

    name = _PROVIDER_NAME

    def __init__(self, rows: list[dict], ts: datetime, symbol: str) -> None:
        self._rows = rows
        self._ts = ts
        self._symbol = (symbol or "SPX").strip().upper()

    @property
    def timestamp(self) -> datetime:
        return self._ts

    def get_snapshot(self, symbol: str | None = None) -> StructureSnapshot:
        return mappers.map_structure(self._rows, self._ts, symbol or self._symbol)


class ReplayQuoteProvider:
    """QuoteProvider over a saved raw snapshot at one timestamp (mid-to-mid)."""

    name = _PROVIDER_NAME

    def __init__(self, rows: list[dict], ts: datetime, symbol: str) -> None:
        self._rows = rows
        self._ts = ts
        self._symbol = (symbol or "SPX").strip().upper()
        self._chain: OptionChainSnapshot | None = None

    @property
    def timestamp(self) -> datetime:
        return self._ts

    def get_option_chain(
        self,
        symbol: str | None = None,
        *,
        expiry: str | None = None,
        request: QuoteRequest | None = None,
    ) -> OptionChainSnapshot:
        # `request` is accepted for interface parity; the historical chain is
        # authoritative (every strike in the file), so no synthesis hint is used.
        self._chain = mappers.map_option_chain(
            self._rows, self._ts, symbol or self._symbol, expiry=expiry
        )
        return self._chain

    def get_spot(self, symbol: str | None = None) -> SpotQuote:
        chain = self._chain or self.get_option_chain(symbol)
        return SpotQuote(
            symbol=symbol or self._symbol,
            last=chain.spot,
            bid=None,
            ask=None,
            ts=self._ts,
        )

    def status(self) -> QuoteProviderStatus:
        return QuoteProviderStatus(
            provider_name=_PROVIDER_NAME,
            connected=True,
            last_chain_ts=self._ts,
            notes="historical replay — saved raw snapshot, mid-to-mid, no broker",
        )


def providers_for_snapshot(
    rows: list[dict], ts: datetime, symbol: str,
) -> tuple[ReplayStructureProvider, ReplayQuoteProvider]:
    """Build a (structure, quote) provider pair pinned to one entry snapshot."""
    return (
        ReplayStructureProvider(rows, ts, symbol),
        ReplayQuoteProvider(rows, ts, symbol),
    )


def map_snapshot(rows: list[dict], ts: datetime, symbol: str) -> dict[str, Any]:
    """Convenience: structure + chain in one call (used by the runner)."""
    struct_p, quote_p = providers_for_snapshot(rows, ts, symbol)
    return {
        "structure": struct_p.get_snapshot(symbol),
        "chain": quote_p.get_option_chain(symbol),
    }
