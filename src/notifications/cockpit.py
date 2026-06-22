"""In-process local feed backend used by the Streamlit Alert Center."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.alerts.types import AlertDeliveryResult, AlertEvent


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CockpitNotificationBackend:
    enabled: bool = True
    payloads: list[dict[str, Any]] = field(default_factory=list)
    name: str = "cockpit"

    def send(self, alert_event: AlertEvent) -> AlertDeliveryResult:
        if not self.enabled:
            return AlertDeliveryResult(
                alert_event.event_id, self.name, False, False, "backend_disabled", None, _utc_now()
            )
        self.payloads.append(alert_event.to_dict())
        return AlertDeliveryResult(
            alert_event.event_id, self.name, True, True, "local_feed_accepted", None, _utc_now()
        )
