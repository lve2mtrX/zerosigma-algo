"""Backend protocol shared by local and opt-in notification adapters."""

from __future__ import annotations

from typing import Protocol

from src.alerts.types import AlertDeliveryResult, AlertEvent


class NotificationBackend(Protocol):
    name: str
    enabled: bool

    def send(self, alert_event: AlertEvent) -> AlertDeliveryResult: ...
