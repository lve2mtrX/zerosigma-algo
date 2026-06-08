"""Sanitized latest-readiness snapshot persistence.

Only an explicit allowlist is written. Credentials, provider objects, raw config,
and arbitrary diagnostic fields are never serialized.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SAFE_FIELDS = (
    "symbol",
    "target_dte",
    "profile_id",
    "profile_valid",
    "profile_dte",
    "zs_configured",
    "structure_provider",
    "structure_available",
    "spot",
    "corridor_10k_valid",
    "corridor_10k_reason",
    "required_strikes",
    "quote_provider",
    "tasty_configured",
    "tasty_auth_mode",
    "quote_state",
    "quote_label",
    "quote_root",
    "quote_expiration",
    "quote_chain_dte",
    "chain_returned",
    "quote_count",
    "missing_strikes",
    "top_blocker",
    "start_paper_test_enabled",
    "start_reason",
    "preview_only",
    "no_broker",
    "no_order_preview",
    "no_execution",
)


def sanitized_readiness(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "captured_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        **{key: result.get(key) for key in SAFE_FIELDS},
    }


def readiness_latest_dir(output_root: str | Path = "outputs") -> Path:
    path = Path(output_root) / "readiness" / "latest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_readiness_snapshot(
    result: dict[str, Any], *, output_root: str | Path = "outputs"
) -> Path:
    directory = readiness_latest_dir(output_root)
    target = directory / "readiness_summary.json"
    temporary = directory / "readiness_summary.json.tmp"
    temporary.write_text(
        json.dumps(sanitized_readiness(result), indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def read_readiness_snapshot(
    *, output_root: str | Path = "outputs"
) -> dict[str, Any] | None:
    path = Path(output_root) / "readiness" / "latest" / "readiness_summary.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
