"""QuoteProvider interface.

Single source for live spot + per-option quotes + full option chains.
Broker-specific. Phase 1: `NullQuoteProvider` + `MockQuoteProvider` only.

Concrete data models live in `src.providers.quotes.types`. They are
re-exported here so existing imports of `OptionQuote` / `SpotQuote` from
`src.providers.quotes.base` keep working.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from src.providers.quotes.types import (
    OptionChainSnapshot,
    OptionQuote,
    OptionType,
    QuoteProviderStatus,
    SpotQuote,
    SpreadQuote,
)

# Back-compat alias for the old short-form "C"/"P" string. Prefer OptionType.
Right = Literal["C", "P"]

__all__ = [
    "OptionChainSnapshot",
    "OptionQuote",
    "OptionType",
    "QuoteProvider",
    "QuoteProviderStatus",
    "Right",
    "SpotQuote",
    "SpreadQuote",
]


@runtime_checkable
class QuoteProvider(Protocol):

    name: str

    def get_spot(self, symbol: str) -> SpotQuote | None: ...

    def get_option_quote(
        self,
        symbol: str,
        expiry: str,
        strike: float,
        right: Right,
    ) -> OptionQuote | None: ...

    def get_option_chain(
        self,
        symbol: str,
        expiry: str | None = None,
    ) -> OptionChainSnapshot | None:
        """Return the full chain for `expiry`. None if not available.

        Implementations should treat `expiry=None` as "use the nearest expiry
        the provider knows about" — typically today's 0DTE for SPX.
        """
        ...

    def quote_timestamp(self) -> datetime | None:
        """Most-recent successful read across spot/option/chain. UI uses this."""
        ...

    def status(self) -> QuoteProviderStatus:
        """Health snapshot for the cockpit's provider-status panel."""
        ...
