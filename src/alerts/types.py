"""Serializable alert-domain types with no provider or UI dependencies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


class AlertAction(StrEnum):
    LOG_ONLY = "LOG_ONLY"
    COCKPIT = "COCKPIT"
    VOICE = "VOICE"
    PUSHOVER = "PUSHOVER"
    ALL = "ALL"


class AlertSource(StrEnum):
    REGIME_CHANGE = "REGIME_CHANGE"
    PAPER_ENTRY = "PAPER_ENTRY"
    PAPER_MARK = "PAPER_MARK"
    PAPER_EXIT = "PAPER_EXIT"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    RISK_QUALITY = "RISK_QUALITY"
    SYSTEM = "SYSTEM"


def deterministic_event_id(*parts: object) -> str:
    """Return a stable short ID for an alert's source event."""
    payload = json.dumps(parts, default=str, separators=(",", ":"), ensure_ascii=True)
    return f"alert_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


@dataclass(frozen=True)
class AlertEvent:
    event_id: str
    timestamp: str
    source: AlertSource
    severity: AlertSeverity
    title: str
    message: str
    symbol: str | None
    profile_id: str | None
    trade_id: str | None
    regime_label: str | None
    old_regime: str | None
    new_regime: str | None
    suggested_action: str | None
    reason_codes: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    local_only: bool = True
    no_broker_order_sent: bool = True
    delivery_action: AlertAction = AlertAction.ALL

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        if not self.reason_codes:
            raise ValueError("every alert must include at least one stable reason code")

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["source"] = self.source.value
        row["severity"] = self.severity.value
        row["reason_codes"] = list(self.reason_codes)
        row["delivery_action"] = self.delivery_action.value
        return row

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> AlertEvent:
        values = dict(row)
        values["source"] = AlertSource(values["source"])
        values["severity"] = AlertSeverity(values["severity"])
        values["reason_codes"] = tuple(values.get("reason_codes") or ())
        values["delivery_action"] = AlertAction(
            values.get("delivery_action", AlertAction.ALL.value)
        )
        return cls(**values)


@dataclass(frozen=True)
class AlertDeliveryResult:
    event_id: str
    backend: str
    attempted: bool
    delivered: bool
    reason: str
    error_type: str | None
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
