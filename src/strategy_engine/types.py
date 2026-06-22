"""Typed, strategy-archetype-neutral candidate contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class StrategyArchetype(StrEnum):
    CALL_CREDIT_SPREAD = "CALL_CREDIT_SPREAD"
    PUT_CREDIT_SPREAD = "PUT_CREDIT_SPREAD"
    LONG_CALL = "LONG_CALL"
    LONG_PUT = "LONG_PUT"
    CALL_DEBIT_SPREAD = "CALL_DEBIT_SPREAD"
    PUT_DEBIT_SPREAD = "PUT_DEBIT_SPREAD"


class DirectionalBias(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class OptionRight(StrEnum):
    CALL = "C"
    PUT = "P"


class LegAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class EntryPriceType(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"


@dataclass(frozen=True)
class StrategyLeg:
    option_symbol: str
    strike: float
    right: OptionRight
    action: LegAction
    bid: float | None
    ask: float | None
    mid: float | None
    quantity: int = 1

    @property
    def bid_ask_width(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return round(self.ask - self.bid, 4)


@dataclass(frozen=True)
class StrategyCandidate:
    candidate_id: str
    timestamp: datetime
    symbol: str
    dte: int
    expiry: str
    archetype: StrategyArchetype
    directional_bias: DirectionalBias
    legs: tuple[StrategyLeg, ...]
    entry_price_type: EntryPriceType
    entry_credit: float | None = None
    entry_debit: float | None = None
    width: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    risk_reward: float | None = None
    credit_pct_of_width: float | None = None
    debit_at_risk: float | None = None
    contracts: int = 1
    expected_entry_window: str | None = None
    time_to_close_minutes: int | None = None
    distance_to_short_strike: float | None = None
    distance_to_target_level: float | None = None
    regime_label: str | None = None
    structure_fields: dict[str, Any] = field(default_factory=dict)
    quote_quality: str | None = None
    reason_codes: tuple[str, ...] = ()
    thesis: str = ""
    stop_loss_debit: float | None = None
    stop_loss_multiple: float | None = None
    target_move_required: float | None = None
    invalidation_level: float | None = None
    minimum_target_multiple: float | None = None

    @property
    def is_credit_spread(self) -> bool:
        return self.archetype in {
            StrategyArchetype.CALL_CREDIT_SPREAD,
            StrategyArchetype.PUT_CREDIT_SPREAD,
        }

    @property
    def is_long_premium(self) -> bool:
        return self.archetype in {
            StrategyArchetype.LONG_CALL,
            StrategyArchetype.LONG_PUT,
        }

    @property
    def quote_spread_width(self) -> float | None:
        widths = [leg.bid_ask_width for leg in self.legs if leg.bid_ask_width is not None]
        return round(sum(widths), 4) if widths else None
