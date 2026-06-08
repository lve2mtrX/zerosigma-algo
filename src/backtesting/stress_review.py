"""Phase 10I candidate-only stress review.

Replays one persisted optimization candidate once, then applies deterministic
split, fill, account-sizing, and concentration checks. This is research
reporting only and never changes strategy, selector, risk, or quote math.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.backtesting import reports
from src.backtesting.optimization import chronological_split, optimization_base
from src.backtesting.replay_runner import run_backtest
from src.backtesting.robustness_review import generated_profile_from_run, load_optimization_run

CANDIDATE_HASH = "1a30a6cdf1b150c0"
SPLIT_CONFIGS: tuple[tuple[int, int, int], ...] = (
    (60, 20, 20),
    (50, 25, 25),
    (70, 15, 15),
    (55, 20, 25),
    (65, 20, 15),
)
FILL_HAIRCUTS: tuple[tuple[str, float], ...] = (
    ("base", 0.0),
    ("credit_haircut_5pct", 0.05),
    ("credit_haircut_10pct", 0.10),
)
ACCOUNT_SCENARIOS: tuple[tuple[float, int], ...] = (
    (2500.0, 1),
    (10000.0, 1),
    (10000.0, 5),
    (100000.0, 5),
)


@dataclass
class StressReviewResult:
    candidate_profile_snapshot: dict[str, Any]
    split_stress_summary: list[dict[str, Any]]
    slippage_stress_summary: list[dict[str, Any]]
    account_sizing_stress: list[dict[str, Any]]
    concentration_summary: list[dict[str, Any]]
    recommendation: dict[str, Any]
    narrative: str


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metrics_row(
    trades: list[dict[str, Any]],
    *,
    starting_balance: float,
    contracts: int,
) -> dict[str, Any]:
    metric = reports.metrics(
        trades, starting_balance=starting_balance, contracts=contracts
    )
    return {
        "sessions_with_trades": len({str(row.get("date")) for row in trades}),
        "total_trades": metric["total_trades"],
        "total_pnl_dollars": metric["total_pnl_dollars"],
        "expectancy_dollars": metric["expectancy_dollars"],
        "profit_factor": metric["profit_factor"],
        "win_rate": metric["win_rate"],
        "max_drawdown_dollars": metric["max_drawdown_dollars"],
        "max_drawdown_pct": metric["max_drawdown_pct"],
        "ending_balance": metric["ending_balance"],
        "return_pct": metric["return_pct"],
    }


def split_stress(
    trades: list[dict[str, Any]],
    dates: list[str],
    *,
    starting_balance: float = 10000.0,
    contracts: int = 1,
) -> list[dict[str, Any]]:
    """Measure the same candidate under five chronological split boundaries."""
    rows: list[dict[str, Any]] = []
    for train_pct, validation_pct, holdout_pct in SPLIT_CONFIGS:
        split = chronological_split(
            dates,
            train_pct=train_pct,
            validation_pct=validation_pct,
            holdout_pct=holdout_pct,
        )
        for scope in ("train", "validation", "holdout"):
            scope_dates = set(split[scope])
            scope_trades = [
                trade for trade in trades if str(trade.get("date")) in scope_dates
            ]
            rows.append({
                "split": f"{train_pct}/{validation_pct}/{holdout_pct}",
                "scope": scope,
                "sessions": len(scope_dates),
                "start": split[scope][0] if split[scope] else None,
                "end": split[scope][-1] if split[scope] else None,
                **_metrics_row(
                    scope_trades,
                    starting_balance=starting_balance,
                    contracts=contracts,
                ),
            })
    return rows


def _haircut_trades(
    trades: list[dict[str, Any]], haircut: float, *, contracts: int
) -> list[dict[str, Any]]:
    adjusted: list[dict[str, Any]] = []
    for trade in trades:
        row = dict(trade)
        deduction = _f(row.get("entry_credit_dollars")) * contracts * haircut
        row["pnl_dollars"] = round(_f(row.get("pnl_dollars")) - deduction, 2)
        row["fill_stress_deduction_dollars"] = round(deduction, 2)
        adjusted.append(row)
    return adjusted


def slippage_stress(
    trades: list[dict[str, Any]],
    *,
    starting_balance: float = 10000.0,
    contracts: int = 1,
) -> list[dict[str, Any]]:
    """Apply conservative entry-credit haircuts without resimulating exits."""
    rows: list[dict[str, Any]] = []
    for scenario, haircut in FILL_HAIRCUTS:
        adjusted = _haircut_trades(trades, haircut, contracts=contracts)
        rows.append({
            "scenario": scenario,
            "credit_haircut_pct": round(haircut * 100.0, 2),
            "method": "conservative post-trade entry-credit deduction",
            **_metrics_row(
                adjusted, starting_balance=starting_balance, contracts=contracts
            ),
        })
    return rows


def _scaled_trades(
    trades: list[dict[str, Any]], contracts: int
) -> list[dict[str, Any]]:
    dollar_fields = (
        "entry_credit_dollars",
        "exit_debit_dollars",
        "max_risk_dollars",
        "pnl_dollars",
    )
    scaled: list[dict[str, Any]] = []
    for trade in trades:
        row = dict(trade)
        for key in dollar_fields:
            if row.get(key) is not None:
                row[key] = round(_f(row[key]) * contracts, 2)
        row["contracts"] = contracts
        scaled.append(row)
    return scaled


def account_sizing_stress(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for starting_balance, contracts in ACCOUNT_SCENARIOS:
        rows.append({
            "scenario": f"${starting_balance:,.0f} / {contracts} contract"
            + ("s" if contracts != 1 else ""),
            "starting_balance": starting_balance,
            "contracts": contracts,
            **_metrics_row(
                _scaled_trades(trades, contracts),
                starting_balance=starting_balance,
                contracts=contracts,
            ),
        })
    return rows


def _concentration_row(
    dimension: str,
    category: Any,
    trades: list[dict[str, Any]],
    total_pnl: float,
) -> dict[str, Any]:
    pnl = round(sum(_f(trade.get("pnl_dollars")) for trade in trades), 2)
    return {
        "dimension": dimension,
        "category": category,
        "trades": len(trades),
        "pnl_dollars": pnl,
        "contribution_pct": (
            round(pnl / total_pnl * 100.0, 4) if total_pnl else None
        ),
    }


def concentration_check(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize structural concentration and best/worst-trade dependence."""
    total_pnl = round(sum(_f(trade.get("pnl_dollars")) for trade in trades), 2)
    rows: list[dict[str, Any]] = []
    dimensions = {
        "month": lambda row: str(row.get("date") or "")[:7],
        "side": lambda row: row.get("side") or "unknown",
        "exit_reason": lambda row: row.get("exit_reason") or "unknown",
        "wds_tier": lambda row: row.get("wds_tier") if row.get("wds_tier") is not None else "none",
        "corridor_valid": lambda row: row.get("corridor_valid"),
    }
    for dimension, key_fn in dimensions.items():
        grouped: dict[Any, list[dict[str, Any]]] = {}
        for trade in trades:
            grouped.setdefault(key_fn(trade), []).append(trade)
        for category in sorted(grouped, key=lambda value: str(value)):
            rows.append(_concentration_row(
                dimension, category, grouped[category], total_pnl
            ))

    ordered = sorted(trades, key=lambda row: _f(row.get("pnl_dollars")))
    exclusions = {
        "top_5_winners": ordered[-5:],
        "bottom_5_losers": ordered[:5],
        "excluding_best_trade": ordered[:-1],
        "excluding_best_3_trades": ordered[:-3],
        "excluding_worst_trade": ordered[1:],
        "excluding_worst_3_trades": ordered[3:],
    }
    for category, subset in exclusions.items():
        rows.append(_concentration_row("contribution_check", category, subset, total_pnl))
    return rows


def stress_recommendation(
    split_rows: list[dict[str, Any]],
    slippage_rows: list[dict[str, Any]],
    account_rows: list[dict[str, Any]],
    concentration_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    validation = [row for row in split_rows if row["scope"] == "validation"]
    holdout = [row for row in split_rows if row["scope"] == "holdout"]
    lookup = {
        str(row["category"]): row
        for row in concentration_rows
        if row["dimension"] == "contribution_check"
    }
    criteria = {
        "positive_validation_every_split": all(
            _f(row["expectancy_dollars"]) > 0 for row in validation
        ),
        "nonnegative_holdout_every_split": all(
            _f(row["expectancy_dollars"]) >= 0 for row in holdout
        ),
        "validation_trade_floor_every_split": all(
            int(row["total_trades"]) >= 10 for row in validation
        ),
        "holdout_trade_floor_every_split": all(
            int(row["total_trades"]) >= 5 for row in holdout
        ),
        "positive_after_10pct_credit_haircut": _f(
            next(
                row["total_pnl_dollars"]
                for row in slippage_rows
                if row["scenario"] == "credit_haircut_10pct"
            )
        ) > 0,
        "account_drawdown_below_15pct": all(
            _f(row["max_drawdown_pct"]) <= 15.0 for row in account_rows
        ),
        "positive_excluding_best_3_trades": _f(
            lookup.get("excluding_best_3_trades", {}).get("pnl_dollars")
        ) > 0,
    }
    passed = all(criteria.values())
    return {
        "freeze_eligible": passed,
        "recommendation": (
            "Freeze disabled research profile"
            if passed else "Do not freeze; keep as near-miss research candidate"
        ),
        "passed_criteria": sum(criteria.values()),
        "total_criteria": len(criteria),
        "failed_criteria": [name for name, ok in criteria.items() if not ok],
        "criteria": criteria,
    }


def build_stress_review(
    optimization_run_dir: str | Path,
    *,
    candidate_hash: str = CANDIDATE_HASH,
    trading_root: str | None = None,
) -> StressReviewResult:
    run = load_optimization_run(optimization_run_dir)
    candidate = generated_profile_from_run(run, candidate_hash)
    config = run["run_config"]
    split_dates = config.get("split_dates") or {}
    dates = sorted({
        date
        for scope in ("train", "validation", "holdout")
        for date in split_dates.get(scope, [])
    })
    if not dates:
        raise ValueError("optimization run has no persisted split dates")
    result = run_backtest(
        symbol=str(config.get("symbol") or "SPX"),
        profile_objects=[candidate],
        start=dates[0],
        end=dates[-1],
        dte=int(config.get("dte") or 0),
        trading_root=trading_root or config.get("trading_root"),
        run_label="phase10i_candidate_stress",
        starting_balance=10000.0,
        contracts=1,
    )
    trades = list(result.trades)
    split_rows = split_stress(trades, dates)
    slippage_rows = slippage_stress(trades)
    account_rows = account_sizing_stress(trades)
    concentration_rows = concentration_check(trades)
    recommendation = stress_recommendation(
        split_rows, slippage_rows, account_rows, concentration_rows
    )
    snapshot = {
        "parameter_hash": candidate_hash,
        "profile": candidate.to_dict(),
        "source_optimization_run": str(optimization_run_dir),
        "sessions": len(dates),
        "trades": len(trades),
        "base_metrics": reports.metrics(trades, starting_balance=10000.0, contracts=1),
        "fill_stress_method": (
            "Credit-haircut scenarios are conservative post-trade entry-credit "
            "deductions, not lifecycle resimulation."
        ),
        "no_broker": True,
        "no_order_preview": True,
        "no_execution": True,
    }
    narrative = (
        f"Candidate {candidate_hash} produced {len(trades)} trades across {len(dates)} "
        f"sessions and passed {recommendation['passed_criteria']} of "
        f"{recommendation['total_criteria']} stress criteria. "
        + (
            "It may be frozen as a disabled research profile; this is not production approval."
            if recommendation["freeze_eligible"]
            else "It remains a near-miss and was not frozen. Failed criteria: "
            + ", ".join(recommendation["failed_criteria"])
            + "."
        )
    )
    return StressReviewResult(
        candidate_profile_snapshot=snapshot,
        split_stress_summary=split_rows,
        slippage_stress_summary=slippage_rows,
        account_sizing_stress=account_rows,
        concentration_summary=concentration_rows,
        recommendation=recommendation,
        narrative=narrative,
    )


def stress_base() -> Path:
    return optimization_base().parent / "stress"


def stress_latest_dir() -> Path:
    path = stress_base() / "latest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stress_run_dir(run_label: str) -> Path:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in run_label)
    path = stress_base() / "runs" / f"{datetime.now():%Y-%m-%d_%H%M%S}_{safe[:80]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def write_stress_review(
    result: StressReviewResult, out_dirs: list[Path]
) -> list[Path]:
    for directory in out_dirs:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "candidate_profile_snapshot.json").write_text(
            json.dumps(result.candidate_profile_snapshot, indent=2, default=str),
            encoding="utf-8",
        )
        _write_csv(directory / "split_stress_summary.csv", result.split_stress_summary)
        _write_csv(
            directory / "slippage_stress_summary.csv", result.slippage_stress_summary
        )
        _write_csv(directory / "account_sizing_stress.csv", result.account_sizing_stress)
        _write_csv(directory / "concentration_summary.csv", result.concentration_summary)
        (directory / "narrative_summary.md").write_text(
            "# Near-Miss Candidate Stress Review\n\n"
            + result.narrative
            + "\n\n```json\n"
            + json.dumps(result.recommendation, indent=2)
            + "\n```\n",
            encoding="utf-8",
        )
    return out_dirs
