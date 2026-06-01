"""Phase 1 quote provider — no broker connected.

Returns None for everything, forcing the cockpit into manual-mark mode.
"""

from __future__ import annotations

from datetime import datetime

from src.providers.quotes.base import OptionQuote, Right, SpotQuote


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

    def quote_timestamp(self) -> datetime | None:
        return None
