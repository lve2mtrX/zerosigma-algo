"""Phase 11A deterministic backtest learning and learned optimization grid."""

from __future__ import annotations

import json
from pathlib import Path

from src.backtesting import learning, optimization
from src.backtesting.replay_runner import BacktestResult

_REPO = Path(__file__).resolve().parents[1]
_UI = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def _candidate(
    date: str,
    *,
    profile: str,
    side: str,
    threshold: str,
    credit: float,
    distance: float,
    selected: bool,
    corridor: bool = True,
    tier: int = 1,
) -> dict[str, object]:
    return {
        "symbol": "SPX",
        "date": date,
        "dte": "0DTE",
        "profile_id": profile,
        "preset_kind": "control" if "control" in profile else "dynamic",
        "entry_target": "11:00",
        "entry_timestamp": f"{date}T11:00:00",
        "side": side,
        "threshold": threshold,
        "spot": 6000.0,
        "short_strike": 6020.0 if side == "CALL_CREDIT" else 5980.0,
        "long_strike": 6025.0 if side == "CALL_CREDIT" else 5975.0,
        "corridor_valid": corridor,
        "active_wds": 0.8,
        "raw_wds": 0.8,
        "wds_tier": tier,
        "dominant_wing_side": "CALL_FLOOR",
        "gamma_regime": "positive",
        "gamma_relationship": "spot_between_gamma",
        "primary_gamma": 6010.0,
        "secondary_gamma": 5990.0,
        "entry_credit_points": credit,
        "max_risk_points": 5.0 - credit,
        "reward_risk": credit / (5.0 - credit),
        "distance_from_spot_to_short": distance,
        "score": 0.75,
        "selector_score": 0.70,
        "selector_score_components": json.dumps({"premium": 0.8, "distance": 0.7}),
        "selected_trade": selected,
    }


def _result() -> BacktestResult:
    candidates = [
        _candidate(
            "2026-06-01", profile="morning_5k_call_tp75_control",
            side="CALL_CREDIT", threshold="5k", credit=1.2, distance=25, selected=True,
        ),
        _candidate(
            "2026-06-02", profile="morning_5k_dynamic_tp75",
            side="PUT_CREDIT", threshold="5k", credit=1.6, distance=8, selected=True,
            corridor=False, tier=3,
        ),
        _candidate(
            "2026-06-03", profile="morning_2k_dynamic_no_tp",
            side="CALL_CREDIT", threshold="2k", credit=0.8, distance=55, selected=False,
        ),
    ]
    trades = [
        {
            **candidates[0],
            "contracts": 1,
            "tp_mode": "TP75",
            "sl_mode": "SL150",
            "exit_reason": "TP",
            "hold_minutes": 30,
            "pnl_dollars": 90.0,
        },
        {
            **candidates[1],
            "contracts": 1,
            "tp_mode": "TP75",
            "sl_mode": "SL150",
            "exit_reason": "SL",
            "hold_minutes": 20,
            "pnl_dollars": -240.0,
        },
    ]
    no_trades = [{
        "symbol": "SPX",
        "date": "2026-06-03",
        "dte": "0DTE",
        "profile_id": "morning_2k_dynamic_no_tp",
        "entry_target": "11:00",
        "reason": "no_selection",
        "first_blocker": "score_below_threshold",
        "candidate_count": 1,
        "eligible_candidate_count": 0,
        "score_filtered_count": 1,
        "risk_filtered_count": 0,
        "quote_filtered_count": 0,
        "selector_filtered_count": 1,
    }]
    return BacktestResult(
        run_config={
            "symbol": "SPX",
            "dte": "0DTE",
            "profiles": [
                "morning_5k_call_tp75_control",
                "morning_5k_dynamic_tp75",
                "morning_2k_dynamic_no_tp",
            ],
            "starting_balance": 10000.0,
            "contracts": 1,
            "run_label": "pytest",
        },
        candidates=candidates,
        trades=trades,
        no_trade_reasons=no_trades,
        counters={
            "dates_evaluated": 3,
            "candidates": 3,
            "selected_trades": 2,
            "no_trade_rows": 1,
        },
    )


def test_credit_and_distance_buckets_are_deterministic():
    assert learning.credit_bucket(0.49) == "<0.50"
    assert learning.credit_bucket(1.0) == "1.00-1.49"
    assert learning.credit_bucket(2.0) == "2.00+"
    assert learning.distance_bucket(9.99) == "<10"
    assert learning.distance_bucket(25) == "25-49.99"
    assert learning.distance_bucket(50) == "50+"


def test_feature_tables_extract_trades_candidates_and_no_trade_rows():
    trades, candidates, no_trades = learning.extract_feature_tables(_result())
    assert len(trades) == 2 and len(candidates) == 3 and len(no_trades) == 1
    assert trades[0]["credit_bucket"] == "1.00-1.49"
    assert trades[0]["distance_bucket"] == "25-49.99"
    assert trades[0]["selector_component_premium"] == 0.8
    assert trades[0]["spot_relation_primary_gamma"] == "below"
    assert trades[0]["outcome"] == "win"
    assert candidates[1]["outcome"] == "loss"
    assert no_trades[0]["top_blocker"] == "score_below_threshold"


def test_feature_summaries_and_no_trade_blockers_are_generated():
    result = learning.run_learning(_result())
    side = {row["bucket"]: row for row in result.performance_tables["side"]}
    assert side["CALL_CREDIT"]["total_pnl_dollars"] == 90.0
    assert side["PUT_CREDIT"]["expectancy_dollars"] == -240.0
    corridor = {row["bucket"]: row for row in result.performance_tables["corridor"]}
    assert corridor["True"]["trade_count"] == 1
    assert corridor["False"]["trade_count"] == 1
    assert result.no_trade_blockers[0]["blocker"] == "score_below_threshold"
    assert result.no_trade_blockers[0]["potential_trade_slots_if_removed"] == 1


def test_hypotheses_and_learned_grid_are_deterministic_and_bounded(tmp_path):
    one = learning.run_learning(_result())
    two = learning.run_learning(_result())
    assert one.hypotheses == two.hypotheses
    assert one.learned_parameter_sets == two.learned_parameter_sets
    assert 4 <= len(one.learned_parameter_sets) <= learning.MAX_LEARNED_PARAMETER_SETS
    output = tmp_path / "research"
    learning.write_learning_reports(one, [output])
    generated = optimization.build_parameter_grid(
        "learned_hypotheses",
        optimizer_run_id="pytest",
        max_combinations=8,
        from_research=output,
    )
    assert len(generated) <= 8
    assert all(row.profile.research_only for row in generated)
    assert {row.profile.base_profile_id for row in generated} >= {
        "morning_5k_call_tp75_control",
        "morning_2k_call_no_tp_control",
        "morning_5k_dynamic_tp75",
        "morning_2k_dynamic_no_tp",
    }
    assert sum(bool(row.parameters["research_benchmark"]) for row in generated) == 4
    assert optimization._promotion(
        {"profile_kind": "benchmark"}, []
    )[0] == "Comparison Baseline"
    assert optimization._promotion(
        {"profile_kind": "control"}, []
    )[0] == "Benchmark Control"
    repeated = optimization.build_parameter_grid(
        "learned_hypotheses",
        optimizer_run_id="pytest-two",
        max_combinations=8,
        from_research=output,
    )
    assert [row.parameter_hash for row in generated] == [
        row.parameter_hash for row in repeated
    ]


def test_learning_writes_all_required_outputs(tmp_path):
    result = learning.run_learning(_result())
    learning.write_learning_reports(result, [tmp_path])
    for name in (
        "backtest_assumption_audit.md",
        "trade_feature_table.csv",
        "candidate_feature_table.csv",
        "no_trade_feature_table.csv",
        "feature_performance_summary.csv",
        "by_entry_window.csv",
        "by_side.csv",
        "by_threshold.csv",
        "by_wds_tier.csv",
        "by_corridor.csv",
        "by_credit_bucket.csv",
        "by_distance_bucket.csv",
        "by_exit_reason.csv",
        "by_month.csv",
        "by_profile_family.csv",
        "no_trade_blocker_summary.csv",
        "generated_strategy_hypotheses.md",
        "generated_strategy_hypotheses.json",
        "run_config.json",
    ):
        assert (tmp_path / name).is_file()
    config = json.loads((tmp_path / "run_config.json").read_text(encoding="utf-8"))
    assert config["no_broker"] is True
    assert config["live_strategy_behavior_changed"] is False


def test_ui_and_sources_surface_learning_review_without_live_paths():
    assert "Learning Review" in _UI
    assert "learned_hypotheses" in _UI
    assert "Feature buckets describe historical association, not causality." in _UI
    source = (_REPO / "src" / "backtesting" / "learning.py").read_text(
        encoding="utf-8"
    ).lower()
    cli = (_REPO / "scripts" / "backtest_learn.py").read_text(encoding="utf-8").lower()
    for token in (
        "submit_order", "place_order", "preview_order", "execute_trade",
        "import httpx", "from src.selector", "from src.risk",
    ):
        assert token not in source
        assert token not in cli
