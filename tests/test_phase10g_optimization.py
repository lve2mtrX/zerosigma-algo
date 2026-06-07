"""Phase 10G repeatable optimization harness and walk-forward research."""

from __future__ import annotations

from pathlib import Path

from src.backtesting import optimization as opt
from tests.test_phase10b_backtest import _csv

_REPO = Path(__file__).resolve().parents[1]
_UI = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def _root_with_dates(tmp_path: Path, count: int = 10) -> Path:
    root = tmp_path / "Trading"
    directory = root / "TOS Data" / "Daily Exposures" / "SPX"
    directory.mkdir(parents=True)
    for day in range(1, count + 1):
        date = f"2026-06-{day:02d}"
        (directory / f"SPX_RAW_{date}.csv").write_text(_csv(date), encoding="utf-8")
    return root


def test_optimizer_grid_and_parameter_hash_are_deterministic():
    one = opt.build_parameter_grid(
        "core_morning", optimizer_run_id="run-one", max_combinations=6
    )
    two = opt.build_parameter_grid(
        "core_morning", optimizer_run_id="run-two", max_combinations=6
    )
    assert [row.parameters for row in one] == [row.parameters for row in two]
    assert [row.parameter_hash for row in one] == [row.parameter_hash for row in two]
    assert all(row.profile.research_only for row in one)
    assert all(row.profile.optimizer_run_id == "run-one" for row in one)
    assert all(row.synopsis for row in one)


def test_chronological_split_has_no_overlap_and_preserves_order():
    dates = [f"2026-01-{day:02d}" for day in range(1, 11)]
    split = opt.chronological_split(dates)
    assert split["train"] == dates[:6]
    assert split["validation"] == dates[6:8]
    assert split["holdout"] == dates[8:]
    assert not set(split["train"]) & set(split["validation"])
    assert not set(split["validation"]) & set(split["holdout"])
    assert split["train"][-1] < split["validation"][0] < split["holdout"][0]


def test_custom_chronological_split_is_deterministic():
    dates = [f"2026-01-{day:02d}" for day in range(1, 11)]
    split = opt.chronological_split(
        dates, train_end="2026-01-05", validation_end="2026-01-08"
    )
    assert split["train"][-1] == "2026-01-05"
    assert split["validation"] == dates[5:8]
    assert split["holdout"] == dates[8:]


def test_ranking_score_does_not_use_holdout():
    row = {
        "train_expectancy_dollars": 20,
        "validation_expectancy_dollars": 10,
        "validation_profit_factor": 1.4,
        "validation_return_pct": 2,
        "validation_total_trades": 10,
        "validation_max_drawdown_pct": 5,
        "holdout_expectancy_dollars": 1000,
        "holdout_total_pnl_dollars": 99999,
    }
    changed = {**row, "holdout_expectancy_dollars": -1000, "holdout_total_pnl_dollars": -99999}
    assert opt.robust_score(row) == opt.robust_score(changed)


def test_overfit_warning_and_promotion_labels_are_deterministic():
    row = {
        "profile_id": "research",
        "parameter_hash": "abc",
        "profile_kind": "dynamic",
        "train_expectancy_dollars": 25,
        "validation_expectancy_dollars": -5,
        "holdout_expectancy_dollars": -10,
        "validation_total_trades": 12,
        "holdout_total_trades": 5,
        "validation_profit_factor": 0.8,
        "validation_max_drawdown_pct": 5,
        "holdout_max_drawdown_pct": 5,
    }
    warnings = opt._warning_rows(row)
    assert {warning["warning"] for warning in warnings} >= {
        "train_positive_validation_negative",
        "train_validation_expectancy_degradation",
    }
    assert opt._promotion(row, warnings)[0] == "Reject / Overfit"
    assert opt._promotion(row, warnings) == opt._promotion(row, warnings)


def test_optimizer_writes_all_expected_outputs_and_reproducible_profiles(tmp_path):
    root = _root_with_dates(tmp_path)
    config = opt.OptimizationConfig(
        symbol="SPX",
        dte=0,
        all_data=True,
        grid="core_morning",
        run_label="pytest",
        max_combinations=2,
        trading_root=str(root),
    )
    result = opt.run_optimization(config, optimizer_run_id="pytest-run")
    output = tmp_path / "optimization"
    opt.write_optimization_reports(result, [output])
    for name in (
        "run_config.json", "parameter_grid.csv", "train_results.csv",
        "validation_results.csv", "holdout_results.csv", "combined_results.csv",
        "rankings.csv", "promotion_candidates.csv", "rejected_candidates.csv",
        "robustness_summary.csv", "overfit_warnings.csv", "narrative_summary.md",
    ):
        assert (output / name).is_file()
    generated = result.run_config["generated_profiles"]
    assert generated and generated[0]["profile"]["research_only"] is True
    assert generated[0]["parameter_hash"] == opt.parameter_hash(
        generated[0]["profile"]["base_profile_id"], generated[0]["parameters"]
    )
    assert result.run_config["holdout_used_for_ranking"] is False
    assert (output / "rejected_candidates.csv").read_text(encoding="utf-8").startswith("rank,")


def test_builtin_profiles_are_not_created_or_mutated_by_grid_generation():
    before = sorted(path.name for path in (_REPO / "profiles").glob("*.yaml"))
    opt.build_parameter_grid("dynamic_selector_experiments", optimizer_run_id="test", max_combinations=3)
    after = sorted(path.name for path in (_REPO / "profiles").glob("*.yaml"))
    assert after == before
    assert not any(name.startswith("opt_") for name in after)


def test_ui_and_cli_surface_optimization_lab_without_execution_paths():
    assert "Optimization Lab" in _UI
    assert "Optimization is research only. It does not change live strategy behavior." in _UI
    assert "Run Optimization" in _UI
    assert "Refresh Latest Optimization" in _UI
    source = (_REPO / "src" / "backtesting" / "optimization.py").read_text(
        encoding="utf-8"
    ).lower()
    cli = (_REPO / "scripts" / "backtest_optimize.py").read_text(encoding="utf-8").lower()
    for token in ("submit_order", "place_order", "preview_order", "execute_trade", "import httpx"):
        assert token not in source
        assert token not in cli
