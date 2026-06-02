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

    Validation fields (Phase 4) are set by broker providers that run their
    own quality checks (e.g. TastytradeQuoteProvider). Mock/null providers
    leave them as None — meaning "not validated", NOT "passed validation".
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
    # Phase 4 — broker-side validation result. None means "not validated".
    validation_passed:           bool | None = None
    validation_rejection_reason: str | None = None

    @property
    def bid_ask_spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


@dataclass(frozen=True)
class QuoteValidation:
    """Conservative validation thresholds for live broker quotes (Phase 4).

    Applied by the broker QuoteProvider PER QUOTE. Reasons are short
    snake_case strings so CSV/JSONL stays grep-friendly.

    Defaults match `.env.example`:
      TASTY_QUOTE_MAX_AGE_SECONDS=10
      TASTY_QUOTE_MAX_SPREAD_PCT=0.50
      TASTY_QUOTE_MAX_SPREAD_ABS=5.00
      TASTY_REJECT_ZERO_BID=true
      TASTY_REJECT_CROSSED_MARKET=true
    """
    max_age_seconds:       float = 10.0
    max_spread_pct:        float = 0.50
    max_spread_abs:        float = 5.00
    reject_zero_bid:       bool  = True
    reject_crossed_market: bool  = True

    def validate(
        self,
        quote: OptionQuote,
        *,
        now: datetime | None = None,
    ) -> tuple[bool, str | None]:
        """Returns (passed, rejection_reason)."""
        if quote.bid is None or quote.ask is None:
            return False, "missing_bid_or_ask"
        if self.reject_crossed_market and quote.ask < quote.bid:
            return False, f"crossed_market(bid={quote.bid:.2f},ask={quote.ask:.2f})"
        if self.reject_zero_bid and quote.bid <= 0:
            return False, "zero_bid"
        spread = quote.ask - quote.bid
        if spread > self.max_spread_abs:
            return False, f"spread_abs({spread:.2f}>{self.max_spread_abs:.2f})"
        mid = quote.mid if quote.mid is not None else (quote.bid + quote.ask) / 2.0
        if mid > 0 and (spread / mid) > self.max_spread_pct:
            return False, f"spread_pct({spread/mid:.2%}>{self.max_spread_pct:.0%})"
        if now is not None and quote.quote_time is not None and self.max_age_seconds > 0:
            try:
                age = (now - quote.quote_time).total_seconds()
                if age > self.max_age_seconds:
                    return False, f"stale(age={age:.1f}s>{self.max_age_seconds:.0f}s)"
            except (TypeError, ValueError):
                pass     # mismatched tz → skip age check
        return True, None


@dataclass(frozen=True)
class OptionChainSnapshot:
    """A whole chain for one expiry at one timestamp.

    Phase 4 added optional `resolved_root_symbol` + `root_resolution_source`
    fields so broker providers (e.g. Tastytrade) can record SPX→SPXW auto-
    resolution in the chain itself. Mock/null providers leave them None.
    """
    underlying:    str
    spot:          float
    expiry:        str
    quotes:        list[OptionQuote]
    quote_ts:      datetime
    provider_name: str
    # Phase 4 — broker provider may report which OPRA root it resolved to
    resolved_root_symbol:   str | None = None
    root_resolution_source: str | None = None    # explicit|auto_chain|direct_match|unresolved

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


@dataclass(frozen=True)
class QuoteRequest:
    """Optional hint passed to `QuoteProvider.get_option_chain(...)`.

    Real broker providers can ignore this — they have authoritative chain
    data already. Synthesis providers (the Phase 1.5 mock) USE it to align
    their generated chain with the structure provider's anchor strikes —
    otherwise the mock chain would always center on 5800 while live SPX
    structure levels could sit at 7580, leaving no overlapping strikes
    for the strategy to build candidates from.

    All fields are optional. Sensible behavior:
      - `spot_hint`: re-center synthesized prices around this value.
      - `required_strikes`: ensure each of these strikes appears in the
        returned chain with both call and put quotes.
      - `strike_min` / `strike_max`: bound the chain.
    """
    symbol:           str
    expiry:           str | None = None
    spot_hint:        float | None = None
    required_strikes: tuple[float, ...] = ()
    strike_min:       float | None = None
    strike_max:       float | None = None
    # Where `spot_hint` came from — for decision-log audit only.
    # Values: "structure_spot" | "maxvol" | "structure_midpoint" | "mock_default"
    spot_hint_source: str | None = None
