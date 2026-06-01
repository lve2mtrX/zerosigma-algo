"""QuoteProvider interface.

Single source for live spot + per-option quotes. Broker-specific.
Phase 1: only `NullQuoteProvider` exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

Right = Literal["C", "P"]


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    expiry: str          # YYYY-MM-DD
    strike: float
    right: Right
    bid: float | None
    ask: float | None
    mid: float | None
    ts: datetime


@dataclass(frozen=True)
class SpotQuote:
    symbol: str
    last: float | None
    bid: float | None
    ask: float | None
    ts: datetime


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

    def quote_timestamp(self) -> datetime | None: ...
