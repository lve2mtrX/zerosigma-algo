"""Config-change log — append a JSONL record whenever a session value changes.

The cockpit treats `config/risk_profiles.yaml` as a TEMPLATE; the user can
override any field at session start (and, eventually, mid-session). Every
override goes here so the EOD audit can show exactly what was active when.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.storage.csv_writer import append_jsonl
from src.storage.paths import config_change_log_path


def log_config_change(
    output_root: Path,
    *,
    field: str,
    old_value: Any,
    new_value: Any,
    active_strategy: str | None,
    active_risk_profile: str | None,
    source: str = "streamlit_session_control",
    ts: datetime | None = None,
    date_str: str | None = None,
) -> Path:
    """Append one change record. Returns the JSONL path written."""
    path = config_change_log_path(output_root, date_str)
    append_jsonl(path, {
        "ts": (ts or datetime.now().astimezone()).isoformat(),
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "active_strategy": active_strategy,
        "active_risk_profile": active_risk_profile,
        "source": source,
    })
    return path


def log_session_snapshot(
    output_root: Path,
    *,
    session_dict: dict[str, Any],
    active_strategy: str | None,
    active_risk_profile: str | None,
    source: str = "session_start",
    ts: datetime | None = None,
    date_str: str | None = None,
) -> Path:
    """Append a single 'session_start' or 'session_reset' snapshot of all fields.

    Use this when per-field deltas aren't tracked (e.g., on dashboard boot).
    """
    path = config_change_log_path(output_root, date_str)
    append_jsonl(path, {
        "ts": (ts or datetime.now().astimezone()).isoformat(),
        "event": source,
        "active_strategy": active_strategy,
        "active_risk_profile": active_risk_profile,
        "session_snapshot": dict(session_dict),
    })
    return path
