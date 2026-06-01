"""Manual tracker execution — user reports their own fills."""

from __future__ import annotations

from src.providers.execution.base import ExecutionMode, FillReport, OrderTicket
from src.utils.time import now_et


class ManualTrackerExecutionProvider:
    """Records what the user reports as a real fill done outside the cockpit."""

    mode = ExecutionMode.MANUAL_TRADE_TRACKING

    def __init__(self, **_: object) -> None:
        pass

    def submit(self, ticket: OrderTicket) -> FillReport | None:
        # The cockpit's UI is the source of truth here; the executor just
        # echoes back the ticket as a "filled" record at the credit provided.
        return FillReport(
            ticket=ticket,
            filled_credit=ticket.credit,
            fill_ts=now_et(),
            fill_source="manual",
            order_id=None,
        )

    def is_active(self) -> bool:
        return True
