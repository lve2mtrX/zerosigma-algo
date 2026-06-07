"""Phase 4.2 — strict target-DTE + quote-age clock-skew clamp.

NO network, NO Tasty creds. Two areas:

  1. STRICT target-DTE via the scanner harness (stub structure + mock quotes):
     - strict OFF (default) → a target_dte the chain can't serve falls back and
       still TRADES (byte-identical 4.1 behavior);
     - strict ON → the fallback is SUPPRESSED: decision NO_TRADE, the row carries
       a 'strict_target_dte_unavailable' blocker, and there is NO traceback;
     - an EXACT target_dte match (today) passes strict mode untouched.

  2. CLOCK-SKEW clamp on _candidate_row: a NEGATIVE oldest-leg quote age (quote
     timestamp AHEAD of the scanner clock) clamps quote_age_seconds to 0.0 and
     sets quote_clock_skew_detected / quote_clock_skew_seconds; a POSITIVE age
     flows through unchanged; a missing timestamp stays None.
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from src.app.session_state import SessionConfig
from src.providers.quotes.types import OptionChainSnapshot
from src.risk.limits import RiskProfile
from src.strategies.base import Candidate

rs = importlib.import_module("scripts.run_scanner")

_ET = ZoneInfo("America/New_York")
_EXACT_MATCH_NOW = datetime(2026, 6, 2, 11, 0, tzinfo=_ET)  # Tuesday
_WEEKEND_NOW = datetime(2026, 6, 6, 11, 0, tzinfo=_ET)  # Saturday


# ── strict target-DTE through the scanner ────────────────────────────────

def _run(
    monkeypatch,
    tmp_path,
    argv: list[str],
    capsys=None,
    *,
    now: datetime = _EXACT_MATCH_NOW,
) -> tuple[int, str]:
    from src.providers.quotes import mock_provider
    from src.providers.structure import stub
    from src.utils import time as time_utils

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    # Make the strict knobs deterministic: clear any ambient env so the CLI
    # flag (or its absence) is the only signal.
    monkeypatch.delenv("STRICT_TARGET_DTE", raising=False)
    monkeypatch.delenv("TARGET_DTE", raising=False)
    # The scanner, stub structure provider, and mock quote provider each import
    # now_et directly. Freeze all three so exact/fallback behavior never depends
    # on the machine's current weekday.
    for module in (time_utils, stub, mock_provider):
        monkeypatch.setattr(module, "now_et", lambda: now)
    monkeypatch.setattr(sys, "argv", argv)
    importlib.reload(rs)
    rc = rs.main()
    out = ""
    if capsys is not None:
        out = capsys.readouterr().out
    return rc, out


def test_strict_off_allows_fallback(monkeypatch, tmp_path, capsys):
    """Control: --target-dte 1 with a single-expiry stub chain falls back to
    today's expiry and the tick completes (rc=0) — the 4.1 lax behavior. The
    row must NOT carry the strict blocker."""
    rc, out = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner",
        "--strategy", "vertical_wing_v1",
        "--quote-provider", "mock",
        "--structure-provider", "stub",
        "--target-dte", "1",
        "--dte-mode", "trading_days",
        "--print-candidates",
    ], capsys)
    assert rc == 0
    assert "Traceback" not in out
    assert "strict_target_dte_unavailable" not in out
    # strict flags present and OFF in the audit print
    assert "strict_target_dte=False" in out
    assert "strict_target_dte_passed=True" in out


def test_strict_on_blocks_fallback_cleanly(monkeypatch, tmp_path, capsys):
    """--target-dte 1 + --strict-target-dte: the stub chain has only today's
    expiry, so target_dte=1 can only be served by a fallback → strict mode
    SUPPRESSES the trade. Decision NO_TRADE, blocker present, NO traceback."""
    rc, out = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner",
        "--strategy", "vertical_wing_v1",
        "--quote-provider", "mock",
        "--structure-provider", "stub",
        "--target-dte", "1",
        "--dte-mode", "trading_days",
        "--strict-target-dte",
        "--print-candidates",
    ], capsys)
    assert rc == 0
    assert "Traceback" not in out
    assert "strict_target_dte_unavailable" in out
    assert "strict_target_dte=True" in out
    assert "strict_target_dte_passed=False" in out
    # The decision header on every candidate block must read NO_TRADE.
    assert "decision=NO_TRADE" in out
    assert "decision=TRADE_CALL_CREDIT" not in out
    assert "decision=TRADE_PUT_CREDIT" not in out


def test_strict_on_exact_match_today_passes(monkeypatch, tmp_path, capsys):
    """On a fixed trading day, target DTE 0 exactly matches the stub expiry,
    so strict mode is a no-op and a trade can still be selected."""
    rc, out = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner",
        "--strategy", "vertical_wing_v1",
        "--quote-provider", "mock",
        "--structure-provider", "stub",
        "--target-dte", "0",
        "--strict-target-dte",
        "--print-candidates",
    ], capsys)
    assert rc == 0
    assert "Traceback" not in out
    assert "strict_target_dte_unavailable" not in out
    assert "strict_target_dte=True" in out
    assert "strict_target_dte_passed=True" in out
    # Exact-match strict does not force NO_TRADE; the mock smoke chain trades.
    assert "decision=TRADE_CALL_CREDIT" in out


def test_strict_on_weekend_blocks_fallback_when_exact_expiry_missing(
    monkeypatch, tmp_path, capsys
):
    """On a fixed weekend, target DTE 0 resolves to the next trading day.
    The weekend-dated stub expiry is not an exact match, so strict mode blocks."""
    rc, out = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner",
        "--strategy", "vertical_wing_v1",
        "--quote-provider", "mock",
        "--structure-provider", "stub",
        "--target-dte", "0",
        "--strict-target-dte",
        "--print-candidates",
    ], capsys, now=_WEEKEND_NOW)
    assert rc == 0
    assert "Traceback" not in out
    assert "strict_target_dte_unavailable" in out
    assert "strict_target_dte=True" in out
    assert "strict_target_dte_passed=False" in out
    assert "decision=NO_TRADE" in out


def test_help_lists_strict_target_dte_flag(monkeypatch):
    """argparse --help surfaces the new --strict-target-dte flag (alongside the
    4.1 flags)."""
    import os
    import subprocess
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    out = subprocess.run(
        [sys.executable, "-m", "scripts.run_scanner", "--help"],
        capture_output=True, text=True, timeout=30, env=env,
        encoding="utf-8", errors="replace",
    )
    assert out.returncode == 0
    for flag in ("--strict-target-dte", "--target-dte", "--dte-mode"):
        assert flag in out.stdout, f"missing {flag} in --help"


# ── clock-skew clamp on _candidate_row ────────────────────────────────────

def _candidate_with_quote_time(qt_iso: str | None) -> Candidate:
    """Build a minimal CALL_CREDIT candidate whose legs carry quote_time."""
    leg = {"bid": 1.05, "ask": 1.15, "mid": 1.10, "validation_passed": True}
    short = dict(leg)
    long_ = dict(leg)
    if qt_iso is not None:
        short["quote_time"] = qt_iso
        long_["quote_time"] = qt_iso
    c = Candidate(
        strategy_id="vw_v1", side="CALL_CREDIT",
        symbol="SPX", expiry="2026-06-02",
        short_strike=5815.0, long_strike=5820.0,
        credit=0.60, max_risk=4.40, reward_risk=0.136,
        breakeven=5815.60, distance_from_spot=15.0,
        meta={
            "short_leg": short, "long_leg": long_,
            "worst_leg_bid_ask_abs": 0.10, "worst_leg_bid_ask_pct_of_mid": 0.02,
            "bid_ask_quality": 1.0, "bid_ask_quality_mode": "relative",
            "bid_ask_quality_reason": "pct<=good_3.00%",
            "quote_quality_bucket": "good", "quote_quality_reason": "pct<=good_3.00%",
            "risk_rejections": {"planned_loss_cap": {"passed": True}},
        },
    )
    c.score = 0.65
    c.score_threshold = 0.60
    c.score_gap_to_threshold = -0.05
    c.score_edge = 0.05
    c.score_edge_passed = True
    c.marginal_score = False
    return c


def _session() -> SessionConfig:
    return SessionConfig.from_profile(RiskProfile(
        name="t",
        raw={"starting_balance": 10_000, "contracts_per_trade": 1,
             "default_stop_variant": "BASELINE_CASH_SETTLE"},
    ))


def _chain(ts: datetime) -> OptionChainSnapshot:
    return OptionChainSnapshot(
        underlying="SPX", spot=5800.0, expiry="2026-06-02",
        quotes=[], quote_ts=ts, provider_name="mock",
    )


def test_negative_quote_age_clamped_to_zero_with_skew_flag():
    """Quote timestamp 3s AHEAD of the scanner clock → quote_age_seconds clamps
    to 0.0, quote_clock_skew_detected=True, quote_clock_skew_seconds≈3.0."""
    ts = datetime(2026, 6, 2, 14, 0, 0, tzinfo=UTC)
    future = (ts + timedelta(seconds=3)).isoformat()
    c = _candidate_with_quote_time(future)
    row = rs._candidate_row(
        "vw_v1", c, _session(), ts, "TRADE_CALL_CREDIT",
        chain=_chain(ts), target_dte=0, available_expiries=["2026-06-02"],
    )
    assert row["quote_age_seconds"] == 0.0
    assert row["quote_clock_skew_detected"] is True
    assert row["quote_clock_skew_seconds"] == 3.0
    # mirrored onto meta for the JSONL / Streamlit readers
    assert c.meta["quote_clock_skew_detected"] is True
    assert c.meta["quote_clock_skew_seconds"] == 3.0


def test_small_negative_within_tolerance_still_clamps():
    """A tiny skew (0.5s, within the 2.0s default tolerance) STILL clamps the
    age to 0.0 and flags skew — the tolerance only labels the magnitude, it
    does not let a negative age leak through."""
    ts = datetime(2026, 6, 2, 14, 0, 0, tzinfo=UTC)
    future = (ts + timedelta(seconds=0.5)).isoformat()
    c = _candidate_with_quote_time(future)
    row = rs._candidate_row(
        "vw_v1", c, _session(), ts, "TRADE_CALL_CREDIT",
        chain=_chain(ts), target_dte=0, available_expiries=["2026-06-02"],
    )
    assert row["quote_age_seconds"] == 0.0
    assert row["quote_clock_skew_detected"] is True
    assert row["quote_clock_skew_seconds"] == 0.5


def test_positive_quote_age_flows_through_unchanged():
    """A normal positive age (quote 5s OLDER than the clock) is reported as-is,
    with NO skew flagged."""
    ts = datetime(2026, 6, 2, 14, 0, 0, tzinfo=UTC)
    past = (ts - timedelta(seconds=5)).isoformat()
    c = _candidate_with_quote_time(past)
    row = rs._candidate_row(
        "vw_v1", c, _session(), ts, "TRADE_CALL_CREDIT",
        chain=_chain(ts), target_dte=0, available_expiries=["2026-06-02"],
    )
    assert row["quote_age_seconds"] == 5.0
    assert row["quote_clock_skew_detected"] is False
    assert row["quote_clock_skew_seconds"] == 0.0


def test_missing_quote_time_keeps_age_none():
    """No quote_time on either leg → quote_age_seconds stays None (not 0.0),
    and no skew is flagged. Guards the regression called out in the design."""
    ts = datetime(2026, 6, 2, 14, 0, 0, tzinfo=UTC)
    c = _candidate_with_quote_time(None)
    row = rs._candidate_row(
        "vw_v1", c, _session(), ts, "TRADE_CALL_CREDIT",
        chain=_chain(ts), target_dte=0, available_expiries=["2026-06-02"],
    )
    assert row["quote_age_seconds"] is None
    assert row["quote_clock_skew_detected"] is False
    assert row["quote_clock_skew_seconds"] == 0.0
