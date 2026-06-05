"""Phase 10D additional task: global Strategy Synopsis + backtest run narrative.

Pure deterministic helper tests plus source-level placement checks. No AI/API
calls, no execution, no order preview.
"""

from __future__ import annotations

from pathlib import Path

from src.app import operator_mode as om
from src.config.strategy_profiles import load_profile_file

_REPO = Path(__file__).resolve().parents[1]
_SRC = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def _profile(profile_id: str):
    return load_profile_file(profile_id).profile


def test_strategy_synopsis_exists_and_is_deterministic():
    p = _profile("morning_5k_dynamic_tp75")
    one = om.strategy_synopsis(p, context="run")
    two = om.strategy_synopsis(p, context="run")
    assert one == two
    assert "Morning 5K Dynamic" in one
    assert "Broker execution and order preview remain deferred" in one


def test_dynamic_profile_synopsis_mentions_threshold_sides_time_tp_sl():
    text = om.strategy_synopsis(_profile("eod_5k_dynamic_sl150_no_tp"), context="run")
    assert "5K wing structure" in text
    assert "15:15 ET" in text
    assert "both call-credit and put-credit" in text
    assert "balanced structure, premium, distance from spot" in text
    assert "no fixed take-profit" in text
    assert "150% credit stop" in text


def test_2k_dynamic_profile_mentions_2k_threshold():
    text = om.strategy_synopsis(_profile("morning_2k_dynamic_no_tp"), context="run")
    assert "2K wing structure" in text
    assert "both call-credit and put-credit" in text


def test_call_only_control_synopsis():
    text = om.strategy_synopsis(_profile("morning_5k_call_tp75_control"), context="run")
    assert "comparison/control profile" in text
    assert "only evaluates call-credit spreads" in text
    assert "Put Ceiling" in text


def test_put_only_synopsis():
    text = om.strategy_synopsis(_profile("regime_put_credit_test"), context="run")
    assert "only evaluates put-credit spreads" in text
    assert "Call Floor" in text


def test_observe_synopsis_says_should_not_select_trades():
    text = om.strategy_synopsis(_profile("observe_dynamic_5k"), context="run")
    assert "observe-only profile" in text
    assert "should not select trades" in text


def test_custom_profile_synopsis():
    text = om.strategy_synopsis({
        "profile_id": "custom_saved_profile",
        "profile_name": "My Custom Profile",
        "strategy_type": "vertical_credit_spread",
        "symbol": "SPX",
        "target_dte": 0,
        "daily_selector": "score_best_valid",
        "allow_call_credit": True,
        "allow_put_credit": True,
        "target_time": "12:30",
    }, context="builder")
    assert "My Custom Profile" in text
    assert "custom saved profile" in text


def test_synopsis_hides_raw_enums():
    text = om.strategy_synopsis({
        "profile_name": "Raw Enum Test",
        "symbol": "SPX",
        "target_dte": 0,
        "threshold_label": "5k",
        "daily_selector": "balanced_structure_premium_valid",
        "allow_call_credit": True,
        "allow_put_credit": True,
        "stop_loss_pct": 1.5,
        "take_profit_pct": 0.75,
    }, context="run")
    for raw in (
        "CALL_CREDIT", "PUT_CREDIT", "balanced_structure_premium_valid",
        "SL_150_PERCENT_LOSS",
    ):
        assert raw not in text


def test_no_active_profile_message():
    assert om.strategy_synopsis(None) == \
        "No active strategy selected. Open Run Strategy to choose one."


def test_backtest_run_narrative_mentions_core_metrics_and_top_blocker():
    text = om.backtest_run_narrative(
        run_config={
            "symbol": "SPX",
            "profiles": ["eod_5k_dynamic_sl150_no_tp"],
            "dte": "0DTE",
            "counters": {"dates_evaluated": 20, "candidates": 253},
        },
        metrics={
            "contracts": 1,
            "starting_balance": 10000.0,
            "ending_balance": 9922.5,
            "total_pnl_dollars": -77.5,
            "return_pct": -0.775,
            "max_drawdown_pct": 3.48,
            "win_rate": 0.67,
            "total_trades": 12,
            "tp_count": 0,
            "sl_count": 4,
            "eod_count": 8,
        },
        explainability={"top_reasons": [{"reason": "score_below_threshold", "count": 12}]},
    )
    assert "SPX backtest" in text
    assert "selected 12 trades" in text
    assert "-$77.50" in text
    assert "-0.78%" in text
    assert "3.48%" in text
    assert "67.00%" in text
    assert "Score Below Threshold" in text


def test_backtest_run_narrative_no_trades_mentions_blockers():
    text = om.backtest_run_narrative(
        run_config={"symbol": "SPX", "profiles": ["observe_dynamic_5k"],
                    "counters": {"dates_evaluated": 5, "candidates": 10}},
        metrics={"total_trades": 0, "contracts": 1, "starting_balance": 10000.0},
        explainability={"top_reasons": [{"reason": "no_selection", "count": 5}]},
    )
    assert "No trades were selected" in text
    assert "No Selection" in text


def test_strategy_synopsis_placement_wired_to_pages():
    assert "_render_strategy_synopsis(_active_profile, context=\"live\")" in _SRC
    assert "_render_strategy_synopsis(sel_dict, context=\"builder\")" in _SRC
    assert "_render_strategy_synopsis(base, context=\"builder\")" in _SRC
    assert "_render_strategy_synopsis(_sel_runner_dict, context=\"run\")" in _SRC
    assert "om.multi_strategy_synopsis(_bt_profile_rows, context=\"backtest\")" in _SRC
    assert "_render_strategy_synopsis(_bt_profile_row, context=\"backtest\")" in _SRC
    assert "Strategy context" in _SRC and "context=\"portfolio\"" in _SRC
    assert "_render_strategy_synopsis(_latest_profile_ctx, context=\"stats\")" in _SRC
    assert "selected strategy " in _SRC and "remains:" in _SRC


def test_backtests_run_summary_wired():
    assert "om.backtest_run_narrative(" in _SRC
    assert "**Run Summary**" in _SRC


def test_no_execution_surface_introduced():
    for rel in ("src/app/operator_mode.py", "src/app/streamlit_main.py"):
        text = (_REPO / rel).read_text(encoding="utf-8").lower()
        for token in ("submit_order", "place_order", "preview_order", "create_order",
                      "order_preview", "execute_trade", "broker."):
            assert token not in text, f"{rel} contains {token!r}"
