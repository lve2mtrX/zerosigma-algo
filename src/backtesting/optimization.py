"""Phase 10G repeatable research optimization over historical replay.

The optimizer generates in-memory research profiles, runs the existing replay
pipeline once, then evaluates chronological train/validation/holdout splits.
Ranking uses train + validation only. Holdout is reported separately and may
affect promotion labels, but never ranking order.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.app import operator_mode as om
from src.backtesting import mappers as M
from src.backtesting import raw_snapshot_loader as L
from src.backtesting import reports
from src.backtesting.replay_runner import BacktestResult, run_backtest
from src.backtesting.schemas import DTE_0, DTE_1
from src.config.strategy_profiles import StrategyProfile, load_profile_file

MIN_VALIDATION_TRADES = 10
MIN_HOLDOUT_TRADES = 5
MAX_PROMOTION_DRAWDOWN_PCT = 10.0
SEVERE_DRAWDOWN_PCT = 15.0
MATERIAL_HOLDOUT_EXPECTANCY_FLOOR = -5.0

RANKING_METHOD = (
    "Rank uses train + validation only: validation expectancy, validation profit "
    "factor, validation return, validation trade count, validation drawdown penalty, "
    "and train-to-validation expectancy degradation penalty. Holdout metrics never "
    "affect ranking order; they are used only for robustness warnings and promotion."
)

GRID_SPECS: dict[str, dict[str, Any]] = {
    "core_morning": {
        "base_profile_ids": ["morning_5k_dynamic_tp75"],
        "dimensions": {
            "entry_target": ["11:00"],
            "threshold": ["2k", "5k"],
            "side_policy": ["call_only", "dynamic_both"],
            "selector": ["score_best_valid", "balanced_structure_premium_valid"],
            "take_profit": [None, 0.75],
            "stop_loss": [1.50, 2.00],
            "corridor_gate": ["off", "active_required"],
            "wds_gate": ["off", "tier_1_2"],
            "min_credit": [None, 1.00],
            "distance_rule": ["none"],
        },
    },
    "core_eod": {
        "base_profile_ids": ["eod_5k_dynamic_sl150_no_tp"],
        "dimensions": {
            "entry_target": ["15:15"],
            "threshold": ["5k"],
            "side_policy": ["call_only", "dynamic_both"],
            "selector": ["score_best_valid", "balanced_structure_premium_valid"],
            "take_profit": [None, 0.50],
            "stop_loss": [1.50, 2.00],
            "corridor_gate": ["off", "active_required"],
            "wds_gate": ["off", "tier_1_2"],
            "min_credit": [None, 1.00],
            "distance_rule": ["none"],
        },
    },
    "dynamic_selector_experiments": {
        "base_profile_ids": ["morning_5k_dynamic_tp75", "morning_2k_dynamic_no_tp"],
        "dimensions": {
            "entry_target": ["11:00"],
            "threshold": ["base"],
            "side_policy": ["dynamic_both"],
            "selector": ["score_best_valid", "balanced_structure_premium_valid"],
            "take_profit": ["base"],
            "stop_loss": ["base"],
            "corridor_gate": ["off", "active_required"],
            "wds_gate": ["off", "tier_1_2"],
            "min_credit": [None, 1.00, 1.50],
            "distance_rule": ["none", "avoid_too_close", "avoid_too_far"],
        },
    },
    "controls_baseline": {
        "base_profile_ids": [
            "morning_5k_call_tp75_control",
            "morning_2k_call_no_tp_control",
            "eod_5k_call_sl150_no_tp_control",
            "eod_5k_call_tp50_control",
        ],
        "dimensions": {
            "entry_target": ["base"],
            "threshold": ["base"],
            "side_policy": ["base"],
            "selector": ["base"],
            "take_profit": ["base"],
            "stop_loss": ["base"],
            "corridor_gate": ["off"],
            "wds_gate": ["off"],
            "min_credit": ["base"],
            "distance_rule": ["base"],
        },
    },
    "custom_selected_profiles": {
        "base_profile_ids": [],
        "dimensions": {
            "entry_target": ["base"],
            "threshold": ["base"],
            "side_policy": ["base"],
            "selector": ["base"],
            "take_profit": ["base"],
            "stop_loss": ["base"],
            "corridor_gate": ["off"],
            "wds_gate": ["off"],
            "min_credit": ["base"],
            "distance_rule": ["base"],
        },
    },
    "learned_hypotheses": {
        "base_profile_ids": [],
        "dimensions": {},
        "source": "outputs/research/latest/generated_strategy_hypotheses.json",
    },
}

DEFERRED_PARAMETERS = {
    "selectors": ["call_biased_research_selector", "gated_put_research_selector"],
    "entry_targets": ["15:00", "15:30"],
    "reason": (
        "Named research selectors remain deferred; this phase uses existing selector "
        "modes plus explicit research-only corridor/WDS gates. Additional entry times "
        "are supported by replay but omitted from built-in grids pending targeted study."
    ),
}


@dataclass(frozen=True)
class OptimizationConfig:
    symbol: str = "SPX"
    dte: int = 0
    start: str | None = None
    end: str | None = None
    latest_days: int = 0
    all_data: bool = False
    starting_balance: float = 10000.0
    contracts: int = 1
    grid: str = "core_morning"
    run_label: str = "optimize"
    max_combinations: int = 12
    profile_ids: tuple[str, ...] = ()
    train_pct: int = 60
    validation_pct: int = 20
    holdout_pct: int = 20
    train_end: str | None = None
    validation_end: str | None = None
    trading_root: str | None = None
    from_research: str | None = None


@dataclass
class GeneratedProfile:
    profile: StrategyProfile
    parameters: dict[str, Any]
    parameter_hash: str
    synopsis: str


@dataclass
class OptimizationResult:
    run_config: dict[str, Any]
    parameter_grid: list[dict[str, Any]]
    train_results: list[dict[str, Any]]
    validation_results: list[dict[str, Any]]
    holdout_results: list[dict[str, Any]]
    combined_results: list[dict[str, Any]]
    rankings: list[dict[str, Any]]
    promotion_candidates: list[dict[str, Any]]
    rejected_candidates: list[dict[str, Any]]
    robustness_summary: list[dict[str, Any]]
    overfit_warnings: list[dict[str, Any]]
    narrative: str


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parameter_hash(base_profile_id: str, parameters: dict[str, Any]) -> str:
    """Stable hash independent of optimizer run id or generation timestamp."""
    blob = json.dumps(
        {"base_profile_id": base_profile_id, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _spread_indices(total: int, maximum: int) -> list[int]:
    """Deterministically sample across the full product without random bias."""
    if maximum <= 0 or total <= maximum:
        return list(range(total))
    if maximum == 1:
        return [0]
    return sorted({
        round(index * (total - 1) / (maximum - 1))
        for index in range(maximum)
    })


def _threshold_value(label: str) -> float:
    return {"1k": 1000.0, "2k": 2000.0, "5k": 5000.0, "10k": 10000.0}[label]


def _generated_profile(
    base: StrategyProfile,
    parameters: dict[str, Any],
    *,
    grid_name: str,
    optimizer_run_id: str,
    parameter_set_id: str,
) -> GeneratedProfile:
    phash = parameter_hash(base.profile_id, parameters)
    raw = base.to_dict()
    raw.update({
        "profile_id": f"opt_{base.profile_id[:28]}_{phash[:10]}",
        "profile_name": f"Research {base.profile_name} [{phash[:8]}]",
        "enabled": False,
        "notes": "Generated in-memory Phase 10G research profile. No execution.",
        "research_only": True,
        "generated_profile_id": f"opt_{phash[:12]}",
        "base_profile_id": base.profile_id,
        "parameter_set_id": parameter_set_id,
        "optimizer_run_id": optimizer_run_id,
        "parameter_hash": phash,
        "research_grid_name": grid_name,
        "research_corridor_gate": parameters["corridor_gate"],
        "research_wds_gate": parameters["wds_gate"],
        "profile_path": None,
    })
    entry = parameters["entry_target"]
    if entry != "base":
        raw["target_time"] = entry
    threshold = parameters["threshold"]
    if threshold != "base":
        raw["threshold_label"] = threshold
        raw["wing_threshold"] = _threshold_value(threshold)
    side = parameters["side_policy"]
    if side == "call_only":
        raw.update(allow_call_credit=True, allow_put_credit=False, side_policy="call only")
    elif side == "put_only":
        raw.update(allow_call_credit=False, allow_put_credit=True, side_policy="put only")
    elif side == "dynamic_both":
        raw.update(
            allow_call_credit=True,
            allow_put_credit=True,
            side_policy="dynamic both sides",
            preset_kind="dynamic",
        )
    selector = parameters["selector"]
    if selector != "base":
        raw["daily_selector"] = selector
    tp = parameters["take_profit"]
    if tp != "base":
        raw["take_profit_pct"] = tp
        raw["take_profit_mode"] = "credit_capture" if tp is not None else "none"
    sl = parameters["stop_loss"]
    if sl != "base":
        raw["stop_loss_pct"] = sl
        raw["stop_loss_mode"] = "fixed_credit_multiple" if sl is not None else None
    min_credit = parameters["min_credit"]
    if min_credit != "base":
        raw["min_selector_credit"] = min_credit
    distance = parameters["distance_rule"]
    if distance != "base":
        raw["min_selector_distance_from_spot"] = 10.0 if distance == "avoid_too_close" else None
        raw["max_selector_distance_from_spot"] = 50.0 if distance == "avoid_too_far" else None
    profile = StrategyProfile.from_dict(raw)
    return GeneratedProfile(
        profile=profile,
        parameters=dict(parameters),
        parameter_hash=phash,
        synopsis=om.strategy_synopsis(profile, context="backtest"),
    )


def build_parameter_grid(
    grid_name: str,
    *,
    optimizer_run_id: str,
    max_combinations: int = 12,
    profile_ids: list[str] | tuple[str, ...] | None = None,
    from_research: str | Path | None = None,
) -> list[GeneratedProfile]:
    """Build a deterministic, reproducible in-memory generated-profile grid."""
    if grid_name not in GRID_SPECS:
        raise ValueError(f"unknown optimization grid {grid_name!r}")
    if grid_name == "learned_hypotheses":
        from src.backtesting.learning import load_learned_parameter_sets

        learned = load_learned_parameter_sets(from_research)
        allowed = set(profile_ids or ())
        if allowed:
            learned = [row for row in learned if row.get("base_profile_id") in allowed]
        if not learned:
            raise ValueError("learned_hypotheses produced no parameter sets")
        benchmarks = [row for row in learned if row.get("benchmark")]
        research = [row for row in learned if not row.get("benchmark")]
        maximum = max_combinations if max_combinations > 0 else len(learned)
        selected = benchmarks[:maximum]
        remaining = max(0, maximum - len(selected))
        if remaining:
            selected.extend(
                research[index]
                for index in _spread_indices(len(research), remaining)
            )
        generated: list[GeneratedProfile] = []
        for index, row in enumerate(selected, start=1):
            base_id = str(row.get("base_profile_id") or "")
            parameters = dict(row.get("parameters") or {})
            parameters["hypothesis_id"] = row.get("hypothesis_id")
            parameters["learned_variant_id"] = row.get("variant_id")
            parameters["research_benchmark"] = bool(row.get("benchmark"))
            loaded = load_profile_file(base_id)
            if not loaded.ok or loaded.profile is None:
                raise ValueError(f"base profile {base_id!r} not loadable: {loaded.errors}")
            generated.append(_generated_profile(
                loaded.profile,
                parameters,
                grid_name=grid_name,
                optimizer_run_id=optimizer_run_id,
                parameter_set_id=f"p{index:04d}",
            ))
        return generated
    spec = GRID_SPECS[grid_name]
    bases = list(profile_ids or spec["base_profile_ids"])
    if not bases:
        raise ValueError(f"grid {grid_name!r} requires --profile-ids")
    dimensions = spec["dimensions"]
    keys = list(dimensions)
    combos = [
        dict(zip(keys, values, strict=True))
        for values in itertools.product(*(dimensions[key] for key in keys))
    ]
    pairs = [(base_id, combo) for base_id in bases for combo in combos]
    selected = [pairs[index] for index in _spread_indices(len(pairs), max_combinations)]
    generated: list[GeneratedProfile] = []
    for index, (base_id, parameters) in enumerate(selected, start=1):
        loaded = load_profile_file(base_id)
        if not loaded.ok or loaded.profile is None:
            raise ValueError(f"base profile {base_id!r} not loadable: {loaded.errors}")
        generated.append(_generated_profile(
            loaded.profile,
            parameters,
            grid_name=grid_name,
            optimizer_run_id=optimizer_run_id,
            parameter_set_id=f"p{index:04d}",
        ))
    return generated


def chronological_split(
    dates: list[str],
    *,
    train_pct: int = 60,
    validation_pct: int = 20,
    holdout_pct: int = 20,
    train_end: str | None = None,
    validation_end: str | None = None,
) -> dict[str, list[str]]:
    """Return ordered, non-overlapping chronological train/validation/holdout dates."""
    ordered = sorted(dict.fromkeys(dates))
    if train_end or validation_end:
        if not (train_end and validation_end and train_end < validation_end):
            raise ValueError("custom split requires train_end < validation_end")
        split = {
            "train": [date for date in ordered if date <= train_end],
            "validation": [date for date in ordered if train_end < date <= validation_end],
            "holdout": [date for date in ordered if date > validation_end],
        }
    else:
        if train_pct + validation_pct + holdout_pct != 100:
            raise ValueError("train/validation/holdout percentages must sum to 100")
        if len(ordered) < 3:
            raise ValueError("at least three dates are required for chronological splitting")
        train_n = max(1, int(len(ordered) * train_pct / 100))
        validation_n = max(1, int(len(ordered) * validation_pct / 100))
        if train_n + validation_n >= len(ordered):
            validation_n = 1
            train_n = len(ordered) - 2
        split = {
            "train": ordered[:train_n],
            "validation": ordered[train_n:train_n + validation_n],
            "holdout": ordered[train_n + validation_n:],
        }
    if not all(split.values()):
        raise ValueError("train, validation, and holdout splits must each contain dates")
    return split


def _date_range(config: OptimizationConfig) -> list[str]:
    dte_label = DTE_1 if int(config.dte) == 1 else DTE_0
    dates = L.available_dates(config.symbol, dte_label, root=L.trading_root(config.trading_root))
    if not config.all_data:
        if config.start:
            dates = [date for date in dates if date >= config.start]
        if config.end:
            dates = [date for date in dates if date <= config.end]
        if config.latest_days:
            dates = dates[-config.latest_days:]
    return dates


def _split_result_row(
    result: BacktestResult,
    generated: GeneratedProfile,
    split_name: str,
    dates: list[str],
    config: OptimizationConfig,
) -> dict[str, Any]:
    pid = generated.profile.profile_id
    trades = [
        row for row in result.trades
        if row.get("profile_id") == pid and row.get("date") in dates
    ]
    candidates = [
        row for row in result.candidates
        if row.get("profile_id") == pid and row.get("date") in dates
    ]
    metric = reports.metrics(
        trades,
        starting_balance=config.starting_balance,
        contracts=config.contracts,
    )
    learned = generated.parameters.get("hypothesis_id") is not None
    profile_kind = (
        (
            "control"
            if str(generated.profile.preset_kind or "").lower() == "control"
            else "benchmark"
        )
        if generated.parameters.get("research_benchmark")
        else "research" if learned
        else generated.profile.preset_kind or "research"
    )
    return {
        "split": split_name,
        "profile_id": pid,
        "generated_profile_id": generated.profile.generated_profile_id,
        "base_profile_id": generated.profile.base_profile_id,
        "parameter_set_id": generated.profile.parameter_set_id,
        "parameter_hash": generated.parameter_hash,
        "profile_kind": profile_kind,
        "synopsis": generated.synopsis,
        "sessions": len(dates),
        "split_start": dates[0],
        "split_end": dates[-1],
        "candidates": len(candidates),
        **metric,
    }


def _prefixed(row: dict[str, Any], prefix: str) -> dict[str, Any]:
    skip = {
        "profile_id", "generated_profile_id", "base_profile_id", "parameter_set_id",
        "parameter_hash", "profile_kind", "synopsis", "split",
    }
    return {f"{prefix}_{key}": value for key, value in row.items() if key not in skip}


def _warning_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[tuple[str, str]] = []
    train_exp = _f(row.get("train_expectancy_dollars"))
    validation_exp = _f(row.get("validation_expectancy_dollars"))
    holdout_exp = _f(row.get("holdout_expectancy_dollars"))
    if train_exp > 0 >= validation_exp:
        warnings.append(("severe", "train_positive_validation_negative"))
    if validation_exp > 0 > holdout_exp:
        warnings.append(("warning", "validation_positive_holdout_negative"))
    if train_exp > 0 and validation_exp < train_exp * 0.25:
        warnings.append(("warning", "train_validation_expectancy_degradation"))
    if _f(row.get("validation_total_trades")) < MIN_VALIDATION_TRADES:
        warnings.append(("warning", "low_validation_trade_count"))
    if _f(row.get("holdout_total_trades")) < MIN_HOLDOUT_TRADES:
        warnings.append(("warning", "low_holdout_trade_count"))
    if _f(row.get("validation_max_drawdown_pct")) > SEVERE_DRAWDOWN_PCT:
        warnings.append(("severe", "high_validation_drawdown"))
    if _f(row.get("holdout_max_drawdown_pct")) > SEVERE_DRAWDOWN_PCT:
        warnings.append(("warning", "high_holdout_drawdown"))
    return [
        {
            "profile_id": row["profile_id"],
            "parameter_hash": row["parameter_hash"],
            "severity": severity,
            "warning": warning,
        }
        for severity, warning in warnings
    ]


def robust_score(row: dict[str, Any]) -> float:
    """Train/validation-only score. Deliberately ignores every holdout field."""
    validation_exp = _f(row.get("validation_expectancy_dollars"))
    validation_pf = _f(row.get("validation_profit_factor"))
    validation_return = _f(row.get("validation_return_pct"))
    validation_trades = _f(row.get("validation_total_trades"))
    validation_dd = _f(row.get("validation_max_drawdown_pct"))
    train_exp = _f(row.get("train_expectancy_dollars"))
    degradation = max(0.0, train_exp - validation_exp)
    return round(
        50.0
        + _clamp(validation_exp / 5.0, -20.0, 20.0)
        + _clamp((validation_pf - 1.0) * 12.0, -20.0, 20.0)
        + _clamp(validation_return * 2.0, -15.0, 15.0)
        + min(validation_trades, 20.0) / 20.0 * 15.0
        - min(validation_dd, 20.0) / 20.0 * 25.0
        - min(degradation / 5.0, 20.0),
        4,
    )


def _promotion(row: dict[str, Any], warnings: list[dict[str, Any]]) -> tuple[str, str]:
    profile_kind = str(row.get("profile_kind") or "").lower()
    if profile_kind == "control":
        return (
            "Benchmark Control",
            "Positive control result is comparison-only and requires manual approval.",
        )
    if profile_kind == "benchmark":
        return (
            "Comparison Baseline",
            "Existing dynamic profile included for comparison; not a learned candidate.",
        )
    validation_trades = int(_f(row.get("validation_total_trades")))
    holdout_trades = int(_f(row.get("holdout_total_trades")))
    if validation_trades < MIN_VALIDATION_TRADES or holdout_trades < MIN_HOLDOUT_TRADES:
        return "Needs More Data", "Validation or holdout trade count is below the research floor."
    severe = any(warning["severity"] == "severe" for warning in warnings)
    validation_exp = _f(row.get("validation_expectancy_dollars"))
    holdout_exp = _f(row.get("holdout_expectancy_dollars"))
    validation_pf = _f(row.get("validation_profit_factor"))
    validation_dd = _f(row.get("validation_max_drawdown_pct"))
    if severe or validation_exp <= 0 or validation_pf <= 1.0:
        return "Reject / Overfit", "Validation failed or severe overfit warning triggered."
    if (
        holdout_exp >= MATERIAL_HOLDOUT_EXPECTANCY_FLOOR
        and validation_dd <= MAX_PROMOTION_DRAWDOWN_PCT
    ):
        return (
            "Forward Paper Candidate",
            "Positive validation, sane holdout, sufficient trades, and controlled drawdown.",
        )
    return "Watchlist", "Promising validation result, but holdout or drawdown needs review."


def _narrative(
    config: OptimizationConfig,
    rankings: list[dict[str, Any]],
    split: dict[str, list[str]],
) -> str:
    if not rankings:
        return "No optimization variants were evaluated."
    top = rankings[0]
    best_train = max(rankings, key=lambda row: _f(row.get("train_expectancy_dollars")))
    candidates = [
        row for row in rankings if row.get("promotion_status") == "Forward Paper Candidate"
    ]
    controls = [row for row in rankings if row.get("promotion_status") == "Benchmark Control"]
    lead = (
        f"This optimization tested {len(rankings)} {config.symbol} {config.dte}DTE variants "
        f"across {sum(len(value) for value in split.values())} sessions using a chronological "
        f"{config.train_pct}/{config.validation_pct}/{config.holdout_pct} split. "
        f"Ranking used train and validation only; holdout did not affect rank order. "
        f"The highest validation-ranked profile was {top['profile_id']} with validation "
        f"expectancy ${_f(top.get('validation_expectancy_dollars')):,.2f}."
    )
    if best_train["profile_id"] != top["profile_id"]:
        lead += (
            f" The best in-sample profile was {best_train['profile_id']}, but it did not "
            "rank first after validation."
        )
    if candidates:
        return lead + " Forward-paper research candidates: " + ", ".join(
            row["profile_id"] for row in candidates
        ) + "."
    benchmark = controls[0]["profile_id"] if controls else "the existing comparison controls"
    return (
        lead
        + f" No profile cleared forward-paper promotion rules. The best benchmark remains "
        f"{benchmark}; benchmark status is not production approval."
    )


def run_optimization(config: OptimizationConfig, *, optimizer_run_id: str | None = None) -> OptimizationResult:
    """Run one deterministic optimization and build all report tables."""
    run_id = optimizer_run_id or (
        datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + _safe_label(config.run_label)
    )
    dates = _date_range(config)
    split = chronological_split(
        dates,
        train_pct=config.train_pct,
        validation_pct=config.validation_pct,
        holdout_pct=config.holdout_pct,
        train_end=config.train_end,
        validation_end=config.validation_end,
    )
    generated = build_parameter_grid(
        config.grid,
        optimizer_run_id=run_id,
        max_combinations=config.max_combinations,
        profile_ids=config.profile_ids,
        from_research=config.from_research,
    )
    result = run_backtest(
        symbol=config.symbol,
        profile_objects=[item.profile for item in generated],
        start=dates[0],
        end=dates[-1],
        dte=config.dte,
        trading_root=config.trading_root,
        run_label=config.run_label,
        starting_balance=config.starting_balance,
        contracts=config.contracts,
    )
    by_split: dict[str, list[dict[str, Any]]] = {}
    for split_name, split_dates in split.items():
        by_split[split_name] = [
            _split_result_row(result, item, split_name, split_dates, config)
            for item in generated
        ]
    combined: list[dict[str, Any]] = []
    all_warnings: list[dict[str, Any]] = []
    for index, item in enumerate(generated):
        train = by_split["train"][index]
        validation = by_split["validation"][index]
        holdout = by_split["holdout"][index]
        row = {
            "profile_id": item.profile.profile_id,
            "generated_profile_id": item.profile.generated_profile_id,
            "base_profile_id": item.profile.base_profile_id,
            "parameter_set_id": item.profile.parameter_set_id,
            "parameter_hash": item.parameter_hash,
            "profile_kind": by_split["train"][index]["profile_kind"],
            "synopsis": item.synopsis,
            **_prefixed(train, "train"),
            **_prefixed(validation, "validation"),
            **_prefixed(holdout, "holdout"),
        }
        warnings = _warning_rows(row)
        all_warnings.extend(warnings)
        status, reason = _promotion(row, warnings)
        row.update({
            "robust_score": robust_score(row),
            "promotion_status": status,
            "promotion_reason": reason,
            "overfit_warning_count": len(warnings),
            "severe_overfit_warning": any(w["severity"] == "severe" for w in warnings),
        })
        combined.append(row)
    rankings = [
        {"rank": rank, **row}
        for rank, row in enumerate(
            sorted(
                combined,
                key=lambda row: (
                    -_f(row.get("robust_score")),
                    -_f(row.get("validation_expectancy_dollars")),
                    _f(row.get("validation_max_drawdown_pct")),
                    str(row.get("parameter_hash")),
                ),
            ),
            start=1,
        )
    ]
    parameter_grid = [
        {
            "profile_id": item.profile.profile_id,
            "generated_profile_id": item.profile.generated_profile_id,
            "base_profile_id": item.profile.base_profile_id,
            "parameter_set_id": item.profile.parameter_set_id,
            "parameter_hash": item.parameter_hash,
            "optimizer_run_id": run_id,
            "research_only": True,
            "synopsis": item.synopsis,
            **item.parameters,
        }
        for item in generated
    ]
    run_config = {
        **asdict(config),
        "optimizer_run_id": run_id,
        "optimization": True,
        "ranking_method": RANKING_METHOD,
        "split_dates": split,
        "parameter_grid": parameter_grid,
        "generated_profiles": [
            {
                "parameter_hash": item.parameter_hash,
                "parameters": item.parameters,
                "profile": item.profile.to_dict(),
                "synopsis": item.synopsis,
            }
            for item in generated
        ],
        "deferred_parameters": DEFERRED_PARAMETERS,
        "walk_forward": {
            "implemented": False,
            "interface_shape": {
                "train_window_days": None,
                "validation_window_days": None,
                "holdout_window_days": None,
                "roll_days": None,
            },
            "status": "Chronological train/validation/holdout implemented; rolling windows deferred.",
        },
        "no_broker": True,
        "no_execution": True,
        "no_order_preview": True,
        "holdout_used_for_ranking": False,
    }
    narrative = _narrative(config, rankings, split)
    return OptimizationResult(
        run_config=run_config,
        parameter_grid=parameter_grid,
        train_results=by_split["train"],
        validation_results=by_split["validation"],
        holdout_results=by_split["holdout"],
        combined_results=combined,
        rankings=rankings,
        promotion_candidates=[
            row for row in rankings if row["promotion_status"] == "Forward Paper Candidate"
        ],
        rejected_candidates=[
            row for row in rankings if row["promotion_status"] == "Reject / Overfit"
        ],
        robustness_summary=rankings,
        overfit_warnings=all_warnings,
        narrative=narrative,
    )


def optimization_base() -> Path:
    return M.output_base() / "optimizations"


def optimization_latest_dir() -> Path:
    path = optimization_base() / "latest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def optimization_run_dir(run_id: str) -> Path:
    path = optimization_base() / "runs" / _safe_label(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_label(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in str(value))[:80]


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> None:
    resolved_columns: list[str] = list(columns or [])
    for row in rows:
        for key in row:
            if key not in resolved_columns:
                resolved_columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=resolved_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_optimization_reports(result: OptimizationResult, out_dirs: list[Path]) -> list[Path]:
    """Write every required Phase 10G artifact."""
    tables = {
        "parameter_grid": result.parameter_grid,
        "train_results": result.train_results,
        "validation_results": result.validation_results,
        "holdout_results": result.holdout_results,
        "combined_results": result.combined_results,
        "rankings": result.rankings,
        "promotion_candidates": result.promotion_candidates,
        "rejected_candidates": result.rejected_candidates,
        "robustness_summary": result.robustness_summary,
        "overfit_warnings": result.overfit_warnings,
    }
    for directory in out_dirs:
        directory.mkdir(parents=True, exist_ok=True)
        for name, rows in tables.items():
            columns = None
            if name in {"promotion_candidates", "rejected_candidates"} and result.rankings:
                columns = list(result.rankings[0])
            elif name == "overfit_warnings":
                columns = ["profile_id", "parameter_hash", "severity", "warning"]
            _write_csv(directory / f"{name}.csv", rows, columns)
        (directory / "run_config.json").write_text(
            json.dumps(result.run_config, indent=2, default=str),
            encoding="utf-8",
        )
        (directory / "narrative_summary.md").write_text(
            "# Optimization Research Summary\n\n" + result.narrative + "\n",
            encoding="utf-8",
        )
    return out_dirs
