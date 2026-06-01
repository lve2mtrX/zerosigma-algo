"""Execution disabled — explicit no-op."""

from __future__ import annotations

from src.providers.execution.base import ExecutionMode, FillReport, OrderTicket


class DisabledExecutionProvider:
    mode = ExecutionMode.DISABLED

    def __init__(self, **_: object) -> None:
        pass

    def submit(self, ticket: OrderTicket) -> FillReport | None:
        return None

    def is_active(self) -> bool:
        return False
