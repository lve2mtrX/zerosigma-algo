"""Append-only JSONL alert journals and tolerant readers."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.alerts.types import AlertDeliveryResult, AlertEvent


def default_alert_journal_dir(output_root: Path | str | None = None) -> Path:
    root = Path(output_root or os.environ.get("OUTPUT_DIR") or "outputs")
    return root / "alerts" / "latest"


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with source.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows


@dataclass(frozen=True)
class AlertJournal:
    directory: Path

    @classmethod
    def under_output_root(cls, output_root: Path | str | None = None) -> AlertJournal:
        return cls(default_alert_journal_dir(output_root))

    @property
    def events_path(self) -> Path:
        return self.directory / "alert_events.jsonl"

    @property
    def deliveries_path(self) -> Path:
        return self.directory / "alert_deliveries.jsonl"

    def append_event(
        self,
        event: AlertEvent,
        *,
        suppressed: bool = False,
        suppression_reason: str | None = None,
        cooldown_seconds: int = 0,
        cooldown_remaining_seconds: float = 0.0,
    ) -> None:
        row = event.to_dict()
        row.update(
            {
                "suppressed": suppressed,
                "suppression_reason": suppression_reason,
                "cooldown_seconds": cooldown_seconds,
                "cooldown_remaining_seconds": round(max(cooldown_remaining_seconds, 0.0), 3),
                "local_paper_only": bool(event.local_only),
                "no_broker_order_sent": True,
            }
        )
        _append_jsonl(self.events_path, [row])

    def append_deliveries(self, results: Iterable[AlertDeliveryResult]) -> None:
        _append_jsonl(
            self.deliveries_path,
            [
                {
                    **result.to_dict(),
                    "no_broker_order_sent": True,
                }
                for result in results
            ],
        )

    def load_events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.events_path)

    def load_deliveries(self) -> list[dict[str, Any]]:
        return read_jsonl(self.deliveries_path)


def load_latest_alerts(output_root: Path | str | None = None) -> list[dict[str, Any]]:
    return AlertJournal.under_output_root(output_root).load_events()


def load_latest_deliveries(output_root: Path | str | None = None) -> list[dict[str, Any]]:
    return AlertJournal.under_output_root(output_root).load_deliveries()
