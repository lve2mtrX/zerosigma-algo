"""Quote-side data models.

These are the broker-style pricing data. Strategies consume these alongside
`StructureSnapshot` (from the StructureProvider) but the two shapes are
deliberately independent — see plan.md §6 "Providers".

Naming convention:
  - `OptionQuote`        : one strike + side
  - `OptionChainSnapshot`: a full chain for one expiry at one timestamp
  - `SpotQuote`          : underlying spot tick
  - `SpreadQuote`        : two-leg spread synthesized from individual legs
  - `QuoteProviderStatus`: light health/heartbeat for the UI
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class OptionType(StrEnum):
    CALL = "CALL"
    PUT  = "PUT"


@dataclass(frozen=True)
class SpotQuote:
    symbol: str
    last: float | None
    bid:  float | None
    ask:  float | None
    ts:   datetime


@dataclass(frozen=True)
class OptionQuote:
    """One strike, one side. Volume + OI are CURRENT (intraday) — not historical.

    Greek fields are optional because some brokers don't expose them in their
    quote feed. When None, downstream code must either skip those scoring
    sub-features or compute Greeks via a model.
    """
    underlying:      str            # e.g. "SPX"
    expiry:          str            # "YYYY-MM-DD"
    option_type:     OptionType
    strike:          float
    bid:             float | None
    ask:             float | None
    mid:             float | None
    volume:          float | None
    open_interest:   float | None
    quote_time:      datetime
    vendor_symbol:   str | None = None    # e.g. ".SPXW260601C5815"
    # Optional Greeks
    iv:    float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega:  float | None = None
    theta: float | None = None

    @property
    def bid_ask_spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


@dataclass(frozen=True)
class OptionChainSnapshot:
    """A whole chain for one expiry at one timestamp."""
    underlying:    str
    spot:          float
    expiry:        str
    quotes:        list[OptionQuote]
    quote_ts:      datetime
    provider_name: str

    def find(self, strike: float, option_type: OptionType) -> OptionQuote | None:
        for q in self.quotes:
            if q.strike == strike and q.option_type == option_type:
                return q
        return None

    def strikes(self) -> list[float]:
        return sorted({q.strike for q in self.quotes})


@dataclass(frozen=True)
class SpreadQuote:
    """Two-leg spread synthesized from individual leg quotes.

    For credit spreads (selling premium): SHORT leg is the higher-priced leg.
    `credit_mid`  = short.mid - long.mid          (mid-to-mid theoretical credit)
    `credit_bid`  = short.bid - long.ask          (worst-case fill — natural)
    `credit_ask`  = short.ask - long.bid          (best-case fill — through-the-book)
    """
    short_leg:  OptionQuote
    long_leg:   OptionQuote
    width:      float
    credit_mid: float | None
    credit_bid: float | None
    credit_ask: float | None

    @classmethod
    def from_legs(cls, short_leg: OptionQuote, long_leg: OptionQuote) -> SpreadQuote:
        width = abs(long_leg.strike - short_leg.strike)
        c_mid = (
            short_leg.mid - long_leg.mid
            if short_leg.mid is not None and long_leg.mid is not None else None
        )
        c_bid = (
            short_leg.bid - long_leg.ask
            if short_leg.bid is not None and long_leg.ask is not None else None
        )
        c_ask = (
            short_leg.ask - long_leg.bid
            if short_leg.ask is not None and long_leg.bid is not None else None
        )
        return cls(
            short_leg=short_leg, long_leg=long_leg,
            width=width, credit_mid=c_mid, credit_bid=c_bid, credit_ask=c_ask,
        )


@dataclass
class QuoteProviderStatus:
    """Light health-check for the UI; not used by the scanner."""
    provider_name:   str
    connected:       bool
    last_spot_ts:    datetime | None = None
    last_chain_ts:   datetime | None = None
    last_error:      str | None = None
    notes:           str | None = None
