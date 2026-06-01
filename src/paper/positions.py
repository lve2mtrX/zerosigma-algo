"""Paper / manual position dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Side = Literal["CALL_CREDIT", "PUT_CREDIT"]
Source = Literal["paper", "manual"]


@dataclass
class PaperPosition:
    position_id: str
    strategy_id: str
    side: Side
    symbol: str
    expiry: str
    short_strike: float
    long_strike: float
    credit: float                # per-contract, dollars
    contracts: int
    entry_time: datetime
    entry_spot: float | None
    stop_variant: str
    profit_targets: list[float] = field(default_factory=list)
    source: Source = "paper"
    notes: str | None = None

    # mutable mid-trade state
    current_mark: float | None = None    # current debit-to-close (per contract)
    unrealized_pnl: float = 0.0
    high_water_pnl: float = 0.0          # max favorable
    low_water_pnl: float = 0.0           # max adverse (MAE)

    # exit
    exit_time: datetime | None = None
    exit_debit: float | None = None
    exit_reason: str | None = None       # "stop" | "target" | "cash_settle" | "manual"
    realized_pnl: float | None = None

    def is_open(self) -> bool:
        return self.exit_time is None
