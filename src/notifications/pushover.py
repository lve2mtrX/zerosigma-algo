"""Opt-in Pushover delivery with secret-safe failure reporting."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from src.alerts.types import AlertDeliveryResult, AlertEvent, AlertSeverity

PUSHOVER_ENDPOINT = "https://api.pushover.net/1/messages.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _priority(severity: AlertSeverity) -> int:
    return {
        AlertSeverity.INFO: 0,
        AlertSeverity.WATCH: 0,
        AlertSeverity.WARNING: 1,
        AlertSeverity.CRITICAL: 1,
        AlertSeverity.EMERGENCY: 2,
    }[severity]


@dataclass
class PushoverNotificationBackend:
    enabled: bool = False
    user_key: str | None = None
    api_token: str | None = None
    client: httpx.Client | None = None
    timeout_seconds: float = 10.0
    name: str = "pushover"

    @classmethod
    def from_env(cls, *, client: httpx.Client | None = None) -> PushoverNotificationBackend:
        enabled = os.environ.get("ALERTS_PUSHOVER_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        return cls(
            enabled=enabled,
            user_key=os.environ.get("PUSHOVER_USER_KEY") or None,
            api_token=os.environ.get("PUSHOVER_API_TOKEN") or None,
            client=client,
        )

    def send(self, alert_event: AlertEvent) -> AlertDeliveryResult:
        now = _utc_now()
        if not self.enabled:
            return AlertDeliveryResult(
                alert_event.event_id, self.name, False, False, "backend_disabled", None, now
            )
        if not self.user_key or not self.api_token:
            return AlertDeliveryResult(
                alert_event.event_id, self.name, False, False, "credentials_missing", None, now
            )

        priority = _priority(alert_event.severity)
        payload: dict[str, str | int] = {
            "token": self.api_token,
            "user": self.user_key,
            "title": alert_event.title,
            "message": alert_event.message,
            "priority": priority,
        }
        if priority == 2:
            payload.update({"retry": 60, "expire": 600})

        owned_client = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout_seconds)
        try:
            response = client.post(PUSHOVER_ENDPOINT, data=payload)
            response.raise_for_status()
            return AlertDeliveryResult(
                alert_event.event_id, self.name, True, True, "delivered", None, now
            )
        except Exception as exc:  # delivery must never interrupt the caller
            return AlertDeliveryResult(
                alert_event.event_id,
                self.name,
                True,
                False,
                "delivery_failed",
                type(exc).__name__,
                now,
            )
        finally:
            if owned_client:
                client.close()
