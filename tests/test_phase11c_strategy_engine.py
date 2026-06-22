"""Phase 11C archetype-neutral strategy engine and Optuna research harness."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from scripts import backtest_optuna
from src.backtesting import learning
from src.backtesting.optuna_optimizer import (
    OptunaResult,
    robustness_objective,
    write_optuna_outputs,
)
from src.strategy_engine.candidates import build_credit_spread, build_long_option
from src.strategy_engine.evaluator import evaluate_candidates
from src.strategy_engine.regime_compatibility import (
    CompatibilityLabel,
    RegimeContext,
    evaluate_regime_compatibility,
)
from src.strategy_engine.risk_quality import (
    EvaluationStatus,
    RiskQualityLabel,
    evaluate_risk_quality,
)
from src.strategy_engine.types import (
    LegAction,
    OptionRight,
    StrategyArchetype,
    StrategyLeg,
)
from tests.test_phase11a_learning import _result

_REPO = Path(__file__).resolve().parents[1]


def _leg(strike: float, right: OptionRight, action: LegAction) -> StrategyLeg:
    return StrategyLeg(
        option_symbol=f"SPX-{right}-{strike}", strike=strike, right=right,
        action=action, bid=1.95, ask=2.05, mid=2.00,
    )


def _credit_candidate(
    credit: float,
    *,
    minutes: int = 60,
    distance: float = 25.0,
    quote_quality: str = "usable",
    stop_loss_multiple: float = 1.0,
):
    return build_credit_spread(
        timestamp=datetime(2026, 6, 21, 11), symbol="SPX", dte=0,
        expiry="2026-06-21", archetype=StrategyArchetype.CALL_CREDIT_SPREAD,
        short_leg=_leg(6000, OptionRight.CALL, LegAction.SELL),
        long_leg=_leg(6005, OptionRight.CALL, LegAction.BUY),
        credit=credit, contracts=1, time_to_close_minutes=minutes,
        distance_to_short_strike=distance, quote_quality=quote_quality,
        stop_loss_multiple=stop_loss_multiple, thesis="Contained-regime call credit test.",
    )


def test_strategy_archetypes_include_credit_long_and_debit_placeholders():
    assert {item.value for item in StrategyArchetype} == {
        "CALL_CREDIT_SPREAD", "PUT_CREDIT_SPREAD", "LONG_CALL", "LONG_PUT",
        "CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD",
    }


def test_credit_spread_payoff_and_tiny_credit_rejection():
    candidate = _credit_candidate(0.15)
    assert candidate.width == 5
    assert candidate.max_profit == 15
    assert candidate.max_loss == 485
    assert candidate.credit_pct_of_width == 0.03
    risk = evaluate_risk_quality(candidate)
    assert risk.status == EvaluationStatus.REJECT
    assert risk.label == RiskQualityLabel.TOO_CHEAP_FOR_RISK
    assert "credit_pct_of_width_too_low" in risk.reason_codes


def test_tiny_credit_is_eod_exception_only_under_strict_conditions():
    allowed = evaluate_risk_quality(_credit_candidate(0.15, minutes=10, distance=30))
    assert allowed.status == EvaluationStatus.WARN
    assert allowed.label == RiskQualityLabel.EOD_EXCEPTION_ONLY
    assert allowed.eod_exception_candidate is True
    assert "eod_exception_expectancy_unproven" in allowed.reason_codes
    too_early = evaluate_risk_quality(_credit_candidate(0.15, minutes=30, distance=30))
    bad_quote = evaluate_risk_quality(
        _credit_candidate(0.15, minutes=10, distance=30, quote_quality="poor")
    )
    assert too_early.status == EvaluationStatus.REJECT
    assert bad_quote.status == EvaluationStatus.REJECT


def test_reasonable_credit_and_controlled_stop_pass_risk_quality():
    candidate = _credit_candidate(1.50, stop_loss_multiple=1.0)
    risk = evaluate_risk_quality(candidate)
    assert candidate.max_profit == 150
    assert candidate.max_loss == 350
    assert candidate.credit_pct_of_width == 0.30
    assert risk.stop_loss_dollar_risk == 150
    assert risk.credit_to_stop_risk == 1.0
    assert risk.status == EvaluationStatus.PASS
    assert risk.label == RiskQualityLabel.GOOD


def test_long_call_and_put_debit_risk_and_regime_compatibility():
    call = build_long_option(
        timestamp=datetime(2026, 6, 21, 11), symbol="SPX", dte=0,
        expiry="2026-06-21", archetype=StrategyArchetype.LONG_CALL,
        leg=_leg(6000, OptionRight.CALL, LegAction.BUY), debit=2.0,
        quote_quality="usable", target_move_required=12,
        invalidation_level=5985, minimum_target_multiple=2.0,
    )
    put = build_long_option(
        timestamp=datetime(2026, 6, 21, 11), symbol="SPX", dte=0,
        expiry="2026-06-21", archetype=StrategyArchetype.LONG_PUT,
        leg=_leg(6000, OptionRight.PUT, LegAction.BUY), debit=1.5,
        quote_quality="usable", target_move_required=10,
        invalidation_level=6015, minimum_target_multiple=2.0,
    )
    assert call.debit_at_risk == 200 and put.debit_at_risk == 150
    call_regime = evaluate_regime_compatibility(
        call, RegimeContext(regime_label="upside_acceleration")
    )
    put_regime = evaluate_regime_compatibility(
        put, RegimeContext(regime_label="downside_acceleration")
    )
    assert call_regime.label == CompatibilityLabel.COMPATIBLE
    assert put_regime.label == CompatibilityLabel.COMPATIBLE
    assert evaluate_risk_quality(call, regime=call_regime).status == EvaluationStatus.PASS
    assert evaluate_risk_quality(put, regime=put_regime).status == EvaluationStatus.PASS


def test_regime_conflict_rejects_and_evaluator_sorts_candidates():
    good = _credit_candidate(1.50)
    tiny = _credit_candidate(0.15)
    contexts = {
        good.candidate_id: RegimeContext(regime_label="contained"),
        tiny.candidate_id: RegimeContext(regime_label="upside_acceleration"),
    }
    batch = evaluate_candidates([tiny, good], regime_contexts=contexts)
    assert batch.ranked[0].candidate.candidate_id == good.candidate_id
    assert good.candidate_id in {row.candidate.candidate_id for row in batch.accepted}
    assert tiny.candidate_id in {row.candidate.candidate_id for row in batch.rejected}
    assert "call_credit_hostile_upside_acceleration" in batch.rejection_reasons


def test_learning_tables_include_risk_quality_fields_and_summaries(tmp_path):
    result = learning.run_learning(_result())
    assert result.trade_features[0]["archetype"] == "CALL_CREDIT_SPREAD"
    assert "credit_pct_of_width" in result.trade_features[0]
    assert "risk_quality_label" in result.candidate_features[0]
    assert "regime_compatibility_label" in result.candidate_features[0]
    assert result.performance_tables["risk_quality"]
    assert result.performance_tables["credit_pct_of_width"]
    learning.write_learning_reports(result, [tmp_path])
    for name in (
        "by_archetype.csv", "by_risk_quality.csv", "by_credit_pct_of_width.csv",
        "by_credit_to_stop_risk.csv", "by_eod_exception.csv",
        "by_regime_compatibility.csv", "risk_quality_rejection_summary.csv",
    ):
        assert (tmp_path / name).is_file()


def test_optuna_objective_penalizes_low_sample_and_poor_risk_reward():
    robust = {
        "validation_expectancy_dollars": 20, "holdout_expectancy_dollars": 10,
        "validation_profit_factor": 1.5, "holdout_profit_factor": 1.2,
        "validation_total_trades": 20, "holdout_total_trades": 10,
        "validation_max_drawdown_pct": 4, "holdout_max_drawdown_pct": 5,
        "fill_haircut_expectancy_dollars": 5, "positive_validation_holdout_splits": 3,
        "validation_one_day_pnl_concentration": 0.1, "holdout_one_day_pnl_concentration": 0.1,
        "validation_month_concentration": 0.2, "holdout_month_concentration": 0.2,
        "avg_risk_reward": 0.3, "avg_credit_pct_of_width": 0.25,
    }
    weak = {**robust, "validation_total_trades": 2, "holdout_total_trades": 1,
            "avg_risk_reward": 0.02, "avg_credit_pct_of_width": 0.03}
    robust_score, _ = robustness_objective(robust)
    weak_score, weak_components = robustness_objective(weak)
    assert robust_score > weak_score
    assert weak_components["low_trade_count_penalty"] < 0
    assert weak_components["poor_risk_reward_penalty"] < 0
    empty_score, empty_components = robustness_objective(
        {**robust, "validation_total_trades": 0}
    )
    assert empty_score < robust_score
    assert empty_components["empty_validation_or_holdout_penalty"] == -50
    negative_score, negative_components = robustness_objective(
        {**robust, "validation_expectancy_dollars": -100}
    )
    assert negative_score < robust_score
    assert negative_components["negative_validation_penalty"] == -30
    assert negative_components["validation_expectancy_component"] == -20


def test_optuna_outputs_and_cli_missing_dependency_behavior(tmp_path, capsys, monkeypatch):
    result = OptunaResult(
        run_config={"research_only": True},
        trials=[{"trial_number": 0, "objective_value": 1.0}],
        best_params={"min_credit": 1.5},
        best_trials_markdown="# Optuna Best Trials\n",
        param_importance=[{"parameter": "min_credit", "importance": 1.0}],
        robustness_markdown="# Optuna Robustness Summary\n",
    )
    write_optuna_outputs(result, [tmp_path])
    for name in (
        "optuna_trials.csv", "optuna_best_params.json", "optuna_best_trials.md",
        "optuna_param_importance.csv", "optuna_robustness_summary.md", "run_config.json",
    ):
        assert (tmp_path / name).is_file()
    monkeypatch.setattr(
        backtest_optuna,
        "run_optuna",
        lambda _config: (_ for _ in ()).throw(RuntimeError("Optuna is not installed")),
    )
    rc = backtest_optuna.main([
        "--symbol", "SPX", "--dte", "0", "--trials", "1",
        "--timeout-seconds", "1", "--run-label", "pytest",
        "--trading-root", str(tmp_path / "missing"),
    ])
    assert rc == 1
    assert "Optuna is not installed" in capsys.readouterr().out


def test_phase11c_ui_and_sources_have_no_order_paths():
    ui = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")
    assert "Strategy Engine / Risk Quality" in ui
    assert "Optuna Research" in ui
    assert "too cheap for its maximum risk" in ui
    for path in (
        _REPO / "src" / "strategy_engine" / "evaluator.py",
        _REPO / "src" / "backtesting" / "optuna_optimizer.py",
        _REPO / "scripts" / "backtest_optuna.py",
    ):
        source = path.read_text(encoding="utf-8").lower()
        for token in ("submit_order", "place_order", "preview_order", "execute_trade", "import httpx"):
            assert token not in source
