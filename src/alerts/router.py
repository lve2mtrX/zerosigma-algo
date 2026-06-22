"""Preference-aware alert routing with deterministic in-process cooldowns."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.alerts.journal import AlertJournal
from src.alerts.types import AlertAction, AlertDeliveryResult, AlertEvent
from src.notifications.base import NotificationBackend
from src.notifications.cockpit import CockpitNotificationBackend
from src.notifications.pushover import PushoverNotificationBackend
from src.notifications.voice import VoiceNotificationBackend


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


@dataclass(frozen=True)
class AlertPreferences:
    delivery_enabled: bool = False
    default_cooldown_seconds: int = 300

    @classmethod
    def from_env(cls) -> AlertPreferences:
        return cls(
            delivery_enabled=_env_bool("ALERTS_ENABLED", False),
            default_cooldown_seconds=_env_int("ALERTS_DEFAULT_COOLDOWN_SECONDS", 300),
        )


class AlertRouter:
    def __init__(
        self,
        *,
        backends: Iterable[NotificationBackend] = (),
        journals: Iterable[AlertJournal],
        preferences: AlertPreferences | None = None,
    ) -> None:
        self.backends = list(backends)
        self.journals = list(journals)
        self.preferences = preferences or AlertPreferences()
        self._last_event_at: dict[tuple[object, ...], datetime] = {}

    @classmethod
    def from_env(
        cls,
        *,
        output_root: Path | str | None = None,
        mirror_directories: Iterable[Path | str] = (),
    ) -> AlertRouter:
        primary = AlertJournal.under_output_root(output_root)
        journals = [primary]
        journals.extend(AlertJournal(Path(directory)) for directory in mirror_directories)
        cockpit = CockpitNotificationBackend(
            enabled=_env_bool("ALERTS_COCKPIT_ENABLED", True)
        )
        voice = VoiceNotificationBackend(
            enabled=_env_bool("ALERTS_VOICE_ENABLED", False),
            queue_path=primary.directory / "voice_queue.jsonl",
        )
        return cls(
            backends=[cockpit, PushoverNotificationBackend.from_env(), voice],
            journals=journals,
            preferences=AlertPreferences.from_env(),
        )

    @staticmethod
    def _cooldown_key(event: AlertEvent) -> tuple[object, ...]:
        return (
            event.source.value,
            event.symbol,
            event.profile_id,
            event.trade_id,
            event.title,
            event.reason_codes,
        )

    @staticmethod
    def _backend_selected(event: AlertEvent, backend: NotificationBackend) -> bool:
        action = event.delivery_action
        if action == AlertAction.LOG_ONLY:
            return False
        if action == AlertAction.ALL:
            return True
        return backend.name.lower() == action.value.lower()

    def _write_event(
        self,
        event: AlertEvent,
        *,
        suppressed: bool,
        suppression_reason: str | None,
        cooldown_remaining_seconds: float,
    ) -> None:
        for journal in self.journals:
            journal.append_event(
                event,
                suppressed=suppressed,
                suppression_reason=suppression_reason,
                cooldown_seconds=self.preferences.default_cooldown_seconds,
                cooldown_remaining_seconds=cooldown_remaining_seconds,
            )

    def _write_results(self, results: list[AlertDeliveryResult]) -> None:
        for journal in self.journals:
            journal.append_deliveries(results)

    def route(
        self,
        event: AlertEvent,
        *,
        now: datetime | None = None,
    ) -> list[AlertDeliveryResult]:
        routed_at = now or _utc_now()
        if routed_at.tzinfo is None:
            routed_at = routed_at.replace(tzinfo=UTC)
        key = self._cooldown_key(event)
        previous = self._last_event_at.get(key)
        remaining = 0.0
        if previous is not None:
            elapsed = max(0.0, (routed_at - previous).total_seconds())
            remaining = max(0.0, self.preferences.default_cooldown_seconds - elapsed)
        if remaining > 0:
            self._write_event(
                event,
                suppressed=True,
                suppression_reason="cooldown_active",
                cooldown_remaining_seconds=remaining,
            )
            results = [AlertDeliveryResult(
                event.event_id,
                "router",
                False,
                False,
                "suppressed_by_cooldown",
                None,
                _timestamp(routed_at),
            )]
            self._write_results(results)
            return results

        self._last_event_at[key] = routed_at
        self._write_event(
            event,
            suppressed=False,
            suppression_reason=None,
            cooldown_remaining_seconds=0.0,
        )

        if not self.preferences.delivery_enabled:
            results = [AlertDeliveryResult(
                event.event_id,
                "router",
                False,
                False,
                "delivery_disabled_alert_journal_only",
                None,
                _timestamp(routed_at),
            )]
            self._write_results(results)
            return results

        selected = [
            backend for backend in self.backends
            if backend.enabled and self._backend_selected(event, backend)
        ]
        if not selected:
            reason = (
                "log_only_requested"
                if event.delivery_action == AlertAction.LOG_ONLY
                else "no_enabled_backend"
            )
            results = [AlertDeliveryResult(
                event.event_id, "router", False, False, reason, None, _timestamp(routed_at)
            )]
            self._write_results(results)
            return results

        results: list[AlertDeliveryResult] = []
        for backend in selected:
            try:
                results.append(backend.send(event))
            except Exception as exc:
                results.append(AlertDeliveryResult(
                    event.event_id,
                    backend.name,
                    True,
                    False,
                    "backend_exception",
                    type(exc).__name__,
                    _timestamp(routed_at),
                ))
        self._write_results(results)
        return results
