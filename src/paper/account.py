"""Paper account state.

Tracks starting balance, realized P&L, open positions, equity curve.
Persistence to CSV is handled by `src/paper/manual_tracker.py` and the
scanner loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.paper.positions import PaperPosition
from src.risk.limits import realized_loss_for


@dataclass
class PaperAccount:
    starting_balance: float = 10000.0
    realized_pnl: float = 0.0
    open_positions: list[PaperPosition] = field(default_factory=list)
    closed_positions: list[PaperPosition] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.open_positions)

    @property
    def equity(self) -> float:
        return self.starting_balance + self.realized_pnl + self.unrealized_pnl

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def open_position(self, pos: PaperPosition) -> None:
        self.open_positions.append(pos)

    def update_mark(self, position_id: str, current_debit: float, ts: datetime) -> None:
        for p in self.open_positions:
            if p.position_id == position_id:
                p.current_mark = current_debit
                # per-contract P&L = credit - debit; scale by contracts × 100
                pnl = (p.credit - current_debit) * p.contracts * 100
                p.unrealized_pnl = pnl
                p.high_water_pnl = max(p.high_water_pnl, pnl)
                p.low_water_pnl = min(p.low_water_pnl, pnl)
                self._record_equity(ts)
                return

    def close_position(
        self,
        position_id: str,
        exit_debit: float,
        ts: datetime,
        reason: str,
    ) -> PaperPosition | None:
        for i, p in enumerate(self.open_positions):
            if p.position_id == position_id:
                p.exit_time = ts
                p.exit_debit = exit_debit
                p.exit_reason = reason
                p.realized_pnl = (p.credit - exit_debit) * p.contracts * 100
                self.realized_pnl += p.realized_pnl
                self.closed_positions.append(p)
                del self.open_positions[i]
                self._record_equity(ts)
                return p
        return None

    def force_stop(self, position_id: str, ts: datetime) -> PaperPosition | None:
        """Close at the stop_variant's defined loss without needing a debit mark."""
        for p in self.open_positions:
            if p.position_id == position_id:
                loss_per_contract = realized_loss_for(p.credit, p.stop_variant)  # negative
                exit_debit = p.credit - loss_per_contract  # solve: pnl = (credit - debit)
                return self.close_position(position_id, exit_debit, ts, reason="stop")
        return None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _record_equity(self, ts: datetime) -> None:
        self.equity_curve.append((ts, self.equity))
