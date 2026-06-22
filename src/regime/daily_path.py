"""Chronological DA-GEX sign-path state for Pete/Stone R0-R3 regimes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MIN_PATH_OBSERVATIONS = 2


@dataclass(frozen=True)
class DaGexPathState:
    session_date: str | None = None
    observations: tuple[float, ...] = ()
    signs: tuple[int, ...] = ()
    timestamps: tuple[str, ...] = ()
    sign_changes: int = 0


@dataclass(frozen=True)
class DailyPathRegime:
    code: str
    label: str
    reason_codes: tuple[str, ...]
    observation_count: int
    sign_changes: int
    summary: str


def _as_datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=ET)


def _sign(value: float | None) -> int | None:
    if value is None or value == 0:
        return None
    return 1 if value > 0 else -1


def append_da_gex_observation(
    state: DaGexPathState | None,
    value: float | None,
    timestamp: datetime | str,
) -> DaGexPathState:
    """Append one observation without mutation, lookahead, or duplicate timestamps."""
    current = state or DaGexPathState()
    observed_at = _as_datetime(timestamp)
    session_date = observed_at.astimezone(ET).date().isoformat()
    if current.session_date != session_date:
        current = DaGexPathState(session_date=session_date)
    sign = _sign(value)
    if sign is None:
        return current
    timestamp_text = observed_at.isoformat()
    if current.timestamps and current.timestamps[-1] == timestamp_text:
        return current
    changes = current.sign_changes
    if current.signs and current.signs[-1] != sign:
        changes += 1
    return DaGexPathState(
        session_date=session_date,
        observations=(*current.observations, float(value)),
        signs=(*current.signs, sign),
        timestamps=(*current.timestamps, timestamp_text),
        sign_changes=changes,
    )


def build_da_gex_path(
    observations: Iterable[tuple[datetime | str, float | None]],
) -> DaGexPathState:
    state = DaGexPathState()
    for timestamp, value in observations:
        state = append_da_gex_observation(state, value, timestamp)
    return state


def classify_daily_path(
    state: DaGexPathState | None,
    *,
    minimum_observations: int = MIN_PATH_OBSERVATIONS,
) -> DailyPathRegime:
    current = state or DaGexPathState()
    count = len(current.signs)
    if count < max(1, minimum_observations):
        return DailyPathRegime(
            code="R0_PROVISIONAL",
            label="Provisional / Insufficient DA-GEX Path",
            reason_codes=("da_gex_path_too_few_observations",),
            observation_count=count,
            sign_changes=current.sign_changes,
            summary=f"R0 provisional: {count} confirmed DA-GEX observation(s).",
        )
    if current.sign_changes > 0:
        return DailyPathRegime(
            code="R3_WHIPSAW",
            label="DA-GEX Whipsaw / Unstable Path",
            reason_codes=("da_gex_sign_flip_detected", "da_gex_path_unstable"),
            observation_count=count,
            sign_changes=current.sign_changes,
            summary=(
                f"R3 whipsaw: {current.sign_changes} sign change(s) across "
                f"{count} DA-GEX observations."
            ),
        )
    if all(sign < 0 for sign in current.signs):
        return DailyPathRegime(
            code="R1_NEGATIVE_TREND",
            label="Negative DA-GEX Trend",
            reason_codes=("da_gex_path_all_negative",),
            observation_count=count,
            sign_changes=0,
            summary=f"R1 negative trend: all {count} DA-GEX observations are negative.",
        )
    if all(sign > 0 for sign in current.signs):
        return DailyPathRegime(
            code="R2_POSITIVE_DRIFT",
            label="Positive DA-GEX Drift",
            reason_codes=("da_gex_path_all_positive",),
            observation_count=count,
            sign_changes=0,
            summary=f"R2 positive drift: all {count} DA-GEX observations are positive.",
        )
    return DailyPathRegime(
        code="R0_UNKNOWN",
        label="Unknown DA-GEX Path",
        reason_codes=("da_gex_path_unclassified",),
        observation_count=count,
        sign_changes=current.sign_changes,
        summary="R0 unknown: the available DA-GEX path is not classifiable.",
    )
