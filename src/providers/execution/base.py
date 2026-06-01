"""ExecutionProvider interface + mode catalog.

Modes:
  - disabled              — reject everything
  - local_paper           — simulate locally
  - manual_trade_tracking — record what user reports
  - broker_paper          — broker sandbox (future)
  - manual_confirm        — print ticket, user fills in broker (future)
  - live_tiny             — real money, hard-capped contracts (future)
  - live                  — real money, no cap (future)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable


class ExecutionMode(StrEnum):
    DISABLED              = "disabled"
    LOCAL_PAPER           = "local_paper"
    MANUAL_TRADE_TRACKING = "manual_trade_tracking"
    BROKER_PAPER          = "broker_paper"
    MANUAL_CONFIRM        = "manual_confirm"
    LIVE_TINY             = "live_tiny"
    LIVE                  = "live"


Side = Literal["CALL_CREDIT", "PUT_CREDIT"]


@dataclass(frozen=True)
class OrderTicket:
    strategy_id: str
    side: Side
    symbol: str
    expiry: str
    short_strike: float
    long_strike: float
    contracts: int
    credit: float           # per-contract credit
    stop_variant: str
    notes: str | None = None


@dataclass(frozen=True)
class FillReport:
    ticket: OrderTicket
    filled_credit: float
    fill_ts: datetime
    fill_source: str        # "paper" | "manual" | "broker"
    order_id: str | None = None


@runtime_checkable
class ExecutionProvider(Protocol):

    mode: ExecutionMode

    def submit(self, ticket: OrderTicket) -> FillReport | None:
        """Submit (or simulate, or queue) an order.

        Returns the FillReport on success, or None if the provider refused
        (e.g. mode = disabled, or risk gate rejected upstream).
        """
        ...

    def is_active(self) -> bool:
        """True if this provider can actually accept orders right now."""
        ...
