"""Conservative monthly OpEx context for Pete/Stone R4-R6 regimes."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from src.utils.expiry import is_trading_day, us_market_holidays


@dataclass(frozen=True)
class OpexRegime:
    code: str
    label: str
    reason_codes: tuple[str, ...]
    opex_context: str
    days_to_opex: int | None
    expiration_context: str
    opex_date: str | None


def monthly_opex_date(year: int, month: int) -> date | None:
    """Third Friday, moved backward for a supported market holiday."""
    try:
        holidays = us_market_holidays(year)
    except ValueError:
        return None
    month_calendar = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in month_calendar if week[calendar.FRIDAY]]
    candidate = date(year, month, fridays[2])
    while not is_trading_day(candidate, holidays):
        candidate -= timedelta(days=1)
    return candidate


def _shift_month(day: date, offset: int) -> tuple[int, int]:
    index = day.year * 12 + day.month - 1 + offset
    return index // 12, index % 12 + 1


def classify_opex_context(day: date) -> OpexRegime:
    candidates: list[date] = []
    for offset in (-1, 0, 1):
        year, month = _shift_month(day, offset)
        resolved = monthly_opex_date(year, month)
        if resolved is None:
            return OpexRegime(
                "R_UNKNOWN", "Unknown OpEx Context",
                ("opex_calendar_outside_supported_range",), "unknown", None,
                "UNKNOWN", None,
            )
        candidates.append(resolved)
    previous = max((value for value in candidates if value < day), default=None)
    current_or_next = min((value for value in candidates if value >= day), default=None)
    current_month = monthly_opex_date(day.year, day.month)
    if current_month is None:
        return OpexRegime(
            "R_UNKNOWN", "Unknown OpEx Context",
            ("opex_calendar_outside_supported_range",), "unknown", None,
            "UNKNOWN", None,
        )

    week_start = current_month - timedelta(days=current_month.weekday())
    week_end = week_start + timedelta(days=6)
    if week_start <= day <= week_end:
        expiration = "MONTHLY_OPEX" if day == current_month else "OPEX_WEEK"
        return OpexRegime(
            "R5_OPEX_WEEK_MAGNET", "OpEx Week Magnet",
            ("monthly_opex_week",), "opex_week", (current_month - day).days,
            expiration, current_month.isoformat(),
        )

    if current_or_next is not None and 1 <= (current_or_next - day).days <= 14:
        return OpexRegime(
            "R4_PRE_OPEX_CHARM_BUILD", "Pre-OpEx Charm Build",
            ("within_two_weeks_before_monthly_opex",), "pre_opex",
            (current_or_next - day).days,
            "WEEKLY_EXPIRATION" if day.weekday() == calendar.FRIDAY else "NORMAL",
            current_or_next.isoformat(),
        )

    if previous is not None and 1 <= (day - previous).days <= 7:
        return OpexRegime(
            "R6_POST_OPEX_GAMMA_RESET", "Post-OpEx Gamma Reset",
            ("within_one_week_after_monthly_opex",), "post_opex",
            -(day - previous).days,
            "WEEKLY_EXPIRATION" if day.weekday() == calendar.FRIDAY else "NORMAL",
            previous.isoformat(),
        )

    return OpexRegime(
        "R_OTHER", "Normal Expiration Context", ("outside_monthly_opex_windows",),
        "normal", (current_or_next - day).days if current_or_next else None,
        "WEEKLY_EXPIRATION" if day.weekday() == calendar.FRIDAY else "NORMAL",
        current_or_next.isoformat() if current_or_next else None,
    )
