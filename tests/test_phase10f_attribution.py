"""Phase 10F dynamic selector attribution and control-edge audit."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from src.backtesting import attribution as attr
from src.backtesting import comparison as comp
from src.backtesting.replay_runner import BacktestResult, run_backtest
from tests.test_phase10b_backtest import _make_root

_REPO = Path(__file__).resolve().parents[1]
_UI = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def _trade(
    profile_id: str,
    kind: str,
    side: str,
    pnl: float,
    *,
    date: str,
    threshold: str = "5k",
) -> dict:
    return {
        "profile_id": profile_id,
        "preset_kind": kind,
        "symbol": "SPX",
        "date": date,
        "dte": "0DTE",
        "threshold": threshold,
        "entry_target": "11:00",
        "entry_timestamp": f"{date}T11:00:00",
        "side": side,
        "pnl_dollars": pnl,
        "exit_reason": "TP" if pnl > 0 else "SL",
        "entry_credit_points": 0.85,
        "distance_from_spot_to_short": 20.0,
        "corridor_valid": True,
        "wds_tier": 2,
        "active_wds": 0.60,
        "gamma_regime": "negative",
        "gamma_relationship": "spot_between_gamma",
        "selected_trade": True,
    }


def _attribution_result() -> BacktestResult:
    dynamic = "morning_5k_dynamic_tp75"
    control = "morning_5k_call_tp75_control"
    dynamic_put = _trade(dynamic, "dynamic", "PUT_CREDIT", -100.0, date="2026-06-01")
    dynamic_call = _trade(dynamic, "dynamic", "CALL_CREDIT", 50.0, date="2026-06-02")
    control_one = _trade(control, "control", "CALL_CREDIT", 125.0, date="2026-06-01")
    control_two = _trade(control, "control", "CALL_CREDIT", 75.0, date="2026-06-02")
    component_selected = (
        '{"premium_score": 1.0, "distance_safety_score": 0.0, '
        '"structure_score": 0.5, "total": 2.0}'
    )
    component_opposite = (
        '{"premium_score": 0.0, "distance_safety_score": 1.0, '
        '"structure_score": 0.5, "total": 2.0}'
    )
    attribution = [
        {
            "date": "2026-06-01",
            "profile_id": dynamic,
            "threshold": "5k",
            "entry_target": "11:00",
            "selected_side": "PUT_CREDIT",
            "opposite_side": "CALL_CREDIT",
            "selected_pnl_dollars": -100.0,
            "opposite_pnl_dollars": 80.0,
            "selected_exit_reason": "SL",
            "opposite_exit_reason": "TP",
            "selected_outcome": "loss",
            "opposite_outcome": "win",
            "opposite_available": True,
            "opposite_outcome_simulated": True,
            "opposite_would_have_done_better": True,
            "selected_selector_score_components": component_selected,
            "opposite_selector_score_components": component_opposite,
            "selected_corridor_valid": True,
            "selected_wds_tier": 2,
            "selected_active_wds": 0.60,
            "gamma_regime": "negative",
            "selection_reason": "Selected PUT_CREDIT for premium.",
        },
        {
            "date": "2026-06-02",
            "profile_id": dynamic,
            "threshold": "5k",
            "entry_target": "11:00",
            "selected_side": "CALL_CREDIT",
            "opposite_side": "PUT_CREDIT",
            "selected_pnl_dollars": 50.0,
            "opposite_pnl_dollars": -25.0,
            "selected_exit_reason": "TP",
            "opposite_exit_reason": "SL",
            "selected_outcome": "win",
            "opposite_outcome": "loss",
            "opposite_available": True,
            "opposite_outcome_simulated": True,
            "opposite_would_have_done_better": False,
            "selected_selector_score_components": component_opposite,
            "opposite_selector_score_components": component_selected,
            "selected_corridor_valid": True,
            "selected_wds_tier": 2,
            "selected_active_wds": 0.60,
            "gamma_regime": "negative",
            "selection_reason": "Selected CALL_CREDIT for distance.",
        },
    ]
    return BacktestResult(
        run_config={
            "symbol": "SPX", "dte": "0DTE", "profiles": [dynamic, control],
            "starting_balance": 10000, "contracts": 1,
        },
        trades=[dynamic_put, dynamic_call, control_one, control_two],
        candidates=[
            {**dynamic_put, "selected_trade": True},
            {**dynamic_call, "selected_trade": True},
        ],
        dynamic_side_attribution=attribution,
        no_trade_reasons=[{
            "date": "2026-06-03", "profile_id": dynamic, "candidate_count": 2,
            "quote_filtered_count": 1, "risk_filtered_count": 0,
            "first_blocker": "quote_validation_required",
        }],
        counters={"dates_evaluated": 3, "selected_trades": 4},
    )


def test_replay_generates_dynamic_selected_vs_opposite_simulation(tmp_path):
    result = run_backtest(
        symbol="SPX",
        profile_ids=["morning_5k_dynamic_tp75"],
        trading_root=str(_make_root(tmp_path)),
        dte=0,
    )
    assert len(result.dynamic_side_attribution) == len(result.trades)
    row = result.dynamic_side_attribution[0]
    assert row["selected_side"] != row["opposite_side"]
    assert row["opposite_available"] is True
    assert row["opposite_outcome_simulated"] is True
    assert row["selected_selector_score_components"]
    assert row["opposite_selector_score_components"]
    assert result.counters["dynamic_attribution_rows"] == len(result.dynamic_side_attribution)


def test_selected_side_split_and_dynamic_pnl_by_side():
    rows = {row["selected_side"]: row for row in attr.selected_side_summary(_attribution_result())}
    assert rows["PUT_CREDIT"]["trades"] == 1
    assert rows["PUT_CREDIT"]["win_rate"] == 0.0
    assert rows["PUT_CREDIT"]["total_pnl_dollars"] == -100.0
    assert rows["CALL_CREDIT"]["total_pnl_dollars"] == 50.0
    assert rows["CALL_CREDIT"]["average_pnl_dollars"] == 50.0


def test_dynamic_vs_opposite_and_failure_taxonomy_are_deterministic():
    result = _attribution_result()
    comparisons = attr.dynamic_vs_best_opposite(result)
    put = next(row for row in comparisons if row["selected_side"] == "PUT_CREDIT")
    assert put["opposite_opportunity_cost_dollars"] == 180.0
    assert put["matching_call_control_pnl_dollars"] == 125.0
    assert put["selected_advantage_component"] == "premium_score"
    taxonomy = attr.dynamic_failure_taxonomy(result)
    assert taxonomy == attr.dynamic_failure_taxonomy(result)
    buckets = {row["failure_bucket"] for row in taxonomy}
    assert "chose put-credit and put side lost" in buckets
    assert "quote/risk validation filtered better side" in buckets
    narrative = attr.attribution_narrative(result)
    assert "top failure bucket was chose put-credit and put side lost" in narrative


def test_call_control_edge_audit_and_recommendations():
    result = _attribution_result()
    audit = attr.call_control_edge_summary(result)
    assert {"threshold", "credit_bucket", "distance_bucket", "day_of_week"} <= {
        row["dimension"] for row in audit
    }
    recommendations = attr.research_recommendations(result)
    labels = {row["recommendation"] for row in recommendations}
    assert "Consider testing dynamic-call-biased selector" in labels
    assert "Consider call-only as current live-paper benchmark while dynamic is revised" in labels
    assert all(row["research_only"] is True for row in recommendations)


def test_comparison_writer_generates_phase10f_outputs_without_mutating_result(tmp_path):
    result = _attribution_result()
    before = deepcopy(result.trades)
    comp.write_comparison_reports(result, [tmp_path], stamp="pytest")
    assert result.trades == before
    for name in (
        "dynamic_side_attribution.csv", "selected_side_summary.csv",
        "dynamic_vs_best_opposite.csv", "call_control_edge_summary.csv",
        "call_control_winners_losers.csv", "dynamic_failure_taxonomy.csv",
        "dynamic_failure_summary.csv", "research_recommendations.csv",
        "attribution_summary.json", "attribution_summary.md",
    ):
        assert (tmp_path / name).is_file()


def test_promotion_update_keeps_controls_benchmark_only_and_dynamic_tuning_only():
    positive = {
        "total_trades": 20, "expectancy_dollars": 10, "profit_factor": 1.2,
        "max_drawdown_pct": 5,
    }
    negative = {**positive, "expectancy_dollars": -1, "profit_factor": 0.9}
    assert comp.promotion_label(positive, profile_kind="control")[0] == (
        "Control Positive / Comparison Only"
    )
    assert comp.promotion_label(negative, profile_kind="dynamic")[0] == (
        "Watchlist / Needs Tuning"
    )


def test_attribution_ui_and_no_execution_or_selector_math_paths():
    for text in (
        "Why did dynamic underperform?", "Dynamic Call Credit", "Dynamic Put Credit",
        "Dynamic vs Controls P&L", "Best Opposite Available", "Top Failure Buckets",
        "Call-Control Edge", "Research Recommendations",
    ):
        assert text in _UI
    attribution_source = (_REPO / "src" / "backtesting" / "attribution.py").read_text(
        encoding="utf-8"
    ).lower()
    comparison_source = (_REPO / "src" / "backtesting" / "comparison.py").read_text(
        encoding="utf-8"
    ).lower()
    assert "select_daily_trade" not in attribution_source
    for token in (
        "submit_order", "place_order", "preview_order", "create_order",
        "execute_trade", "tastytrade_provider", "import httpx",
    ):
        assert token not in attribution_source
        assert token not in comparison_source
