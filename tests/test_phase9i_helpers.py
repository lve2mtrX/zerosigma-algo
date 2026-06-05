"""Phase 9I — pure helpers: app/profile source resolution, quote diagnostics,
drawdown/equity math, EOD staleness, profile grouping relabel.

All assertions hit PURE helpers — no Streamlit runtime, no network, no execution.
"""

from __future__ import annotations

from types import SimpleNamespace

import src.app.cockpit_helpers as ch
import src.app.operator_mode as om

# ── app vs profile data-source resolution ────────────────────────────────────

def test_resolve_run_source_mismatch_app_wins():
    r = om.resolve_run_source(om.DATA_SOURCE_LIVE, "stub", "mock")
    assert r["mismatch"] is True
    assert r["winner"] == "app" and r["data_source"] == "Live"
    assert r["structure_provider"] == "zerosigma_api" and r["quote_provider"] == "tastytrade"
    assert "Choose which source should win" in r["message"]


def test_resolve_run_source_prefer_profile():
    r = om.resolve_run_source(om.DATA_SOURCE_LIVE, "stub", "mock",
                              prefer=om.RUN_SOURCE_PROFILE)
    assert r["winner"] == "profile"
    assert r["structure_provider"] == "stub" and r["quote_provider"] == "mock"
    assert r["data_source"] == "Sandbox"


def test_resolve_run_source_no_mismatch():
    r = om.resolve_run_source(om.DATA_SOURCE_SANDBOX, "stub", "mock")
    assert r["mismatch"] is False and r["message"] is None


def test_run_source_status():
    assert om.run_source_status(chain_available=True, mismatch=False) == "ready"
    assert om.run_source_status(chain_available=True, mismatch=True) == "warning"
    assert om.run_source_status(chain_available=False, mismatch=False) == "unavailable"


def test_data_source_short():
    assert om.data_source_short(om.DATA_SOURCE_LIVE) == "Live"
    assert om.data_source_short(om.DATA_SOURCE_SANDBOX) == "Sandbox"


# ── profile grouping relabel + Simple-Mode main-only ─────────────────────────

_SUMS = [
    {"profile_id": "morning_5k_dynamic_tp75", "preset_kind": "dynamic"},
    {"profile_id": "eod_5k_call_tp50_control", "preset_kind": "control"},
    {"profile_id": "regime_put_credit_test", "preset_kind": "regime"},
    {"profile_id": "vertical_wing_no_trade", "preset_kind": None},
]


def test_category_relabel():
    assert om.profile_category("dynamic") == "Main Strategies"
    assert om.profile_category("control") == "Comparison Tests"
    assert om.profile_category("regime") == "Research / Disabled"
    assert om.profile_category(None) == "Custom"   # Phase 10C — no preset_kind → Custom
    assert om.PROFILE_CATEGORIES[0] == "Main Strategies"
    assert om.DEFAULT_SIMPLE_CATEGORY == "Main Strategies"


def test_simple_mode_profile_ids_main_only_then_all():
    assert om.simple_mode_profile_ids(_SUMS) == ["morning_5k_dynamic_tp75"]
    allids = om.simple_mode_profile_ids(_SUMS, show_all=True)
    assert "morning_5k_dynamic_tp75" in allids and "vertical_wing_no_trade" in allids
    # legacy 1DTE-style profile hidden unless show_all
    assert "vertical_wing_no_trade" not in om.simple_mode_profile_ids(_SUMS)


# ── quote-chain diagnostics ──────────────────────────────────────────────────

def _qs(**kw):
    base = dict(provider_name="tastytrade", connected=True, last_error=None,
                notes="x", last_chain_ts=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_quote_diag_tasty_reasons():
    assert ch.quote_chain_status(resolved_quote_name="tastytrade",
        quote_status=_qs(last_error="auth_failed: 401"), chain=None)["reason_code"] == "tasty_auth_failed"
    assert ch.quote_chain_status(resolved_quote_name="tastytrade",
        quote_status=_qs(last_error="chain_unresolved: SPXW"), chain=None)["reason_code"] == "root_or_expiry_unresolved"
    assert ch.quote_chain_status(resolved_quote_name="tastytrade",
        quote_status=_qs(last_error="quote_fetch_failed:http=500"), chain=None)["reason_code"] == "tasty_http_error"
    assert ch.quote_chain_status(resolved_quote_name="tastytrade",
        quote_status=_qs(connected=False), chain=None)["reason_code"] == "tasty_no_chain"


def test_quote_diag_provider_and_fallbacks():
    null_r = ch.quote_chain_status(resolved_quote_name="null", chain=None)
    assert null_r["reason_code"] == "provider_null" and null_r["available"] is False
    mock_r = ch.quote_chain_status(resolved_quote_name="mock", chain=object())
    assert mock_r["available"] is True and mock_r["reason_code"] == "provider_mock"
    cfg_r = ch.quote_chain_status(resolved_quote_name="mock", quote_provider_error="x", chain=object())
    assert cfg_r["reason_code"] == "tasty_config_mock_fallback" and cfg_r["available"] is True
    # unknown cause → generic, never overclaim
    unk = ch.quote_chain_status(resolved_quote_name="tastytrade",
        quote_status=_qs(last_chain_ts="2026-06-03T10:00"), chain=None)
    assert unk["reason_code"] == "unknown"
    assert "no usable chain" in unk["simple_reason"]


def test_quote_diag_advanced_block_and_no_crash():
    r = ch.quote_chain_status(resolved_quote_name="tastytrade", quote_status=None,
                              structure_error="boom", chain=None)
    assert r["available"] is False
    assert r["advanced"]["structure_error"] == "boom"
    assert r["reason_code"] == "structure_error"


# ── equity / drawdown math ───────────────────────────────────────────────────

_TRADES = [
    {"closed_at": "2026-06-01T10:00", "realized_pnl": 100, "profile_id": "A", "exit_reason": "take_profit"},
    {"closed_at": "2026-06-01T15:00", "realized_pnl": -150, "profile_id": "A", "exit_reason": "stop_loss"},
    {"closed_at": "2026-06-02T11:00", "realized_pnl": 80, "profile_id": "B", "exit_reason": "eod_exit"},
]


def test_equity_curve_and_drawdown():
    eq = ch.equity_curve_from_closed_trades(_TRADES)
    assert [p["cumulative"] for p in eq] == [100.0, -50.0, 30.0]
    cum = [p["cumulative"] for p in eq]
    dd = ch.drawdown_series(cum)
    assert dd[1]["drawdown"] == -150.0   # 100 peak → -50
    mdd = ch.max_drawdown(cum)
    assert mdd["max_drawdown"] == -150.0
    # with starting balance the percent is vs peak EQUITY
    mdd_bal = ch.max_drawdown(cum, starting_balance=10000)
    assert mdd_bal["max_drawdown_pct"] == -1.49


def test_pnl_groupings_and_outcomes():
    assert ch.daily_pnl_from_closed_trades(_TRADES) == [
        {"date": "2026-06-01", "realized_pnl": -50.0},
        {"date": "2026-06-02", "realized_pnl": 80.0}]
    by = ch.pnl_by_profile(_TRADES)
    assert {r["profile_id"] for r in by} == {"A", "B"}
    oc = ch.trade_outcome_counts(_TRADES)
    assert oc == {"wins": 2, "losses": 1, "flat": 0, "total": 3, "win_rate": 66.7}
    assert ("take_profit", 1) in ch.exit_reason_counts(_TRADES)


def test_empty_data_graceful():
    assert ch.equity_curve_from_closed_trades([]) == []
    assert ch.equity_curve_from_closed_trades(None) == []
    mdd = ch.max_drawdown([])
    assert mdd["max_drawdown"] == 0.0 and mdd["max_drawdown_pct"] is None
    assert ch.trade_outcome_counts([])["win_rate"] == 0.0
    assert ch.daily_pnl_from_closed_trades([]) == []


# ── EOD staleness ────────────────────────────────────────────────────────────

def test_is_eod_stale():
    assert ch.is_eod_stale(None, "2026-06-03T10:00") is True          # missing
    assert ch.is_eod_stale("2026-06-03T09:00", "2026-06-03T10:00") is True   # older
    assert ch.is_eod_stale("2026-06-03T11:00", "2026-06-03T10:00") is False  # fresh
    assert ch.is_eod_stale("2026-06-03T11:00", None) is False         # no run to compare
    # tz-aware run timestamp must not crash the comparison
    assert ch.is_eod_stale("2026-06-03T09:00", "2026-06-03T10:00-04:00") is True


def test_eod_summary_status_shape():
    s = ch.eod_summary_status()
    for k in ("exists", "generated_at", "date", "latest_run_at", "stale", "note"):
        assert k in s
    assert isinstance(s["stale"], bool) and isinstance(s["note"], str)
