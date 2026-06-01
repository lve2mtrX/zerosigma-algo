"""Phase 1 quote provider — no broker connected.

Returns None for everything, forcing the cockpit into manual-mark mode.
"""

from __future__ import annotations

from datetime import datetime

from src.providers.quotes.base import Right
from src.providers.quotes.types import (
    OptionChainSnapshot,
    OptionQuote,
    QuoteProviderStatus,
    SpotQuote,
)


class NullQuoteProvider:
    name = "null"

    def __init__(self, **_: object) -> None:
        pass

    def get_spot(self, symbol: str) -> SpotQuote | None:
        return None

    def get_option_quote(
        self,
        symbol: str,
        expiry: str,
        strike: float,
        right: Right,
    ) -> OptionQuote | None:
        return None

    def get_option_chain(
        self,
        symbol: str,
        expiry: str | None = None,
    ) -> OptionChainSnapshot | None:
        return None

    def quote_timestamp(self) -> datetime | None:
        return None

    def status(self) -> QuoteProviderStatus:
        return QuoteProviderStatus(
            provider_name=self.name,
            connected=False,
            notes="no broker connected — manual-mark mode",
        )
