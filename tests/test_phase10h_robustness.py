"""Phase 10H optimization robustness review and candidate freezing."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.app import cockpit_helpers as ch
from src.backtesting import optimization as opt
from src.backtesting import robustness_review as review
from src.backtesting.replay_runner import BacktestResult
from src.config.strategy_profiles import load_profile_file

_REPO = Path(__file__).resolve().parents[1]
_UI = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _optimization_run(tmp_path: Path, split: tuple[int, int, int], index: int) -> Path:
    generated = opt.build_parameter_grid(
        "core_morning", optimizer_run_id=f"run-{index}", max_combinations=1
    )[0]
    directory = tmp_path / f"run-{index}"
    directory.mkdir()
    config = {
        "optimizer_run_id": f"run-{index}",
        "grid": "core_morning",
        "symbol": "SPX",
        "dte": 0,
        "starting_balance": 10000,
        "contracts": 1,
        "train_pct": split[0],
        "validation_pct": split[1],
        "holdout_pct": split[2],
        "split_dates": {
            "train": ["2026-01-02"],
            "validation": ["2026-01-05"],
            "holdout": ["2026-01-06"],
        },
        "generated_profiles": [{
            "parameter_hash": generated.parameter_hash,
            "parameters": generated.parameters,
            "profile": generated.profile.to_dict(),
            "synopsis": generated.synopsis,
        }],
    }
    row = {
        "rank": 1,
        "profile_id": generated.profile.profile_id,
        "parameter_hash": generated.parameter_hash,
        "promotion_status": "Forward Paper Candidate",
        "validation_total_trades": 12,
        "validation_expectancy_dollars": 10 + index,
        "validation_profit_factor": 1.3,
        "validation_max_drawdown_pct": 4,
        "holdout_total_trades": 6,
        "holdout_expectancy_dollars": 5 + index,
        "holdout_max_drawdown_pct": 3,
        "severe_overfit_warning": False,
    }
    grid = {
        "profile_id": generated.profile.profile_id,
        "parameter_hash": generated.parameter_hash,
        **generated.parameters,
    }
    (directory / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    _write_csv(directory / "rankings.csv", [row])
    _write_csv(directory / "parameter_grid.csv", [grid])
    _write_csv(directory / "promotion_candidates.csv", [row])
    _write_csv(directory / "overfit_warnings.csv", [])
    return directory


def test_expanded_optimization_runs_load_and_split_sensitivity_is_generated(tmp_path):
    directories = [
        _optimization_run(tmp_path, (60, 20, 20), 1),
        _optimization_run(tmp_path, (50, 25, 25), 2),
        _optimization_run(tmp_path, (70, 15, 15), 3),
    ]
    runs = [review.load_optimization_run(directory) for directory in directories]
    split_rows, consistency, candidate_hash = review.split_sensitivity(runs)
    assert [row["split"] for row in split_rows] == ["60/20/20", "50/25/25", "70/15/15"]
    assert all(row["same_best_hash_across_splits"] for row in split_rows)
    assert consistency[0]["exact_hash_all_splits"] is True
    assert consistency[0]["nonnegative_holdout_splits"] == 3
    assert candidate_hash == consistency[0]["parameter_hash"]


def test_candidate_vs_control_benchmark_is_generated(monkeypatch):
    candidate = opt.build_parameter_grid(
        "core_morning", optimizer_run_id="benchmark", max_combinations=1
    )[0].profile

    def fake_run_backtest(**kwargs):
        profiles = kwargs["profile_objects"]
        trades = []
        for index, profile in enumerate(profiles):
            for date, pnl in (("2026-01-02", 20 - index), ("2026-01-05", 10 - index)):
                trades.append({
                    "date": date,
                    "profile_id": profile.profile_id,
                    "pnl_dollars": pnl,
                    "entry_credit_points": 1.0,
                    "exit_reason": "EOD",
                    "corridor_valid": True,
                    "wds_tier": 1,
                })
        return BacktestResult(run_config={}, trades=trades)

    monkeypatch.setattr(review, "run_backtest", fake_run_backtest)
    rows = review.candidate_control_benchmark(
        candidate=candidate,
        all_dates=["2026-01-02", "2026-01-05"],
        holdout_dates=["2026-01-05"],
        symbol="SPX",
        dte=0,
        starting_balance=10000,
        contracts=1,
    )
    assert len(rows) == 10
    assert {row["scope"] for row in rows} == {"all_data", "holdout"}
    assert sum(bool(row["is_candidate"]) for row in rows) == 2
    assert all("candidate_minus_profile_expectancy_dollars" in row for row in rows)


def test_profile_freezing_writes_disabled_research_profile_only_when_criteria_pass(tmp_path):
    candidate = opt.build_parameter_grid(
        "core_morning", optimizer_run_id="freeze-run", max_combinations=1
    )[0].profile
    recommendation = {"freeze_eligible": True}
    ok, _message = review.freeze_candidate_profile(
        candidate,
        recommendation,
        profiles_dir=tmp_path,
        profile_id="morning_5k_dynamic_tp75_opt_v1",
        split_summary=[{"split": "60/20/20", "status": "passed"}],
    )
    assert ok
    loaded = load_profile_file("morning_5k_dynamic_tp75_opt_v1", profiles_dir=tmp_path)
    assert loaded.ok and loaded.profile is not None
    assert loaded.profile.enabled is False
    assert loaded.profile.preset_kind == "research"
    assert loaded.profile.research_only is True
    assert loaded.profile.optimizer_run_id == "freeze-run"
    assert loaded.profile.parameter_hash == candidate.parameter_hash
    assert "Optimization-derived research profile" in loaded.profile.notes
    assert "No broker execution or order preview" in loaded.profile.notes


def test_profile_is_not_frozen_when_criteria_fail(tmp_path):
    candidate = opt.build_parameter_grid(
        "core_morning", optimizer_run_id="no-freeze", max_combinations=1
    )[0].profile
    ok, message = review.freeze_candidate_profile(
        candidate,
        {"freeze_eligible": False},
        profiles_dir=tmp_path,
        profile_id="should_not_exist",
        split_summary=[],
    )
    assert not ok
    assert "no profile frozen" in message
    assert not (tmp_path / "should_not_exist.yaml").exists()


def test_review_outputs_and_reader(tmp_path):
    result = review.RobustnessReviewResult(
        run_config={"review_run_id": "test", "no_execution": True},
        expanded_run_summary=[{"run": "one"}],
        split_sensitivity_summary=[{"split": "60/20/20"}],
        candidate_consistency=[{"parameter_hash": "abc"}],
        candidate_vs_control_benchmark=[{"profile_id": "candidate"}],
        freeze_criteria=[{"criterion": "trade_floor", "passed": False}],
        freeze_recommendation={"freeze_eligible": False, "recommendation": "Keep researching"},
        narrative="Nothing is robust enough to freeze.",
    )
    review.write_robustness_review(result, [tmp_path])
    loaded = ch.read_backtest_robustness_review(tmp_path)
    assert loaded["available"]
    assert loaded["split_sensitivity_summary"][0]["split"] == "60/20/20"
    assert loaded["freeze_recommendation"]["freeze_eligible"] is False
    assert "Nothing is robust enough" in loaded["narrative"]


def test_ui_and_source_keep_review_research_only_and_profiles_clean():
    assert "Robustness Review" in _UI
    assert "Split Sensitivity" in _UI
    assert "Candidate vs Controls" in _UI
    assert "Frozen Profile Recommendation" in _UI
    assert "No profile was frozen. Continue research before forward paper." in _UI
    profile_names = [path.name for path in (_REPO / "profiles").glob("*.yaml")]
    assert not any(name.startswith("opt_") for name in profile_names)
    assert "morning_5k_dynamic_tp75_opt_v1.yaml" not in profile_names
    source = (
        (_REPO / "src" / "backtesting" / "robustness_review.py").read_text(encoding="utf-8")
        + (_REPO / "scripts" / "backtest_robustness_review.py").read_text(encoding="utf-8")
    ).lower()
    for token in ("submit_order", "place_order", "preview_order", "execute_trade", "import httpx"):
        assert token not in source
