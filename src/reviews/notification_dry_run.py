"""Offline push/voice preview generation with no delivery side effects."""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.alerts.journal import AlertJournal

REPO_ROOT = Path(__file__).resolve().parents[2]

PREVIEW_FIELDS = (
    "timestamp", "event_id", "severity", "source", "symbol", "profile_id",
    "trade_id", "reason_code", "suppressed", "suppression_reason",
    "cooldown_seconds", "cooldown_status", "delivery_action", "push_route_eligible",
    "push_backend_enabled", "push_preview_title", "push_preview_message",
    "voice_route_eligible", "voice_backend_enabled", "voice_preview",
    "dry_run_sent", "failure_handling_note",
)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None or not value.strip() else _bool(value)


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _reasons(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return ["reason_unavailable"]
    return [item.strip() for item in text.replace(",", ";").split(";") if item.strip()]


def backend_state() -> dict[str, Any]:
    """Return booleans only; credential values are never inspected or returned."""
    return {
        "global_delivery_enabled": _env_bool("ALERTS_ENABLED"),
        "cockpit_enabled": _env_bool("ALERTS_COCKPIT_ENABLED", True),
        "pushover_enabled": _env_bool("ALERTS_PUSHOVER_ENABLED"),
        "voice_enabled": _env_bool("ALERTS_VOICE_ENABLED"),
        "default_cooldown_seconds": _env_int("ALERTS_DEFAULT_COOLDOWN_SECONDS", 300),
        "credential_values_included": False,
    }


def sample_alert_events() -> list[dict[str, Any]]:
    return [
        {
            "timestamp": "2026-06-22T10:10:00-04:00",
            "event_id": "dryrun_regime_warning",
            "severity": "WARNING",
            "source": "REGIME_CHANGE",
            "symbol": "SPX",
            "profile_id": "morning_5k_call_tp75_control",
            "trade_id": "paper_fixture_1",
            "title": "Daily DA-GEX path changed",
            "message": "SPX daily path moved to R3 whipsaw. Human review is required.",
            "suggested_action": "WATCH",
            "reason_codes": ["daily_da_gex_regime_changed", "da_gex_path_flipped_or_whipsawed"],
            "suppressed": False,
            "suppression_reason": None,
            "cooldown_seconds": 300,
            "delivery_action": "ALL",
        },
        {
            "timestamp": "2026-06-22T10:10:30-04:00",
            "event_id": "dryrun_regime_duplicate",
            "severity": "WARNING",
            "source": "REGIME_CHANGE",
            "symbol": "SPX",
            "profile_id": "morning_5k_call_tp75_control",
            "trade_id": "paper_fixture_1",
            "title": "Daily DA-GEX path changed",
            "message": "SPX daily path remains R3 whipsaw. Human review is required.",
            "suggested_action": "WATCH",
            "reason_codes": ["daily_da_gex_regime_changed"],
            "suppressed": True,
            "suppression_reason": "suppressed_by_cooldown",
            "cooldown_seconds": 300,
            "delivery_action": "ALL",
        },
        {
            "timestamp": "2026-06-22T10:21:59-04:00",
            "event_id": "dryrun_paper_tp",
            "severity": "INFO",
            "source": "PAPER_EXIT",
            "symbol": "SPX",
            "profile_id": "morning_5k_call_tp75_control",
            "trade_id": "paper_fixture_1",
            "title": "Paper take profit hit",
            "message": "Paper trade paper_fixture_1 reached its take-profit rule.",
            "suggested_action": "REVIEW",
            "reason_codes": ["take_profit_threshold_hit"],
            "suppressed": False,
            "suppression_reason": None,
            "cooldown_seconds": 300,
            "delivery_action": "ALL",
        },
    ]


def _event_title(event: dict[str, Any]) -> str:
    return str(event.get("title") or str(event.get("source") or "Alert").replace("_", " ").title())


def _event_message(event: dict[str, Any]) -> str:
    if event.get("message"):
        return str(event["message"])
    reasons = ", ".join(_reasons(event.get("reason_codes")))
    return f"Recorded {str(event.get('source') or 'system').replace('_', ' ').lower()} event: {reasons}."


def build_notification_dry_run(
    events: Iterable[dict[str, Any]],
    *,
    input_source: str,
    backends: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    state = dict(backends or backend_state())
    rows: list[dict[str, Any]] = []
    for event in events:
        suppressed = _bool(event.get("suppressed"))
        delivery_action = str(event.get("delivery_action") or "ALL").upper()
        push_route = not suppressed and delivery_action in {"ALL", "PUSHOVER"}
        voice_route = not suppressed and delivery_action in {"ALL", "VOICE"}
        title = _event_title(event)
        message = _event_message(event)
        severity = str(event.get("severity") or "INFO").upper()
        action = str(event.get("suggested_action") or "REVIEW").replace("_", " ").title()
        cooldown_seconds = int(event.get("cooldown_seconds") or state["default_cooldown_seconds"])
        cooldown_status = (
            str(event.get("suppression_reason") or "suppressed")
            if suppressed else "eligible_after_cooldown_review"
        )
        for reason in _reasons(event.get("reason_codes")):
            rows.append({
                "timestamp": event.get("timestamp"),
                "event_id": event.get("event_id"),
                "severity": severity,
                "source": event.get("source"),
                "symbol": event.get("symbol"),
                "profile_id": event.get("profile_id"),
                "trade_id": event.get("trade_id"),
                "reason_code": reason,
                "suppressed": suppressed,
                "suppression_reason": event.get("suppression_reason"),
                "cooldown_seconds": cooldown_seconds,
                "cooldown_status": cooldown_status,
                "delivery_action": delivery_action,
                "push_route_eligible": push_route,
                "push_backend_enabled": bool(state.get("pushover_enabled")),
                "push_preview_title": f"[{severity}] {title}",
                "push_preview_message": message,
                "voice_route_eligible": voice_route,
                "voice_backend_enabled": bool(state.get("voice_enabled")),
                "voice_preview": f"{severity.title()}. {title}. {message} Action: {action}.",
                "dry_run_sent": False,
                "failure_handling_note": (
                    "Preview only. A delivery failure would be journaled and must never block "
                    "the local paper lifecycle."
                ),
            })
    return {
        "generated_at": generated_at or datetime.now().astimezone().isoformat(),
        "phase": "Phase 11H-A - Notification / Voice Dry-Run Preview",
        "mode": "OFFLINE_DRY_RUN_NO_SEND",
        "input_source": input_source,
        "event_count": len({row["event_id"] for row in rows}),
        "preview_row_count": len(rows),
        "suppressed_event_count": len({
            row["event_id"] for row in rows if row["suppressed"]
        }),
        "push_route_eligible_events": len({
            row["event_id"] for row in rows if row["push_route_eligible"]
        }),
        "voice_route_eligible_events": len({
            row["event_id"] for row in rows if row["voice_route_eligible"]
        }),
        "backend_state": state,
        "rows": rows,
        "dry_run_sent_count": 0,
        "offline_only": True,
        "live_rth_evidence": False,
        "failure_handling": (
            "No backend is invoked. Production notification failures remain isolated from the "
            "paper lifecycle by the existing alert boundary."
        ),
        "safety_boundaries": [
            "No Pushover request is made.",
            "No voice queue or audio playback is created.",
            "No secrets are read into the artifact.",
            "No paper lifecycle, strategy, selector, or execution behavior is invoked.",
        ],
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def collect_notification_events(
    *,
    output_root: Path | str | None = None,
    fixture: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if fixture == "sample":
        return sample_alert_events(), "deterministic_fixture"
    root = Path(output_root or REPO_ROOT / "outputs")
    journal_events = AlertJournal.under_output_root(root).load_events()
    if journal_events:
        return journal_events, "local_alert_journal"
    soak = _read_json(root / "reviews" / "latest" / "rth_soak_review.json")
    quality = soak.get("alert_quality") or []
    if quality:
        return [dict(row) for row in quality if isinstance(row, dict)], "local_soak_review"
    return sample_alert_events(), "deterministic_fixture_fallback"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _markdown(report: dict[str, Any]) -> str:
    state = report["backend_state"]
    lines = [
        "# Notification / Voice Dry-Run Preview",
        "",
        "OFFLINE PREVIEW ONLY - NOTHING WAS SENT OR SPOKEN.",
        "",
        f"- Input source: `{report['input_source']}`",
        f"- Events: {report['event_count']}",
        f"- Suppressed events: {report['suppressed_event_count']}",
        f"- Pushover enabled: {state.get('pushover_enabled', False)}",
        f"- Voice enabled: {state.get('voice_enabled', False)}",
        "",
        "| Severity | Source | Reason | Cooldown | Push preview | Voice preview |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['severity']} | {row['source']} | `{row['reason_code']}` | "
            f"{row['cooldown_status']} | {row['push_preview_title']}: "
            f"{row['push_preview_message']} | {row['voice_preview']} |"
        )
    lines.extend(["", report["failure_handling"], ""])
    return "\n".join(lines)


def write_notification_dry_run(
    report: dict[str, Any],
    *,
    output_root: Path | str = "outputs/reviews",
    run_id: str | None = None,
) -> dict[str, str]:
    root = Path(output_root)
    if not root.is_absolute():
        root = REPO_ROOT / root
    timestamp = datetime.fromisoformat(report["generated_at"])
    resolved_run_id = run_id or f"{timestamp.strftime('%Y-%m-%d_%H%M%S')}_notification_dry_run"
    run_dir = root / "runs" / resolved_run_id
    latest_dir = root / "latest"
    for directory in (run_dir, latest_dir):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "notification_dry_run.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        (directory / "notification_dry_run.md").write_text(
            _markdown(report), encoding="utf-8"
        )
        _write_csv(directory / "notification_dry_run.csv", report["rows"])
    return {
        "run_id": resolved_run_id,
        "run_dir": str(run_dir),
        "latest_dir": str(latest_dir),
    }


def load_latest_notification_dry_run(
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    root = Path(output_root or REPO_ROOT / "outputs") / "reviews" / "latest"
    report = _read_json(root / "notification_dry_run.json")
    return {"available": bool(report), "directory": str(root), **report}
