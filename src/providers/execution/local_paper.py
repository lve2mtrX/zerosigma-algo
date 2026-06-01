"""Local paper execution — simulate fills, write to outputs."""

from __future__ import annotations

from src.providers.execution.base import ExecutionMode, FillReport, OrderTicket
from src.utils.time import now_et


class LocalPaperExecutionProvider:
    mode = ExecutionMode.LOCAL_PAPER

    def __init__(
        self,
        fill_at: str = "mid",
        slippage_per_leg: float = 0.02,
        commissions_per_contract: float = 0.65,
        **_: object,
    ) -> None:
        self.fill_at = fill_at
        self.slippage_per_leg = float(slippage_per_leg)
        self.commissions_per_contract = float(commissions_per_contract)

    def submit(self, ticket: OrderTicket) -> FillReport | None:
        # Phase 1: trust the ticket's credit, subtract slippage on both legs.
        slip = self.slippage_per_leg * 2
        filled = max(0.0, ticket.credit - slip)
        return FillReport(
            ticket=ticket,
            filled_credit=filled,
            fill_ts=now_et(),
            fill_source="paper",
            order_id=None,
        )

    def is_active(self) -> bool:
        return True
