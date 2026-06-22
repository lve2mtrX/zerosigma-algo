"""Disabled-by-default local voice queue; actual TTS playback is deferred."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.alerts.types import AlertDeliveryResult, AlertEvent


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class VoiceNotificationBackend:
    enabled: bool = False
    queue_path: Path | None = None
    max_queue_size: int = 100
    queued: list[dict[str, Any]] = field(default_factory=list)
    name: str = "voice"

    def send(self, alert_event: AlertEvent) -> AlertDeliveryResult:
        now = _utc_now()
        if not self.enabled:
            return AlertDeliveryResult(
                alert_event.event_id, self.name, False, False, "backend_disabled", None, now
            )
        payload = {
            "event_id": alert_event.event_id,
            "timestamp": alert_event.timestamp,
            "severity": alert_event.severity.value,
            "title": alert_event.title,
            "message": alert_event.message,
            "local_only": True,
            "playback_status": "queued_tts_deferred",
        }
        self.queued.append(payload)
        if len(self.queued) > self.max_queue_size:
            self.queued.pop(0)
        if self.queue_path is not None:
            self.queue_path.parent.mkdir(parents=True, exist_ok=True)
            with self.queue_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return AlertDeliveryResult(
            alert_event.event_id, self.name, True, True, "queued_tts_deferred", None, now
        )
