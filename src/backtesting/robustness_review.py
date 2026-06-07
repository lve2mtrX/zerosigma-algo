"""Phase 10H optimization robustness review and candidate freezing.

This module consumes completed Phase 10G optimization runs, compares
chronological split sensitivity, benchmarks one generated candidate against
named saved profiles on identical dates, and produces a conservative freeze
recommendation. It never changes strategy, selector, risk, or quote math.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from src.backtesting import reports
from src.backtesting.optimization import MAX_PROMOTION_DRAWDOWN_PCT, optimization_base
from src.backtesting.replay_runner import run_backtest
from src.config.strategy_profiles import (
    StrategyProfile,
    load_profile_file,
    save_profile_dict,
    validate_profile_dict,
)

BENCHMARK_PROFILE_IDS: tuple[str, ...] = (
    "morning_5k_call_tp75_control",
    "morning_2k_call_no_tp_control",
    "morning_5k_dynamic_tp75",
    "morning_2k_dynamic_no_tp",
)
MIN_REVIEW_VALIDATION_TRADES = 10
MIN_REVIEW_HOLDOUT_TRADES = 5
MAX_CONTROL_EXPECTANCY_GAP = 5.0


@dataclass
class RobustnessReviewResult:
    run_config: dict[str, Any]
    expanded_run_summary: list[dict[str, Any]]
    split_sensitivity_summary: list[dict[str, Any]]
    candidate_consistency: list[dict[str, Any]]
    candidate_vs_control_benchmark: list[dict[str, Any]]
    freeze_criteria: list[dict[str, Any]]
    freeze_recommendation: dict[str, Any]
    narrative: str


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_optimization_run(path: str | Path) -> dict[str, Any]:
    """Load one completed optimization directory without mutating it."""
    directory = Path(path)
    config_path = directory / "run_config.json"
    if not config_path.is_file() or not (directory / "rankings.csv").is_file():
        raise ValueError(f"not a completed optimization run: {directory}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid optimization run config: {directory}") from exc
    return {
        "directory": str(directory),
        "run_config": config,
        "rankings": _read_csv(directory / "rankings.csv"),
        "parameter_grid": _read_csv(directory / "parameter_grid.csv"),
        "promotion_candidates": _read_csv(directory / "promotion_candidates.csv"),
        "overfit_warnings": _read_csv(directory / "overfit_warnings.csv"),
    }


def _split_label(config: dict[str, Any]) -> str:
    return (
        f"{_i(config.get('train_pct'))}/"
        f"{_i(config.get('validation_pct'))}/"
        f"{_i(config.get('holdout_pct'))}"
    )


def _parameter_family(row: dict[str, Any]) -> str:
    """Major strategy family; selector choice is deliberately excluded."""
    keys = (
        "entry_target",
        "threshold",
        "side_policy",
        "take_profit",
        "stop_loss",
        "corridor_gate",
        "wds_gate",
        "min_credit",
        "distance_rule",
    )
    return "|".join(f"{key}={row.get(key)}" for key in keys)


def split_sensitivity(
    runs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Build per-split leaders and per-hash consistency across completed runs."""
    if len(runs) < 2:
        raise ValueError("split sensitivity requires at least two optimization runs")
    parameter_lookup: dict[str, dict[str, Any]] = {}
    for run in runs:
        parameter_lookup.update({
            str(row.get("parameter_hash")): row for row in run["parameter_grid"]
        })

    best_rows: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        config = run["run_config"]
        rankings = run["rankings"]
        if not rankings:
            raise ValueError(f"optimization run has no rankings: {run['directory']}")
        best = rankings[0]
        best_params = parameter_lookup.get(str(best.get("parameter_hash")), {})
        promotions = run["promotion_candidates"]
        best_rows.append({
            "split": _split_label(config),
            "optimizer_run_id": config.get("optimizer_run_id"),
            "best_profile_id": best.get("profile_id"),
            "best_parameter_hash": best.get("parameter_hash"),
            "best_parameter_family": _parameter_family(best_params),
            "best_rank_status": best.get("promotion_status"),
            "best_validation_trades": best.get("validation_total_trades"),
            "best_validation_expectancy_dollars": best.get("validation_expectancy_dollars"),
            "best_validation_profit_factor": best.get("validation_profit_factor"),
            "best_validation_max_drawdown_pct": best.get("validation_max_drawdown_pct"),
            "best_holdout_trades": best.get("holdout_total_trades"),
            "best_holdout_expectancy_dollars": best.get("holdout_expectancy_dollars"),
            "best_holdout_max_drawdown_pct": best.get("holdout_max_drawdown_pct"),
            "forward_paper_candidates": len(promotions),
            "best_forward_candidate": (
                promotions[0].get("profile_id") if promotions else None
            ),
        })
        for row in rankings:
            enriched = {
                **row,
                "split": _split_label(config),
                "parameter_family": _parameter_family(
                    parameter_lookup.get(str(row.get("parameter_hash")), {})
                ),
            }
            grouped.setdefault(str(row.get("parameter_hash")), []).append(enriched)

    same_best_hash = len({row["best_parameter_hash"] for row in best_rows}) == 1
    same_best_family = len({row["best_parameter_family"] for row in best_rows}) == 1
    for row in best_rows:
        row["same_best_hash_across_splits"] = same_best_hash
        row["same_best_family_across_splits"] = same_best_family

    consistency: list[dict[str, Any]] = []
    expected_splits = len(runs)
    for parameter_hash, rows in grouped.items():
        validation_exp = [_f(row.get("validation_expectancy_dollars")) for row in rows]
        holdout_exp = [_f(row.get("holdout_expectancy_dollars")) for row in rows]
        validation_dd = [_f(row.get("validation_max_drawdown_pct")) for row in rows]
        holdout_dd = [_f(row.get("holdout_max_drawdown_pct")) for row in rows]
        consistency.append({
            "parameter_hash": parameter_hash,
            "profile_id": rows[0].get("profile_id"),
            "parameter_family": rows[0].get("parameter_family"),
            "splits_present": len(rows),
            "exact_hash_all_splits": len(rows) == expected_splits,
            "promotion_splits": sum(
                row.get("promotion_status") == "Forward Paper Candidate" for row in rows
            ),
            "positive_validation_splits": sum(value > 0 for value in validation_exp),
            "nonnegative_holdout_splits": sum(value >= 0 for value in holdout_exp),
            "min_validation_trades": min(_i(row.get("validation_total_trades")) for row in rows),
            "min_holdout_trades": min(_i(row.get("holdout_total_trades")) for row in rows),
            "min_validation_expectancy_dollars": round(min(validation_exp), 2),
            "max_validation_expectancy_dollars": round(max(validation_exp), 2),
            "mean_validation_expectancy_dollars": round(mean(validation_exp), 2),
            "min_holdout_expectancy_dollars": round(min(holdout_exp), 2),
            "max_holdout_expectancy_dollars": round(max(holdout_exp), 2),
            "mean_holdout_expectancy_dollars": round(mean(holdout_exp), 2),
            "max_validation_drawdown_pct": round(max(validation_dd), 4),
            "max_holdout_drawdown_pct": round(max(holdout_dd), 4),
            "severe_overfit_splits": sum(
                str(row.get("severe_overfit_warning")).lower() == "true" for row in rows
            ),
            "average_rank": round(mean(_i(row.get("rank")) for row in rows), 2),
        })
    consistency.sort(
        key=lambda row: (
            -_i(row["positive_validation_splits"]),
            -_i(row["nonnegative_holdout_splits"]),
            -_i(row["min_validation_trades"]),
            -_i(row["min_holdout_trades"]),
            -_f(row["min_holdout_expectancy_dollars"]),
            _f(row["average_rank"]),
            str(row["parameter_hash"]),
        )
    )
    return best_rows, consistency, str(consistency[0]["parameter_hash"])


def generated_profile_from_run(run: dict[str, Any], parameter_hash: str) -> StrategyProfile:
    """Reproduce a generated profile from the persisted Phase 10G run config."""
    for item in run["run_config"].get("generated_profiles", []):
        if str(item.get("parameter_hash")) == parameter_hash:
            raw = item.get("profile")
            if not isinstance(raw, dict):
                break
            errors = validate_profile_dict(raw)
            if errors:
                raise ValueError(f"generated profile is invalid: {errors}")
            return StrategyProfile.from_dict(raw)
    raise ValueError(f"parameter hash not found in run config: {parameter_hash}")


def candidate_control_benchmark(
    *,
    candidate: StrategyProfile,
    all_dates: list[str],
    holdout_dates: list[str],
    symbol: str,
    dte: int,
    starting_balance: float,
    contracts: int,
    trading_root: str | None = None,
) -> list[dict[str, Any]]:
    """Replay candidate and named benchmarks over the exact same date set."""
    profiles = [candidate]
    for profile_id in BENCHMARK_PROFILE_IDS:
        loaded = load_profile_file(profile_id)
        if not loaded.ok or loaded.profile is None:
            raise ValueError(f"benchmark profile not loadable: {profile_id}")
        profiles.append(loaded.profile)
    result = run_backtest(
        symbol=symbol,
        profile_objects=profiles,
        start=all_dates[0],
        end=all_dates[-1],
        dte=dte,
        trading_root=trading_root,
        run_label="phase10h_candidate_benchmark",
        starting_balance=starting_balance,
        contracts=contracts,
    )
    scopes = {"all_data": set(all_dates), "holdout": set(holdout_dates)}
    rows: list[dict[str, Any]] = []
    for scope, scope_dates in scopes.items():
        scope_rows: list[dict[str, Any]] = []
        for profile in profiles:
            trades = [
                trade for trade in result.trades
                if trade.get("profile_id") == profile.profile_id
                and trade.get("date") in scope_dates
            ]
            metric = reports.metrics(
                trades, starting_balance=starting_balance, contracts=contracts
            )
            scope_rows.append({
                "scope": scope,
                "profile_id": profile.profile_id,
                "profile_name": profile.profile_name,
                "profile_kind": (
                    "candidate" if profile.profile_id == candidate.profile_id
                    else (profile.preset_kind or "saved")
                ),
                "is_candidate": profile.profile_id == candidate.profile_id,
                "sessions": len(scope_dates),
                "total_trades": metric["total_trades"],
                "expectancy_dollars": metric["expectancy_dollars"],
                "profit_factor": metric["profit_factor"],
                "max_drawdown_dollars": metric["max_drawdown_dollars"],
                "max_drawdown_pct": metric["max_drawdown_pct"],
                "return_pct": metric["return_pct"],
                "win_rate": metric["win_rate"],
                "total_pnl_dollars": metric["total_pnl_dollars"],
                "active_corridor_trades": metric["active_corridor_trades"],
                "inactive_corridor_trades": metric["inactive_corridor_trades"],
                "active_corridor_pnl_dollars": metric["active_corridor_pnl_dollars"],
                "inactive_corridor_pnl_dollars": metric["inactive_corridor_pnl_dollars"],
                "wds_tier1_trades": metric["wds_tier1"],
                "wds_tier2_trades": metric["wds_tier2"],
                "wds_tier1_pnl_dollars": metric["wds_tier1_pnl_dollars"],
                "wds_tier2_pnl_dollars": metric["wds_tier2_pnl_dollars"],
            })
        candidate_row = next(row for row in scope_rows if row["is_candidate"])
        for row in scope_rows:
            row.update({
                "candidate_minus_profile_expectancy_dollars": round(
                    _f(candidate_row["expectancy_dollars"]) - _f(row["expectancy_dollars"]), 2
                ),
                "candidate_minus_profile_profit_factor": round(
                    _f(candidate_row["profit_factor"]) - _f(row["profit_factor"]), 3
                ),
                "candidate_minus_profile_max_drawdown_pct": round(
                    _f(candidate_row["max_drawdown_pct"]) - _f(row["max_drawdown_pct"]), 4
                ),
                "candidate_minus_profile_total_trades": (
                    _i(candidate_row["total_trades"]) - _i(row["total_trades"])
                ),
            })
        rows.extend(scope_rows)
    return rows


def freeze_review(
    candidate_row: dict[str, Any],
    benchmark_rows: list[dict[str, Any]],
    *,
    required_splits: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return transparent criteria and a conservative freeze recommendation."""
    holdout = [row for row in benchmark_rows if row.get("scope") == "holdout"]
    candidate_holdout = next((row for row in holdout if row.get("is_candidate")), {})
    controls = [row for row in holdout if row.get("profile_kind") == "control"]
    best_control_exp = max((_f(row.get("expectancy_dollars")) for row in controls), default=0.0)
    criteria = [
        (
            "present_in_all_splits",
            bool(candidate_row.get("exact_hash_all_splits")),
            f"{candidate_row.get('splits_present')} of {required_splits} splits",
        ),
        (
            "positive_validation_every_split",
            _i(candidate_row.get("positive_validation_splits")) == required_splits,
            f"{candidate_row.get('positive_validation_splits')} of {required_splits} splits",
        ),
        (
            "nonnegative_holdout_every_split",
            _i(candidate_row.get("nonnegative_holdout_splits")) == required_splits,
            f"{candidate_row.get('nonnegative_holdout_splits')} of {required_splits} splits",
        ),
        (
            "validation_trade_floor_every_split",
            _i(candidate_row.get("min_validation_trades")) >= MIN_REVIEW_VALIDATION_TRADES,
            f"minimum {candidate_row.get('min_validation_trades')} trades",
        ),
        (
            "holdout_trade_floor_every_split",
            _i(candidate_row.get("min_holdout_trades")) >= MIN_REVIEW_HOLDOUT_TRADES,
            f"minimum {candidate_row.get('min_holdout_trades')} trades",
        ),
        (
            "validation_drawdown_acceptable",
            _f(candidate_row.get("max_validation_drawdown_pct"))
            <= MAX_PROMOTION_DRAWDOWN_PCT,
            f"maximum {candidate_row.get('max_validation_drawdown_pct')}%",
        ),
        (
            "no_severe_overfit_warning",
            _i(candidate_row.get("severe_overfit_splits")) == 0,
            f"{candidate_row.get('severe_overfit_splits')} severe split warnings",
        ),
        (
            "not_obviously_worse_than_controls",
            _f(candidate_holdout.get("expectancy_dollars"))
            >= best_control_exp - MAX_CONTROL_EXPECTANCY_GAP,
            (
                f"candidate holdout expectancy ${_f(candidate_holdout.get('expectancy_dollars')):,.2f}; "
                f"best control ${best_control_exp:,.2f}"
            ),
        ),
    ]
    criterion_rows = [
        {"criterion": name, "passed": passed, "detail": detail}
        for name, passed, detail in criteria
    ]
    passed = all(row["passed"] for row in criterion_rows)
    return criterion_rows, {
        "freeze_eligible": passed,
        "recommendation": "Freeze disabled research profile" if passed else "Keep researching",
        "parameter_hash": candidate_row.get("parameter_hash"),
        "profile_id": candidate_row.get("profile_id"),
        "passed_criteria": sum(row["passed"] for row in criterion_rows),
        "total_criteria": len(criterion_rows),
        "failed_criteria": [
            row["criterion"] for row in criterion_rows if not row["passed"]
        ],
        "reason": (
            "Candidate passed every split, drawdown, overfit, trade-count, and control benchmark rule."
            if passed
            else "No profile should be frozen until every robustness criterion passes."
        ),
    }


def freeze_candidate_profile(
    candidate: StrategyProfile,
    recommendation: dict[str, Any],
    *,
    profiles_dir: str | Path,
    profile_id: str,
    split_summary: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Write a disabled research profile only when the review explicitly passes."""
    if not recommendation.get("freeze_eligible"):
        return False, "candidate did not clear robustness criteria; no profile frozen"
    raw = candidate.to_dict()
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    raw.update({
        "profile_id": profile_id,
        "profile_name": "Morning 5K Dynamic Optimization Research V1",
        "enabled": False,
        "preset_kind": "research",
        "research_only": True,
        "notes": (
            "Optimization-derived research profile; disabled. "
            f"optimizer_run_id={candidate.optimizer_run_id}; "
            f"parameter_hash={candidate.parameter_hash}; "
            f"split_review={json.dumps(split_summary, separators=(',', ':'))}. "
            "No broker execution or order preview."
        ),
        "created_at": now,
        "updated_at": now,
        "profile_path": None,
    })
    errors = validate_profile_dict(raw)
    if errors:
        return False, f"frozen profile validation failed: {errors}"
    return save_profile_dict(
        raw, Path(profiles_dir) / f"{profile_id}.yaml", force=False
    )


def build_robustness_review(
    run_dirs: list[str | Path],
    *,
    run_label: str = "robustness_review",
    candidate_hash: str | None = None,
    trading_root: str | None = None,
    expanded_run_dirs: list[str | Path] | None = None,
) -> RobustnessReviewResult:
    """Build the complete Phase 10H review and benchmark."""
    runs = [load_optimization_run(path) for path in run_dirs]
    expanded_runs = list(runs)
    seen = {run["directory"] for run in expanded_runs}
    for path in expanded_run_dirs or []:
        loaded = load_optimization_run(path)
        if loaded["directory"] not in seen:
            seen.add(loaded["directory"])
            expanded_runs.append(loaded)
    split_rows, consistency, selected_hash = split_sensitivity(runs)
    selected_hash = candidate_hash or selected_hash
    candidate_row = next(
        (row for row in consistency if row["parameter_hash"] == selected_hash), None
    )
    if candidate_row is None:
        raise ValueError(f"candidate hash is not present in split runs: {selected_hash}")
    candidate = generated_profile_from_run(runs[0], selected_hash)
    config = runs[0]["run_config"]
    split_dates = config.get("split_dates") or {}
    all_dates = [
        date for split_name in ("train", "validation", "holdout")
        for date in split_dates.get(split_name, [])
    ]
    holdout_dates = list(split_dates.get("holdout") or [])
    benchmark = candidate_control_benchmark(
        candidate=candidate,
        all_dates=all_dates,
        holdout_dates=holdout_dates,
        symbol=str(config.get("symbol") or "SPX"),
        dte=_i(config.get("dte")),
        starting_balance=_f(config.get("starting_balance"), 10000.0),
        contracts=_i(config.get("contracts"), 1),
        trading_root=trading_root or config.get("trading_root"),
    )
    criteria, recommendation = freeze_review(
        candidate_row, benchmark, required_splits=len(runs)
    )
    expanded = [
        {
            "optimizer_run_id": run["run_config"].get("optimizer_run_id"),
            "grid": run["run_config"].get("grid"),
            "split": _split_label(run["run_config"]),
            "variants": len(run["rankings"]),
            "promotion_candidates": len(run["promotion_candidates"]),
            "overfit_warnings": len(run["overfit_warnings"]),
            "directory": run["directory"],
        }
        for run in expanded_runs
    ]
    narrative = (
        f"Phase 10H reviewed {sum(row['variants'] for row in expanded)} ranked rows "
        f"across {len(runs)} chronological split configurations. The same top "
        f"validation hash appeared across splits: "
        f"{'yes' if all(row['same_best_hash_across_splits'] for row in split_rows) else 'no'}. "
        f"The conservative review candidate was {candidate.profile_id} "
        f"({selected_hash}). It passed {recommendation['passed_criteria']} of "
        f"{recommendation['total_criteria']} freeze criteria. "
        + (
            "It is robust enough to freeze as a disabled research profile, not production approval."
            if recommendation["freeze_eligible"]
            else "Nothing is robust enough to freeze; continue research before forward paper."
        )
    )
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return RobustnessReviewResult(
        run_config={
            "review_run_id": f"{stamp}_{_safe_label(run_label)}",
            "run_label": run_label,
            "source_optimization_runs": [run["directory"] for run in expanded_runs],
            "split_sensitivity_runs": [run["directory"] for run in runs],
            "candidate_parameter_hash": selected_hash,
            "candidate_profile": candidate.to_dict(),
            "benchmark_profile_ids": list(BENCHMARK_PROFILE_IDS),
            "no_broker": True,
            "no_execution": True,
            "no_order_preview": True,
            "strategy_math_changed": False,
            "selector_math_changed": False,
        },
        expanded_run_summary=expanded,
        split_sensitivity_summary=split_rows,
        candidate_consistency=consistency,
        candidate_vs_control_benchmark=benchmark,
        freeze_criteria=criteria,
        freeze_recommendation=recommendation,
        narrative=narrative,
    )


def robustness_base() -> Path:
    return optimization_base() / "robustness"


def robustness_latest_dir() -> Path:
    path = robustness_base() / "latest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def robustness_run_dir(run_id: str) -> Path:
    path = robustness_base() / "runs" / _safe_label(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_label(value: Any) -> str:
    return "".join(
        char if char.isalnum() or char in "-_" else "_" for char in str(value)
    )[:100]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_robustness_review(
    result: RobustnessReviewResult, out_dirs: list[Path]
) -> list[Path]:
    """Write repeatable Phase 10H review artifacts."""
    tables = {
        "expanded_run_summary": result.expanded_run_summary,
        "split_sensitivity_summary": result.split_sensitivity_summary,
        "candidate_consistency": result.candidate_consistency,
        "candidate_vs_control_benchmark": result.candidate_vs_control_benchmark,
        "freeze_criteria": result.freeze_criteria,
    }
    for directory in out_dirs:
        directory.mkdir(parents=True, exist_ok=True)
        for name, rows in tables.items():
            _write_csv(directory / f"{name}.csv", rows)
        (directory / "run_config.json").write_text(
            json.dumps(result.run_config, indent=2, default=str), encoding="utf-8"
        )
        (directory / "freeze_recommendation.json").write_text(
            json.dumps(result.freeze_recommendation, indent=2, default=str),
            encoding="utf-8",
        )
        (directory / "narrative_summary.md").write_text(
            "# Optimization Robustness Review\n\n" + result.narrative + "\n",
            encoding="utf-8",
        )
    return out_dirs
