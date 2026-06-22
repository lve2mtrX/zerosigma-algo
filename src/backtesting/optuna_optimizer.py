"""Research-only Optuna search over deterministic historical replay."""

from __future__ import annotations

import csv
import json
import time
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.backtesting import mappers as M
from src.backtesting import optimization as O
from src.backtesting.replay_runner import run_backtest
from src.config.strategy_profiles import load_profile_file
from src.strategy_engine.evaluator import evaluate_backtest_row


@dataclass(frozen=True)
class OptunaConfig:
    symbol: str = "SPX"
    dte: int = 0
    trials: int = 100
    timeout_seconds: int = 900
    starting_balance: float = 10000.0
    contracts: int = 1
    run_label: str = "optuna_research"
    seed: int = 11
    trading_root: str | None = None


@dataclass
class OptunaResult:
    run_config: dict[str, Any]
    trials: list[dict[str, Any]]
    best_params: dict[str, Any]
    best_trials_markdown: str
    param_importance: list[dict[str, Any]]
    robustness_markdown: str


def optuna_available() -> bool:
    try:
        import optuna  # noqa: F401
    except ImportError:
        return False
    return True


def _trial_parameters(trial: Any) -> dict[str, Any]:
    return {
        "archetype": trial.suggest_categorical("archetype", ["credit_spread"]),
        "side_policy": trial.suggest_categorical(
            "side_policy", ["call_only", "put_only", "dynamic_with_put_distance_gate"]
        ),
        "threshold": trial.suggest_categorical("threshold", ["2k", "5k", "10k"]),
        "min_credit": trial.suggest_categorical("min_credit", [0.50, 0.75, 1.00, 1.25, 1.50, 2.00]),
        "min_distance": trial.suggest_categorical("min_distance", [15, 20, 25, 30, 35, 40]),
        "take_profit": trial.suggest_categorical("take_profit", [None, 0.50, 0.75]),
        "stop_loss": trial.suggest_categorical("stop_loss", [1.00, 1.50, 2.00]),
        "corridor_required": trial.suggest_categorical("corridor_required", [False, True]),
        "wds_requirement": trial.suggest_categorical(
            "wds_requirement", ["optional", "preferred", "required"]
        ),
        "eod_exception": trial.suggest_categorical("eod_exception", [False, True]),
        "eod_max_minutes": trial.suggest_categorical("eod_max_minutes", [10, 15, 30]),
    }


def _profile_parameters(params: dict[str, Any], trial_number: int) -> dict[str, Any]:
    side = params["side_policy"]
    return {
        "entry_target": "11:00",
        "threshold": params["threshold"],
        "side_policy": "dynamic_both" if side == "dynamic_with_put_distance_gate" else side,
        "selector": (
            "balanced_structure_premium_valid"
            if side == "dynamic_with_put_distance_gate" else "score_best_valid"
        ),
        "take_profit": params["take_profit"],
        "stop_loss": params["stop_loss"],
        "corridor_gate": "active_required" if params["corridor_required"] else "off",
        "wds_gate": {
            "optional": "off", "preferred": "tier_1_2_preferred", "required": "tier_1_2"
        }[params["wds_requirement"]],
        "min_credit": params["min_credit"],
        "distance_rule": f"min_{params['min_distance']}",
        "put_gate": "distance_25" if side == "dynamic_with_put_distance_gate" else "off",
        "research_eod_exception": params["eod_exception"],
        "research_eod_max_minutes": params["eod_max_minutes"],
        "optuna_trial_number": trial_number,
    }


def robustness_objective(row: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Transparent multi-factor objective; raw P&L is deliberately absent."""
    validation_exp = float(row.get("validation_expectancy_dollars") or 0.0)
    holdout_exp = float(row.get("holdout_expectancy_dollars") or 0.0)
    validation_pf = float(row.get("validation_profit_factor") or 0.0)
    holdout_pf = float(row.get("holdout_profit_factor") or 0.0)
    validation_trades = float(row.get("validation_total_trades") or 0.0)
    holdout_trades = float(row.get("holdout_total_trades") or 0.0)
    max_dd = max(
        float(row.get("validation_max_drawdown_pct") or 0.0),
        float(row.get("holdout_max_drawdown_pct") or 0.0),
    )
    fill_exp = float(row.get("fill_haircut_expectancy_dollars") or 0.0)
    split_consistency = float(row.get("positive_validation_holdout_splits") or 0.0)
    one_day = max(
        float(row.get("validation_one_day_pnl_concentration") or 0.0),
        float(row.get("holdout_one_day_pnl_concentration") or 0.0),
    )
    month = max(
        float(row.get("validation_month_concentration") or 0.0),
        float(row.get("holdout_month_concentration") or 0.0),
    )
    avg_rr = float(row.get("avg_risk_reward") or 0.0)
    avg_credit_pct = float(row.get("avg_credit_pct_of_width") or 0.0)
    low_trade_penalty = max(0.0, 10.0 - validation_trades) * 2.0 + max(0.0, 5.0 - holdout_trades) * 2.0
    poor_rr_penalty = max(0.0, 0.10 - avg_rr) * 100.0
    poor_credit_penalty = max(0.0, 0.10 - avg_credit_pct) * 100.0
    empty_split_penalty = 50.0 if validation_trades <= 0 or holdout_trades <= 0 else 0.0
    negative_validation_penalty = 30.0 if validation_exp <= 0 else 0.0
    negative_holdout_penalty = 20.0 if holdout_exp < 0 else 0.0
    components = {
        "validation_expectancy_component": max(-20.0, min(20.0, validation_exp * 0.30)),
        "holdout_expectancy_component": max(-20.0, min(20.0, holdout_exp * 0.30)),
        "profit_factor_component": (min(validation_pf, 3.0) + min(holdout_pf, 3.0) - 2.0) * 6.0,
        "trade_count_component": min(15.0, (validation_trades + holdout_trades) * 0.5),
        "fill_haircut_component": fill_exp * 0.15,
        "split_consistency_component": split_consistency * 5.0,
        "drawdown_penalty": -max_dd * 2.0,
        "one_day_concentration_penalty": -one_day * 25.0,
        "month_concentration_penalty": -month * 20.0,
        "low_trade_count_penalty": -low_trade_penalty,
        "empty_validation_or_holdout_penalty": -empty_split_penalty,
        "negative_validation_penalty": -negative_validation_penalty,
        "negative_holdout_penalty": -negative_holdout_penalty,
        "poor_risk_reward_penalty": -poor_rr_penalty,
        "poor_credit_pct_penalty": -poor_credit_penalty,
    }
    return round(sum(components.values()), 6), components


def _risk_averages(trades: list[dict[str, Any]]) -> tuple[float, float]:
    enriched = [evaluate_backtest_row(row) for row in trades]
    rr = [float(row["risk_reward"]) for row in enriched if row.get("risk_reward") is not None]
    credit_pct = [
        float(row["credit_pct_of_width"])
        for row in enriched if row.get("credit_pct_of_width") is not None
    ]
    return (
        round(sum(rr) / len(rr), 6) if rr else 0.0,
        round(sum(credit_pct) / len(credit_pct), 6) if credit_pct else 0.0,
    )


def trial_research_status(row: dict[str, Any]) -> str:
    validation_trades = int(float(row.get("validation_total_trades") or 0))
    holdout_trades = int(float(row.get("holdout_total_trades") or 0))
    validation_exp = float(row.get("validation_expectancy_dollars") or 0.0)
    holdout_exp = float(row.get("holdout_expectancy_dollars") or 0.0)
    if validation_trades < 10 or holdout_trades < 5:
        return "Needs More Data"
    if validation_exp <= 0 or holdout_exp < 0:
        return "Reject"
    return "Research Candidate"


def _result_markdown(
    ranked: list[dict[str, Any]],
) -> tuple[str, str]:
    best_md = [
        "# Optuna Best Trials", "", "Research-only. No profile was written or promoted.", "",
    ]
    for row in ranked[:10]:
        best_md.append(
            f"- **#{row['rank']} trial {row['trial_number']}**: {row['research_status']}; "
            f"objective {float(row['objective_value']):.2f}; validation expectancy "
            f"${float(row.get('validation_expectancy_dollars') or 0):,.2f}; holdout "
            f"${float(row.get('holdout_expectancy_dollars') or 0):,.2f}; "
            f"{int(float(row.get('validation_total_trades') or 0))}/"
            f"{int(float(row.get('holdout_total_trades') or 0))} validation/holdout trades."
        )
    candidates = sum(row["research_status"] == "Research Candidate" for row in ranked)
    positive_both = sum(
        float(row.get("validation_expectancy_dollars") or 0) > 0
        and float(row.get("holdout_expectancy_dollars") or 0) >= 0
        for row in ranked
    )
    robust_md = (
        "# Optuna Robustness Summary\n\n"
        f"Evaluated {len(ranked)} deterministic SPX 0DTE trials. {positive_both} had positive "
        f"validation and holdout expectancy; {candidates} also cleared the 10/5 trade-count "
        "floor and qualified as a Research Candidate. The objective combined validation and "
        "holdout expectancy, trade count, profit factor, drawdown, fill haircut, split "
        "consistency, concentration, and risk-quality penalties. No live profile was written "
        "and no automatic promotion occurred.\n"
    )
    return "\n".join(best_md) + "\n", robust_md


def run_optuna(config: OptunaConfig) -> OptunaResult:
    try:
        import optuna
    except ImportError as exc:
        raise RuntimeError(
            "Optuna is not installed. Install research dependencies with "
            "python -m pip install -e .[research]"
        ) from exc
    if config.symbol != "SPX" or config.dte != 0:
        raise ValueError("Phase 11C Optuna search currently supports SPX 0DTE only")
    optimizer_run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + config.run_label
    opt_config = O.OptimizationConfig(
        symbol=config.symbol, dte=config.dte, all_data=True,
        starting_balance=config.starting_balance, contracts=config.contracts,
        grid="learned_dynamic_repair", run_label=config.run_label,
        trading_root=config.trading_root,
    )
    dates = O._date_range(opt_config)
    if len(dates) < 3:
        raise ValueError("at least three historical dates are required")
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=config.seed),
        study_name=optimizer_run_id,
    )
    base = load_profile_file("morning_5k_dynamic_tp75")
    if not base.ok or base.profile is None:
        raise ValueError(f"base profile is unavailable: {base.errors}")
    started = time.monotonic()
    split = O.chronological_split(dates)
    trial_rows: list[dict[str, Any]] = []
    target_trials = max(1, int(config.trials))
    batch_size = 20
    batch_number = 0
    while len(trial_rows) < target_trials:
        if time.monotonic() - started >= max(1, config.timeout_seconds):
            break
        batch_trials: list[Any] = []
        generated: list[O.GeneratedProfile] = []
        for _ in range(min(batch_size, target_trials - len(trial_rows))):
            trial = study.ask()
            params = _trial_parameters(trial)
            generated.append(O._generated_profile(
                base.profile, _profile_parameters(params, trial.number),
                grid_name="optuna_research", optimizer_run_id=optimizer_run_id,
                parameter_set_id=f"trial_{trial.number:04d}",
            ))
            batch_trials.append(trial)
        replay = run_backtest(
            symbol=config.symbol, profile_objects=[item.profile for item in generated],
            start=dates[0], end=dates[-1], dte=config.dte,
            trading_root=config.trading_root,
            run_label=f"{config.run_label}_batch_{batch_number:02d}",
            starting_balance=config.starting_balance, contracts=config.contracts,
        )
        sensitivity = O._robustness_sensitivity(replay, generated, dates, opt_config)
        for trial, item in zip(batch_trials, generated, strict=True):
            train = O._split_result_row(replay, item, "train", split["train"], opt_config)
            validation = O._split_result_row(
                replay, item, "validation", split["validation"], opt_config
            )
            holdout = O._split_result_row(replay, item, "holdout", split["holdout"], opt_config)
            trades = [
                row for row in replay.trades if row.get("profile_id") == item.profile.profile_id
            ]
            avg_rr, avg_credit_pct = _risk_averages(trades)
            extra = sensitivity[item.profile.profile_id]
            row = {
                "trial_number": trial.number,
                "batch_number": batch_number,
                "profile_id": item.profile.profile_id,
                "parameter_hash": item.parameter_hash,
                **item.parameters,
                **trial.params,
                **O._prefixed(train, "train"),
                **O._prefixed(validation, "validation"),
                **O._prefixed(holdout, "holdout"),
                "fill_haircut_expectancy_dollars": extra[
                    "slippage_haircut_expectancy_dollars"
                ],
                "positive_validation_holdout_splits": extra[
                    "positive_validation_holdout_splits"
                ],
                "avg_risk_reward": avg_rr,
                "avg_credit_pct_of_width": avg_credit_pct,
            }
            score, components = robustness_objective(row)
            row.update({"objective_value": score, **components})
            trial_rows.append(row)
            study.tell(trial, score)
        batch_number += 1
    ranked = sorted(trial_rows, key=lambda row: (-float(row["objective_value"]), int(row["trial_number"])))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        row["research_status"] = trial_research_status(row)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", optuna.exceptions.ExperimentalWarning)
            evaluator = optuna.importance.PedAnovaImportanceEvaluator()
            importance = [
                {"parameter": key, "importance": round(value, 6)}
                for key, value in optuna.importance.get_param_importances(
                    study, evaluator=evaluator
                ).items()
            ]
    except (ImportError, RuntimeError, ValueError, ZeroDivisionError):
        importance = []
    best = ranked[0] if ranked else {}
    best_params = {
        key: best.get(key)
        for key in (
            "side_policy", "threshold", "min_credit", "min_distance", "take_profit",
            "stop_loss", "corridor_required", "wds_requirement", "eod_exception",
            "eod_max_minutes", "parameter_hash", "objective_value",
        )
    }
    best_md, robust_md = _result_markdown(ranked)
    return OptunaResult(
        run_config={
            **asdict(config), "optimizer_run_id": optimizer_run_id,
            "dates": {"start": dates[0], "end": dates[-1], "sessions": len(dates)},
            "trials_completed": len(ranked), "research_only": True,
            "adaptive_batch_size": batch_size,
            "profile_writes": False, "automatic_promotion": False,
            "order_preview": False, "execution": False,
            "search_space_limitations": {
                "implemented_archetype": "credit_spread",
                "long_call_long_put": "candidate/risk models implemented; replay search deferred",
                "eod_exception": "recorded research parameter; prospective replay gate deferred",
            },
        },
        trials=ranked, best_params=best_params,
        best_trials_markdown=best_md,
        param_importance=importance, robustness_markdown=robust_md,
    )


def optuna_base() -> Path:
    return M.output_base().parent / "research" / "optuna"


def optuna_latest_dir() -> Path:
    return optuna_base() / "latest"


def optuna_run_dir(run_id: str) -> Path:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in run_id)[:80]
    return optuna_base() / "runs" / safe


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_optuna_outputs(result: OptunaResult, out_dirs: list[Path]) -> list[Path]:
    for directory in out_dirs:
        directory.mkdir(parents=True, exist_ok=True)
        _write_csv(directory / "optuna_trials.csv", result.trials)
        _write_csv(directory / "optuna_param_importance.csv", result.param_importance)
        (directory / "optuna_best_params.json").write_text(
            json.dumps(result.best_params, indent=2, default=str), encoding="utf-8"
        )
        (directory / "optuna_best_trials.md").write_text(
            result.best_trials_markdown, encoding="utf-8"
        )
        (directory / "optuna_robustness_summary.md").write_text(
            result.robustness_markdown, encoding="utf-8"
        )
        (directory / "run_config.json").write_text(
            json.dumps(result.run_config, indent=2, default=str), encoding="utf-8"
        )
    return out_dirs
