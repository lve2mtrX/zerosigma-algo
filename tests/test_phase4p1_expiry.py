"""Phase 4.1 — pick_target_expiry + holiday helpers (pure module).

NO network, NO Tasty creds. Pure date math against the hardcoded NYSE
holiday list in src/utils/expiry.py.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from src.utils.expiry import (
    add_trading_days,
    is_trading_day,
    next_trading_day,
    pick_target_expiry,
    us_market_holidays,
)

# ── holiday helpers ─────────────────────────────────────────────────────

class TestHolidays:
    def test_2025_holidays_complete(self):
        h = us_market_holidays(2025)
        assert date(2025, 1, 1) in h
        assert date(2025, 7, 4) in h
        assert date(2025, 12, 25) in h
        assert len(h) == 10                            # canonical NYSE 10

    def test_2026_independence_day_observed_friday(self):
        # July 4 2026 is a Saturday → observed Friday July 3
        h = us_market_holidays(2026)
        assert date(2026, 7, 3) in h
        assert date(2026, 7, 4) not in h

    def test_2027_supported(self):
        h = us_market_holidays(2027)
        assert date(2027, 1, 1) in h

    def test_year_out_of_range_raises_loudly(self):
        with pytest.raises(ValueError) as exc:
            us_market_holidays(2030)
        assert "2030" in str(exc.value) or "hardcoded" in str(exc.value).lower()


class TestIsTradingDay:
    def test_saturday_is_not(self):
        assert is_trading_day(date(2026, 6, 6)) is False  # Sat

    def test_sunday_is_not(self):
        assert is_trading_day(date(2026, 6, 7)) is False  # Sun

    def test_weekday_yes(self):
        assert is_trading_day(date(2026, 6, 1)) is True  # Mon

    def test_holiday_no(self):
        assert is_trading_day(date(2026, 7, 3)) is False  # Indep Day observed


class TestNextTradingDay:
    def test_friday_to_monday(self):
        # Fri 2026-06-05 → Mon 2026-06-08
        assert next_trading_day(date(2026, 6, 5)) == date(2026, 6, 8)

    def test_holiday_chain_thu_observed_to_mon(self):
        # 2026-07-03 is Friday observed holiday; 2026-07-04 Sat; 2026-07-05 Sun.
        # next_trading_day(Thursday 2026-07-02) → Monday 2026-07-06
        assert next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)


class TestAddTradingDays:
    def test_n_zero_returns_same_when_trading_day(self):
        assert add_trading_days(date(2026, 6, 1), 0) == date(2026, 6, 1)

    def test_n_zero_advances_when_not_trading_day(self):
        # Sat → Mon
        assert add_trading_days(date(2026, 6, 6), 0) == date(2026, 6, 8)

    def test_one_trading_day_forward(self):
        assert add_trading_days(date(2026, 6, 1), 1) == date(2026, 6, 2)

    def test_two_trading_days_crosses_weekend(self):
        # Thursday + 2 trading days = Monday
        assert add_trading_days(date(2026, 6, 4), 2) == date(2026, 6, 8)

    def test_n_skips_holiday(self):
        # Thursday 2026-07-02 + 1 TD = Monday 2026-07-06 (skips obs-holiday Fri)
        assert add_trading_days(date(2026, 7, 2), 1) == date(2026, 7, 6)


# ── pick_target_expiry ──────────────────────────────────────────────────

def _et_dt(y: int, m: int, d: int, hh: int = 9, mm: int = 30) -> datetime:
    return datetime(y, m, d, hh, mm)


class TestPickTargetExpiry:
    def test_today_target_dte_zero_with_today_in_chain(self):
        now = _et_dt(2026, 6, 1, 9, 30)
        expiries = ["2026-06-01", "2026-06-02", "2026-06-08"]
        out = pick_target_expiry(now, target_dte=0, available_expiries=expiries)
        assert out.expiry == "2026-06-01"
        assert out.source == "today"
        assert out.days_out == 0
        assert out.root_hint == "SPXW"   # within 7 days → SPXW heuristic

    def test_target_dte_zero_weekend_rolls_to_monday(self):
        # Sat 2026-06-06 → Monday 2026-06-08 (trading_days)
        now = _et_dt(2026, 6, 6, 12, 0)
        expiries = ["2026-06-05", "2026-06-08"]
        out = pick_target_expiry(now, target_dte=0, mode="trading_days",
                                 available_expiries=expiries)
        assert out.expiry == "2026-06-08"
        # Not after_hours_roll (no opt-in); source is target_dte_match since
        # the computed target IS in the list.
        assert out.source in ("target_dte_match", "today")

    def test_after_hours_roll_advances_one_trading_day(self):
        # 4:01 PM on Monday → next trading day = Tuesday
        now = _et_dt(2026, 6, 1, 16, 1)
        expiries = ["2026-06-01", "2026-06-02", "2026-06-03"]
        out = pick_target_expiry(
            now, target_dte=0, mode="trading_days",
            allow_after_hours_roll=True,
            available_expiries=expiries,
        )
        assert out.expiry == "2026-06-02"
        assert out.source == "after_hours_roll"

    def test_after_hours_roll_disabled_stays_today(self):
        now = _et_dt(2026, 6, 1, 17, 30)
        expiries = ["2026-06-01", "2026-06-02"]
        out = pick_target_expiry(
            now, target_dte=0, mode="trading_days",
            allow_after_hours_roll=False,
            available_expiries=expiries,
        )
        assert out.expiry == "2026-06-01"
        assert out.source in ("today", "target_dte_match")

    def test_target_dte_one_trading_days(self):
        now = _et_dt(2026, 6, 1, 9, 30)
        expiries = ["2026-06-01", "2026-06-02", "2026-06-08"]
        out = pick_target_expiry(now, target_dte=1, mode="trading_days",
                                 available_expiries=expiries)
        assert out.expiry == "2026-06-02"
        assert out.source == "target_dte_match"
        assert out.days_out == 1

    def test_target_dte_two_trading_days_crosses_weekend(self):
        # Thursday 2026-06-04 + 2 TD = Monday 2026-06-08
        now = _et_dt(2026, 6, 4, 9, 30)
        expiries = ["2026-06-04", "2026-06-05", "2026-06-08"]
        out = pick_target_expiry(now, target_dte=2, mode="trading_days",
                                 available_expiries=expiries)
        assert out.expiry == "2026-06-08"

    def test_target_dte_two_calendar_days(self):
        # Mon 2026-06-01 + 2 calendar = Wed 2026-06-03
        now = _et_dt(2026, 6, 1, 9, 30)
        expiries = ["2026-06-01", "2026-06-02", "2026-06-03"]
        out = pick_target_expiry(now, target_dte=2, mode="calendar_days",
                                 available_expiries=expiries)
        assert out.expiry == "2026-06-03"

    def test_holiday_aware_2026_07_06(self):
        # Thursday 2026-07-02 + 1 TD should be Monday 2026-07-06 (Fri = obs Indep)
        now = _et_dt(2026, 7, 2, 9, 30)
        expiries = ["2026-07-02", "2026-07-06", "2026-07-07"]
        out = pick_target_expiry(now, target_dte=1, mode="trading_days",
                                 available_expiries=expiries)
        assert out.expiry == "2026-07-06"

    def test_fallback_when_target_not_in_chain(self):
        # Target = 2026-06-02 but chain only has 06-03 and 06-08
        now = _et_dt(2026, 6, 1, 9, 30)
        expiries = ["2026-06-03", "2026-06-08"]
        out = pick_target_expiry(now, target_dte=1, mode="trading_days",
                                 available_expiries=expiries)
        assert out.expiry == "2026-06-03"
        assert out.source == "fallback_only_available"

    def test_no_forward_expiry_returns_none(self):
        # Target is past everything in the chain → no forward expiry
        now = _et_dt(2026, 12, 31, 9, 30)
        expiries = ["2026-06-01"]
        out = pick_target_expiry(now, target_dte=0, available_expiries=expiries)
        assert out.expiry is None
        assert out.source == "fallback"

    def test_root_hint_spxw_within_7_days(self):
        now = _et_dt(2026, 6, 1, 9, 30)
        out = pick_target_expiry(now, target_dte=0, available_expiries=None)
        assert out.root_hint == "SPXW"

    def test_root_hint_none_beyond_7_days(self):
        # 10 calendar days out → root_hint None (let the resolver decide)
        now = _et_dt(2026, 6, 1, 9, 30)
        out = pick_target_expiry(now, target_dte=8, mode="calendar_days",
                                 available_expiries=None)
        assert out.root_hint is None

    def test_empty_available_expiries_propagates_target(self):
        now = _et_dt(2026, 6, 1, 9, 30)
        out = pick_target_expiry(now, target_dte=0, available_expiries=[])
        # We still compute the target, but flag it for downstream
        assert out.expiry == "2026-06-01"
        assert out.source in ("target_dte_match", "today", "after_hours_roll")
        assert "empty_available_expiries" in out.reason

    def test_explicit_expiry_short_circuits(self):
        now = _et_dt(2026, 6, 1, 9, 30)
        out = pick_target_expiry(
            now, target_dte=99, explicit_expiry="2026-06-15",
            available_expiries=["2026-06-15"],
        )
        assert out.expiry == "2026-06-15"
        assert out.source == "explicit"

    def test_decision_is_immutable(self):
        # ExpiryDecision is a frozen dataclass
        from dataclasses import FrozenInstanceError
        now = _et_dt(2026, 6, 1)
        out = pick_target_expiry(now, target_dte=0, available_expiries=None)
        with pytest.raises(FrozenInstanceError):
            out.expiry = "2099-01-01"                     # type: ignore[misc]


# ── Missing expiry → clean NO_TRADE path (handled by scanner) ──
def test_missing_expiry_returns_explicit_none_expiry_no_traceback():
    """When the chain has NO forward expiries beyond the target, the
    decision returns expiry=None so the scanner can NO_TRADE without
    a traceback."""
    now = _et_dt(2026, 6, 1, 9, 30)
    out = pick_target_expiry(now, target_dte=5, mode="calendar_days",
                             available_expiries=["2026-05-15"])
    assert out.expiry is None
    assert out.source == "fallback"
    assert "no_forward_expiry" in out.reason

    # And nothing in the decision contains a raw exception
    for v in (out.reason, out.source):
        assert "Traceback" not in (v or "")
