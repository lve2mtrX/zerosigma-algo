"""Phase 11B profitability attribution and bounded learned-strategy tightening."""

from __future__ import annotations

from pathlib import Path

from src.backtesting import learning, optimization
from src.backtesting.phase11b_review import write_phase11b_review
from tests.test_phase10g_optimization import _root_with_dates
from tests.test_phase11a_learning import _result

_REPO = Path(__file__).resolve().parents[1]
_UI = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def test_profitability_attribution_interactions_drivers_and_filters_are_generated(tmp_path):
    result = learning.run_learning(_result())
    assert result.profitability_attribution
    assert result.feature_interactions
    assert {row["feature"] for row in result.feature_interactions} >= {
        "side_x_distance",
        "side_x_credit",
        "corridor_x_wds",
        "tp_sl_x_distance",
    }
    assert result.filter_impacts
    exclude_put = next(row for row in result.filter_impacts if row["filter"] == "exclude_put_credit")
    assert exclude_put["trades_removed"] == 1
    assert exclude_put["expectancy_after_dollars"] == 90.0
    assert exclude_put["research_only_warning"]
    learning.write_learning_reports(result, [tmp_path])
    for name in (
        "profitability_attribution_summary.csv",
        "profitability_attribution_summary.md",
        "feature_interaction_matrix.csv",
        "win_driver_matrix.csv",
        "loss_driver_matrix.csv",
        "filter_impact_analysis.csv",
        "filter_impact_analysis.md",
        "strategy_robustness_scorecard.csv",
        "strategy_robustness_scorecard.md",
    ):
        assert (tmp_path / name).is_file()


def test_phase11b_grids_are_deterministic_bounded_readable_and_benchmarked():
    limits = {
        "learned_call_only_expansion": 96,
        "learned_call_only_robustness": 48,
        "learned_dynamic_repair": 48,
    }
    for grid, limit in limits.items():
        one = optimization.build_parameter_grid(grid, optimizer_run_id="one", max_combinations=0)
        two = optimization.build_parameter_grid(grid, optimizer_run_id="two", max_combinations=0)
        assert len(one) <= limit
        assert [row.parameter_hash for row in one] == [row.parameter_hash for row in two]
        assert all(row.profile.research_only for row in one)
        assert all(row.profile.profile_name and row.profile.notes and row.synopsis for row in one)
        assert any(row.parameters.get("research_benchmark") for row in one)
    dynamic = optimization.build_parameter_grid(
        "learned_dynamic_repair", optimizer_run_id="dynamic", max_combinations=48
    )
    assert {row.parameters["put_gate"] for row in dynamic if not row.parameters.get("research_benchmark")} >= {
        "active_corridor",
        "wds_tier_1_2",
        "distance_25",
        "credit_1_distance_25",
    }


def test_phase11b_optimizer_scorecard_has_split_and_slippage_review(tmp_path):
    config = optimization.OptimizationConfig(
        symbol="SPX",
        dte=0,
        all_data=True,
        grid="learned_call_only_robustness",
        run_label="pytest",
        max_combinations=3,
        trading_root=str(_root_with_dates(tmp_path)),
    )
    result = optimization.run_optimization(config, optimizer_run_id="phase11b-pytest")
    assert result.strategy_robustness_scorecard
    assert not result.promotion_candidates
    assert all(row["promotion_status"] != "Forward Paper Candidate" for row in result.rankings)
    assert {row["status"] for row in result.strategy_robustness_scorecard} <= {
        "Research Candidate",
        "Needs More Data",
        "Fragile / Overfit Risk",
        "Reject",
        "Benchmark Only",
    }
    for row in result.strategy_robustness_scorecard:
        assert "positive_validation_holdout_splits" in row
        assert "slippage_haircut_expectancy_dollars" in row
        assert row["automatic_forward_paper_promotion"] is False


def test_phase11b_review_writes_cross_grid_summary(tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    (research / "filter_impact_analysis.csv").write_text(
        "filter,expectancy_delta_dollars\nexclude_put_credit,20\n",
        encoding="utf-8",
    )
    run_dirs: dict[str, Path] = {}
    for index, label in enumerate(
        ("call_only_expansion", "call_only_robustness", "dynamic_repair"), start=1
    ):
        directory = tmp_path / label
        directory.mkdir()
        directory.joinpath("rankings.csv").write_text(
            "rank,profile_id,profile_name,profile_kind,base_profile_id,"
            "train_total_pnl_dollars,validation_total_pnl_dollars,holdout_total_pnl_dollars,"
            "validation_expectancy_dollars,holdout_expectancy_dollars,"
            "validation_total_trades,holdout_total_trades\n"
            f"1,r{index},Research {index},research,base,100,50,25,10,5,8,4\n"
            "2,b,Control,control,morning_5k_call_tp75_control,50,20,10,5,2,8,4\n",
            encoding="utf-8",
        )
        directory.joinpath("strategy_robustness_scorecard.csv").write_text(
            "profile_id,status,split_consistency,slippage_haircut_robustness,warnings\n"
            f"r{index},Research Candidate,3/3 positive,positive,\n",
            encoding="utf-8",
        )
        run_dirs[label] = directory
    write_phase11b_review(run_dirs, output_dir=research)
    assert (research / "phase11b_smoke_summary.md").is_file()
    assert "Best call-only expansion candidate" in (
        research / "phase11b_smoke_summary.md"
    ).read_text(encoding="utf-8")
    assert (research / "dynamic_repair_results.csv").is_file()


def test_phase11b_ui_cli_and_sources_remain_research_only():
    for label in (
        "What is making money?",
        "What is losing money?",
        "Best filters by impact",
        "Worst filters / false positives",
        "Strongest feature interactions",
        "Call-only expansion results",
        "Dynamic repair results",
        "Robustness scorecard",
        "Recommended next test",
        "Warnings",
        "learned_call_only_expansion",
        "learned_call_only_robustness",
        "learned_dynamic_repair",
    ):
        assert label in _UI
    sources = [
        _REPO / "src" / "backtesting" / "learning.py",
        _REPO / "src" / "backtesting" / "optimization.py",
        _REPO / "src" / "backtesting" / "phase11b_review.py",
        _REPO / "scripts" / "backtest_phase11b_review.py",
    ]
    for path in sources:
        source = path.read_text(encoding="utf-8").lower()
        for token in ("submit_order", "place_order", "preview_order", "execute_trade", "import httpx"):
            assert token not in source
