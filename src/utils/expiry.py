"""Target-DTE expiry selection — Phase 4.1.

PURE module (no I/O, no network). Picks the expiry the scanner should ask
for, given:
  - the current ET clock,
  - a target DTE (calendar OR trading days),
  - the broker chain's available expiries,
  - whether after-hours runs should roll to the next day.

The hardcoded US-market holiday list covers 2025-2027 (see
`us_market_holidays`). Refresh ANNUALLY: re-validate the list against the
NYSE calendar before the new year and update the year-cap in
`us_market_holidays`. The function raises if asked for a year outside the
supported range so silent drift is impossible.

Used by `scripts/run_scanner.py` to thread the target expiry into both
the broker `QuoteRequest` and `quote_provider.get_option_chain(...)` so
the same string lands in both places.

NOT a CALENDAR SOURCE OF TRUTH — only what the scanner needs to pick a
forward expiry without depending on `pandas_market_calendars`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

DteMode = Literal["calendar_days", "trading_days"]
ExpirySource = Literal[
    "explicit",                  # caller passed an exact YYYY-MM-DD
    "target_dte_match",          # available expiry matched the requested DTE
    "fallback_only_available",   # target not in list, took the closest forward expiry
    "after_hours_roll",          # cutoff passed, advanced by one (calendar/trading) day
    "today",                     # the target IS today and today is in available list
    "matches_target",            # alias retained for compatibility
    "fallback",                  # generic fallback
]


@dataclass(frozen=True)
class ExpiryDecision:
    """Result of `pick_target_expiry(...)` — sanitized, JSON-safe."""
    expiry: str | None             # YYYY-MM-DD; None ONLY when no expiry could be picked at all
    source: ExpirySource
    reason: str
    root_hint: str | None          # OPRA root suggestion ('SPXW' for short-dated, else None)
    days_out: int | None           # CALENDAR days from the (rolled) anchor date


# ──────────────────────────────────────────────────────────────────────
# US Market Holiday helpers (HARDCODED 2025-2027 — annual refresh needed)
# ──────────────────────────────────────────────────────────────────────

# Hardcoded US-market holidays per NYSE calendar.
# Source: https://www.nyse.com/markets/hours-calendars
#
# REFRESH ANNUALLY in November-December: re-verify the upcoming year's
# observed dates (especially when a fixed holiday lands on a weekend, NYSE
# shifts the OBSERVED close). Update `_SUPPORTED_YEARS` to extend the
# supported range.
_SUPPORTED_YEARS = frozenset({2025, 2026, 2027})


def us_market_holidays(year: int) -> set[date]:
    """Return the set of observed US-market closure dates for `year`.

    Raises ValueError if `year` is outside the supported range — drift
    must be loud, not silent. Update `_SUPPORTED_YEARS` AND extend the
    hardcoded dict below when extending the calendar.
    """
    if year not in _SUPPORTED_YEARS:
        raise ValueError(
            f"us_market_holidays: year {year!r} is outside the supported "
            f"hardcoded range {sorted(_SUPPORTED_YEARS)!r}. Update "
            f"src/utils/expiry.py for the new year (NYSE-observed dates)."
        )
    if year == 2025:
        return {
            date(2025,  1,  1),  # New Year's Day
            date(2025,  1, 20),  # MLK Day
            date(2025,  2, 17),  # Presidents' Day
            date(2025,  4, 18),  # Good Friday
            date(2025,  5, 26),  # Memorial Day
            date(2025,  6, 19),  # Juneteenth
            date(2025,  7,  4),  # Independence Day
            date(2025,  9,  1),  # Labor Day
            date(2025, 11, 27),  # Thanksgiving
            date(2025, 12, 25),  # Christmas Day
        }
    if year == 2026:
        return {
            date(2026,  1,  1),  # New Year's Day
            date(2026,  1, 19),  # MLK Day
            date(2026,  2, 16),  # Presidents' Day
            date(2026,  4,  3),  # Good Friday
            date(2026,  5, 25),  # Memorial Day
            date(2026,  6, 19),  # Juneteenth
            date(2026,  7,  3),  # Independence Day (4th = Sat → observed Fri)
            date(2026,  9,  7),  # Labor Day
            date(2026, 11, 26),  # Thanksgiving
            date(2026, 12, 25),  # Christmas Day
        }
    # 2027
    return {
        date(2027,  1,  1),  # New Year's Day
        date(2027,  1, 18),  # MLK Day
        date(2027,  2, 15),  # Presidents' Day
        date(2027,  3, 26),  # Good Friday
        date(2027,  5, 31),  # Memorial Day
        date(2027,  6, 18),  # Juneteenth observed (19th = Sat)
        date(2027,  7,  5),  # Independence Day observed (4th = Sun)
        date(2027,  9,  6),  # Labor Day
        date(2027, 11, 25),  # Thanksgiving
        date(2027, 12, 24),  # Christmas observed (25th = Sat)
    }


def is_trading_day(d: date, holidays: set[date] | None = None) -> bool:
    """True iff `d` is Mon-Fri AND not a market holiday."""
    if d.weekday() >= 5:  # Sat=5, Sun=6
        return False
    hs = holidays if holidays is not None else _holidays_safe(d.year)
    return d not in hs


def next_trading_day(d: date, holidays: set[date] | None = None) -> date:
    """Smallest date > `d` that is a trading day."""
    nxt = d + timedelta(days=1)
    hs = holidays if holidays is not None else _holidays_for_range(d.year, nxt.year + 1)
    while not is_trading_day(nxt, hs):
        nxt = nxt + timedelta(days=1)
        if nxt.year not in _SUPPORTED_YEARS:
            # Out of holiday data — re-raise loudly so the operator updates.
            us_market_holidays(nxt.year)
    return nxt


def add_trading_days(d: date, n: int, holidays: set[date] | None = None) -> date:
    """Return the date `n` trading days after `d`. n>=0.

    n=0 → if `d` itself is a trading day, returns `d`; else first forward TD.
    n=1 → next trading day strictly after `d`.
    """
    if n < 0:
        raise ValueError("add_trading_days: n must be >= 0")
    hs = holidays if holidays is not None else _holidays_for_range(d.year, d.year + 2)
    cur = d
    if not is_trading_day(cur, hs):
        cur = next_trading_day(cur, hs)
    for _ in range(n):
        cur = next_trading_day(cur, hs)
    return cur


def _holidays_safe(year: int) -> set[date]:
    """Return holidays for `year`, or an empty set if outside supported range.

    Used by `is_trading_day` for graceful per-year lookup. Differs from
    `us_market_holidays` which raises — `is_trading_day` falls back to
    weekday-only when extended.
    """
    try:
        return us_market_holidays(year)
    except ValueError:
        return set()


def _holidays_for_range(start_year: int, end_year: int) -> set[date]:
    """Union holidays across [start_year, end_year]. Missing years contribute
    empty sets so callers (next_trading_day) don't crash on year-overflow
    intermediate values; the actual cross-year guard happens at the caller.
    """
    out: set[date] = set()
    for y in range(start_year, end_year + 1):
        try:
            out |= us_market_holidays(y)
        except ValueError:
            continue
    return out


# ──────────────────────────────────────────────────────────────────────
# After-hours cutoff
# ──────────────────────────────────────────────────────────────────────

def _parse_cutoff(cutoff: str) -> time:
    """Parse 'HH:MM' as a naive 24h time. Defaults to 16:00 on bad input."""
    try:
        hh, mm = cutoff.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return time(16, 0)


def _is_after_hours(now_et_dt: datetime, cutoff: str) -> bool:
    """True iff `now_et_dt`'s time-of-day is >= cutoff (HH:MM, ET)."""
    cut = _parse_cutoff(cutoff)
    return now_et_dt.time() >= cut


# ──────────────────────────────────────────────────────────────────────
# root_hint heuristic (SPXW for short-dated; explicit-only otherwise)
# ──────────────────────────────────────────────────────────────────────

def _root_hint_for(today: date, target: date) -> str | None:
    """Heuristic: SPXW for any target within 7 calendar days; else None.

    Phase 4.1 keeps this conservative — the tasty_probe will VALIDATE the
    hint against the actual chain before using it (validate_root_hint).
    """
    delta = (target - today).days
    if 0 <= delta <= 7:
        return "SPXW"
    return None


# ──────────────────────────────────────────────────────────────────────
# pick_target_expiry — the main entry point
# ──────────────────────────────────────────────────────────────────────

def _date_from_iso(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def pick_target_expiry(
    now_et: datetime,
    target_dte: int,
    *,
    mode: DteMode = "trading_days",
    allow_after_hours_roll: bool = False,
    available_expiries: list[str] | None = None,
    after_hours_cutoff_et: str = "16:00",
    explicit_expiry: str | None = None,
) -> ExpiryDecision:
    """Pick the YYYY-MM-DD expiry to request from the broker.

    Args:
      now_et:                   wall-clock 'now' in America/New_York. Used
                                ONLY for the today/after-hours-roll calc.
      target_dte:               0=today, 1=tomorrow, etc.
      mode:                     'calendar_days' or 'trading_days'.
      allow_after_hours_roll:   if True AND now >= cutoff, advance the
                                anchor date by one day before picking.
      available_expiries:       list of YYYY-MM-DD strings the broker
                                advertises. None or [] → caller has no
                                discovery yet; we still compute a date and
                                pass it through with reason='no_chain_data'.
      after_hours_cutoff_et:    'HH:MM' string. Defaults to 16:00 (market
                                close ET).
      explicit_expiry:          if set, takes precedence over `target_dte`.
                                Used by tests or operator overrides.

    Returns:
      ExpiryDecision(expiry=..., source=..., reason=..., root_hint=..., days_out=...)

    Notes:
      - When `target_dte=0` AND today is a trading day AND today is in
        available_expiries, returns (today, 'today').
      - When `target_dte=0` AND after-hours-roll AND it's past cutoff,
        rolls to the next trading/calendar day (mode-respecting) and
        records 'after_hours_roll'.
      - When the computed target ISN'T in available_expiries, returns the
        nearest forward expiry with source='fallback_only_available'.
      - When available_expiries is empty/None, the computed target is
        returned anyway with source='target_dte_match' but reason notes
        chain wasn't probed.
    """
    today = now_et.date()

    # 1. Explicit short-circuit
    if explicit_expiry:
        d = _date_from_iso(explicit_expiry)
        if d is not None:
            days_out = (d - today).days
            return ExpiryDecision(
                expiry=explicit_expiry,
                source="explicit",
                reason="caller_supplied_explicit_expiry",
                root_hint=_root_hint_for(today, d),
                days_out=days_out,
            )

    # 2. Anchor: today, possibly rolled forward
    anchor = today
    rolled = False
    if (
        target_dte == 0
        and allow_after_hours_roll
        and _is_after_hours(now_et, after_hours_cutoff_et)
    ):
        # Roll forward one day, honoring DTE mode for the roll itself.
        if mode == "trading_days":
            anchor = next_trading_day(today)
        else:
            anchor = today + timedelta(days=1)
        rolled = True

    # 3. Compute target_date from anchor + target_dte under mode
    if target_dte <= 0:
        if mode == "trading_days":
            target_date = anchor if is_trading_day(anchor) else next_trading_day(anchor)
        else:
            target_date = anchor
    elif mode == "trading_days":
        target_date = add_trading_days(anchor, target_dte)
    else:
        target_date = anchor + timedelta(days=target_dte)

    target_iso = target_date.isoformat()
    days_out = (target_date - today).days
    root_hint = _root_hint_for(today, target_date)

    # 4. Resolve against available_expiries (when supplied)
    if not available_expiries:
        # No chain discovery — caller will validate later.
        reason = (
            "no_chain_data_for_discovery"
            if available_expiries is None
            else "empty_available_expiries"
        )
        return ExpiryDecision(
            expiry=target_iso,
            source="after_hours_roll" if rolled else "target_dte_match",
            reason=reason,
            root_hint=root_hint,
            days_out=days_out,
        )

    if target_iso in available_expiries:
        if rolled:
            return ExpiryDecision(
                expiry=target_iso,
                source="after_hours_roll",
                reason=f"after_hours_roll matched available_expiries at +{days_out}d",
                root_hint=root_hint,
                days_out=days_out,
            )
        source_for_today: ExpirySource = (
            "today" if (target_dte == 0 and target_date == today) else "target_dte_match"
        )
        return ExpiryDecision(
            expiry=target_iso,
            source=source_for_today,
            reason=f"available_expiries contains target {target_iso}",
            root_hint=root_hint,
            days_out=days_out,
        )

    # 5. Fallback — pick nearest FORWARD expiry strictly >= target_date.
    forward = sorted(
        e for e in available_expiries
        if (d := _date_from_iso(e)) is not None and d >= target_date
    )
    if forward:
        fb_iso = forward[0]
        fb_date = _date_from_iso(fb_iso)
        fb_days = (fb_date - today).days if fb_date else None
        return ExpiryDecision(
            expiry=fb_iso,
            source="fallback_only_available",
            reason=(
                f"target {target_iso} not in available_expiries; "
                f"chose nearest forward {fb_iso}"
            ),
            root_hint=_root_hint_for(today, fb_date) if fb_date else root_hint,
            days_out=fb_days,
        )

    # 6. No forward expiry at all — return None expiry so caller can NO_TRADE.
    return ExpiryDecision(
        expiry=None,
        source="fallback",
        reason=(
            f"no_forward_expiry: target {target_iso} not available, "
            f"and no forward expiries beyond it in chain"
        ),
        root_hint=root_hint,
        days_out=days_out,
    )
