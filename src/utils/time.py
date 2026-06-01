"""Eastern-Time helpers.

The whole cockpit operates on ET. We never compute in local time.
"""

from __future__ import annotations

from datetime import datetime, time

import pytz

ET = pytz.timezone("America/New_York")


def now_et() -> datetime:
    return datetime.now(ET)


def today_et_date() -> str:
    return now_et().strftime("%Y-%m-%d")


def parse_hhmm(s: str) -> time:
    """Parse "HH:MM" to a naive time object."""
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def is_within_window(t_et: datetime, start: str, end: str) -> bool:
    """True if `t_et`'s time-of-day falls within [start, end] (HH:MM strings)."""
    tod = t_et.timetz().replace(tzinfo=None)
    return parse_hhmm(start) <= tod <= parse_hhmm(end)
