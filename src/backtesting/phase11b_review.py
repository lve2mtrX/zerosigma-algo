"""Deterministic Phase 11B summary across the three bounded research grids."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from src.backtesting.learning import research_latest_dir
from src.backtesting.optimization import optimization_base

_GRID_LABELS = {
    "call_only_expansion": "learned_call_only_expansion",
    "call_only_robustness": "learned_call_only_robustness",
    "dynamic_repair": "learned_dynamic_repair",
}


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


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


def latest_run_for_label(run_label: str) -> Path:
    runs = optimization_base() / "runs"
    matches = sorted(
        (path for path in runs.glob(f"*{run_label}*") if path.is_dir()),
        key=lambda path: path.name,
    )
    if not matches:
        raise ValueError(f"optimization run not found for label {run_label!r}")
    return matches[-1]


def _total_pnl(row: dict[str, Any]) -> float:
    return sum(
        _f(row.get(f"{split}_total_pnl_dollars"))
        for split in ("train", "validation", "holdout")
    )


def _best_research(rows: list[dict[str, Any]]) -> dict[str, Any]:
    research = [
        row for row in rows if str(row.get("profile_kind") or "").lower() not in {"control", "benchmark"}
    ]
    robust = [row for row in research if row.get("robustness_status") == "Research Candidate"]
    return min(robust or research or rows, key=lambda row: int(_f(row.get("rank")) or 999999), default={})


def _benchmark(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        (
            row for row in rows
            if row.get("base_profile_id") == "morning_5k_call_tp75_control"
            and str(row.get("profile_kind") or "").lower() == "control"
        ),
        {},
    )


def write_phase11b_review(
    run_dirs: dict[str, Path],
    *,
    output_dir: Path | None = None,
) -> Path:
    """Write a readable cross-grid smoke summary into the research latest directory."""
    output = output_dir or research_latest_dir()
    output.mkdir(parents=True, exist_ok=True)
    rankings: dict[str, list[dict[str, Any]]] = {}
    combined_scorecard: list[dict[str, Any]] = []
    for label, directory in run_dirs.items():
        rows = _read_csv(directory / "rankings.csv")
        scorecard = {
            row["profile_id"]: row
            for row in _read_csv(directory / "strategy_robustness_scorecard.csv")
        }
        rows = [
            {
                **row,
                "robustness_status": scorecard.get(str(row.get("profile_id")), {}).get("status"),
                "robustness_warnings": scorecard.get(str(row.get("profile_id")), {}).get("warnings"),
            }
            for row in rows
        ]
        rankings[label] = rows
        _write_csv(output / f"{label}_results.csv", rows[:20])
        combined_scorecard.extend(
            {"grid": _GRID_LABELS[label], **row}
            for row in _read_csv(directory / "strategy_robustness_scorecard.csv")
        )
    _write_csv(output / "strategy_robustness_scorecard.csv", combined_scorecard)

    filters = sorted(
        _read_csv(output / "filter_impact_analysis.csv"),
        key=lambda row: -_f(row.get("expectancy_delta_dollars")),
    )
    best = {label: _best_research(rows) for label, rows in rankings.items()}
    controls = [_benchmark(rows) for rows in rankings.values()]
    controls = [row for row in controls if row]
    control = controls[0] if controls else {}
    control_pnl = _total_pnl(control)
    beats = [
        label for label, rows in rankings.items()
        if any(
            str(row.get("profile_kind") or "").lower() not in {"control", "benchmark"}
            and _total_pnl(row) > control_pnl
            for row in rows
        )
    ]
    enough = [
        label for label, row in best.items()
        if _f(row.get("validation_total_trades")) >= 8
        and _f(row.get("holdout_total_trades")) >= 4
    ]
    top_filters = ", ".join(str(row.get("filter")) for row in filters[:3]) or "not available"
    lines = [
        "# Phase 11B Smoke Summary",
        "",
        "Research-only comparison. No profile is automatically promoted.",
        "",
    ]
    for label, title in (
        ("call_only_expansion", "Best call-only expansion candidate"),
        ("call_only_robustness", "Best call-only robustness candidate"),
        ("dynamic_repair", "Best dynamic repair candidate"),
    ):
        row = best[label]
        lines.append(
            f"- **{title}:** {row.get('profile_name') or row.get('profile_id') or 'Unavailable'}; "
            f"validation expectancy ${_f(row.get('validation_expectancy_dollars')):,.2f}, "
            f"holdout expectancy ${_f(row.get('holdout_expectancy_dollars')):,.2f}, "
            f"combined P&L ${_total_pnl(row):,.2f}, "
            f"robustness {row.get('robustness_status') or 'not available'}."
        )
    lines.extend([
        (
            f"- **Morning 5K Call TP75 benchmark:** combined P&L ${control_pnl:,.2f}. "
            f"Grids with any research result above it: {', '.join(beats) if beats else 'none'}."
        ),
        f"- **Enough validation/holdout trades:** {', '.join(enough) if enough else 'none'}.",
        f"- **Most important apparent filters:** {top_filters}.",
        (
            "- **Next experiment:** rerun the most robust dynamic-repair gate across another "
            "chronological period and a higher fill haircut; keep the Morning 5K Call TP75 "
            "control as the benchmark."
        ),
        "",
    ])
    (output / "phase11b_smoke_summary.md").write_text("\n".join(lines), encoding="utf-8")

    score_lines = [
        "# Strategy Robustness Scorecard",
        "",
        "Research-only; statuses do not authorize forward paper.",
        "",
    ]
    for row in combined_scorecard[:30]:
        score_lines.append(
            f"- **{row.get('grid')} · {row.get('profile_id')}**: {row.get('status')}; "
            f"{row.get('split_consistency')}; haircut {row.get('slippage_haircut_robustness')}; "
            f"{row.get('warnings') or 'no generated warning'}."
        )
    (output / "strategy_robustness_scorecard.md").write_text(
        "\n".join(score_lines) + "\n", encoding="utf-8"
    )
    return output
