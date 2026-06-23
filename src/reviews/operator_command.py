"""Offline operator roadmap and multi-profile readiness artifacts.

Only existing local files are read. No provider, broker, notification, selector,
strategy, or lifecycle code is invoked by this module.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.strategy_profiles import StrategyProfile, list_profiles

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE_NAME = "Phase 11H-A - Offline Command Layer Foundation"
PRIMARY_BENCHMARK = "morning_5k_call_tp75_control"
SECONDARY_BENCHMARK = "morning_2k_call_no_tp_control"

MATRIX_FIELDS = (
    "profile_id", "profile_name", "profile_category", "symbol", "target_dte",
    "side_policy", "selector_mode", "entry_window", "tp_metadata", "sl_metadata",
    "profile_enabled", "structure_provider", "quote_provider", "provider_mode",
    "backtest_status", "backtest_trades", "backtest_pnl_dollars",
    "backtest_expectancy_dollars", "backtest_profit_factor", "forward_paper_status",
    "saved_readiness_status", "rth_soak_eligibility", "benchmark_label",
    "blocker_reason_codes", "evidence_notes",
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _number(value: Any) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else round(number, 4)


def _side_policy(profile: StrategyProfile) -> str:
    if profile.side_policy:
        return profile.side_policy
    if profile.allow_call_credit and profile.allow_put_credit:
        return "dynamic both sides"
    if profile.allow_call_credit:
        return "call only"
    if profile.allow_put_credit:
        return "put only"
    return "no sides enabled"


def _tp(profile: StrategyProfile) -> str:
    if profile.take_profit_mode == "none" or profile.take_profit_pct is None:
        return "No fixed TP"
    return f"TP{round(profile.take_profit_pct * 100):g}% credit capture"


def _sl(profile: StrategyProfile) -> str:
    if profile.stop_loss_pct is None:
        return "Profile SL not specified"
    return f"SL{round(profile.stop_loss_pct * 100):g}% credit loss"


def _provider_mode(profile: StrategyProfile) -> str:
    if profile.structure_provider == "zerosigma_api" and profile.quote_provider == "tastytrade":
        return "live-provider configuration (not yet exercised)"
    if profile.structure_provider == "stub" and profile.quote_provider in {"mock", "null"}:
        return "offline sandbox profile"
    return "mixed provider profile"


def _benchmark(profile_id: str) -> tuple[str, str]:
    if profile_id == PRIMARY_BENCHMARK:
        return (
            "Primary RTH benchmark",
            "Top positive call-only control in the saved all-data comparison; comparison only.",
        )
    if profile_id == SECONDARY_BENCHMARK:
        return (
            "Secondary RTH benchmark",
            "Second positive call-only control in the saved all-data comparison; comparison only.",
        )
    return "Not selected", "No next-soak benchmark label."


def _evidence(repo_root: Path) -> dict[str, Any]:
    comparison_dir = repo_root / "outputs" / "backtests" / "comparisons" / "latest"
    comparison_rows = _read_csv(comparison_dir / "profile_rankings.csv")
    comparison_config = _read_json(comparison_dir / "run_config.json")
    latest_backtest_rows = _read_csv(
        repo_root / "outputs" / "backtests" / "latest" / "summary_by_profile.csv"
    )
    portfolio_dir = repo_root / "outputs" / "portfolio_forward" / "latest"
    return {
        "comparison": {row.get("profile_id", ""): row for row in comparison_rows},
        "comparison_config": comparison_config,
        "latest_backtest": {
            row.get("profile_id", ""): row for row in latest_backtest_rows
        },
        "portfolio_manifest": _read_json(portfolio_dir / "portfolio_manifest.json"),
        "portfolio_summary": _read_json(portfolio_dir / "portfolio_summary.json"),
        "readiness": _read_json(
            repo_root / "outputs" / "readiness" / "latest" / "rth_soak_readiness.json"
        ),
        "soak_review": _read_json(
            repo_root / "outputs" / "reviews" / "latest" / "rth_soak_review.json"
        ),
    }


def _backtest(profile_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    comparison = evidence["comparison"].get(profile_id)
    if comparison:
        return {
            "status": comparison.get("promotion_status") or "Offline comparison available",
            "trades": _number(comparison.get("total_trades")),
            "pnl": _number(comparison.get("total_pnl_dollars")),
            "expectancy": _number(comparison.get("expectancy_dollars")),
            "profit_factor": _number(comparison.get("profit_factor")),
            "note": "Saved offline historical comparison; not live-RTH evidence.",
        }
    latest = evidence["latest_backtest"].get(profile_id)
    if latest:
        trades = _number(latest.get("total_trades"))
        return {
            "status": "Insufficient latest backtest data" if not trades else "Latest backtest available",
            "trades": trades,
            "pnl": _number(latest.get("total_pnl_dollars")),
            "expectancy": _number(latest.get("expectancy_dollars")),
            "profit_factor": _number(latest.get("profit_factor")),
            "note": "Latest local backtest artifact only; scope may be a smoke run.",
        }
    return {
        "status": "No local backtest artifact",
        "trades": None,
        "pnl": None,
        "expectancy": None,
        "profit_factor": None,
        "note": "No matching local backtest result was found.",
    }


def _forward_status(profile_id: str, evidence: dict[str, Any]) -> tuple[str, str]:
    manifest = evidence["portfolio_manifest"]
    profiles = set(manifest.get("profiles") or [])
    if profile_id not in profiles:
        return "No local paper artifact", "No matching forward-paper record."
    summary = evidence["portfolio_summary"]
    if manifest.get("fixture_mode"):
        return (
            "Fixture paper artifact",
            f"Fixture only: {summary.get('closed_trade_count', 0)} closed trade(s); not RTH evidence.",
        )
    return (
        "Local paper artifact - RTH unverified",
        "A local paper artifact exists, but this offline review does not certify RTH provenance.",
    )


def _saved_readiness(profile_id: str, evidence: dict[str, Any]) -> tuple[str, list[str]]:
    readiness = evidence["readiness"]
    matching = {
        str(row.get("profile_id")) for row in readiness.get("profiles") or []
        if isinstance(row, dict)
    }
    if profile_id not in matching:
        return "No saved readiness artifact", []
    status = str(readiness.get("status") or "Unknown")
    blockers = [str(value) for value in readiness.get("blockers") or []]
    return f"Saved {status} - not RTH-confirmed", blockers


def profile_readiness_row(
    profile: StrategyProfile,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    backtest = _backtest(profile.profile_id, evidence)
    forward_status, forward_note = _forward_status(profile.profile_id, evidence)
    readiness_status, readiness_blockers = _saved_readiness(profile.profile_id, evidence)
    benchmark_label, benchmark_note = _benchmark(profile.profile_id)
    blockers = ["no_real_rth_readiness_evidence", "no_real_rth_soak_evidence"]
    if not profile.enabled:
        blockers.append("profile_disabled")
    if profile.structure_provider != "zerosigma_api":
        blockers.append(f"structure_provider_{profile.structure_provider}")
    if profile.quote_provider != "tastytrade":
        blockers.append(f"quote_provider_{profile.quote_provider}")
    blockers.extend(readiness_blockers)
    if "Watchlist" in str(backtest["status"]):
        blockers.append("backtest_watchlist_needs_tuning")
    if "Needs More Data" in str(backtest["status"]):
        blockers.append("backtest_needs_more_data")
    eligibility = (
        "BENCHMARK_PENDING_REAL_RTH_READINESS"
        if benchmark_label != "Not selected"
        else "BLOCKED_OFFLINE_ONLY"
    )
    entry_window = (
        f"{profile.entry_window_start}-{profile.entry_window_end}"
        if profile.entry_window_start and profile.entry_window_end
        else profile.target_time or "Not specified"
    )
    return {
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "profile_category": profile.preset_kind or (
            "research" if profile.research_only else "general"
        ),
        "symbol": profile.symbol,
        "target_dte": profile.target_dte,
        "side_policy": _side_policy(profile),
        "selector_mode": profile.daily_selector,
        "entry_window": entry_window,
        "tp_metadata": _tp(profile),
        "sl_metadata": _sl(profile),
        "profile_enabled": profile.enabled,
        "structure_provider": profile.structure_provider,
        "quote_provider": profile.quote_provider,
        "provider_mode": _provider_mode(profile),
        "backtest_status": backtest["status"],
        "backtest_trades": backtest["trades"],
        "backtest_pnl_dollars": backtest["pnl"],
        "backtest_expectancy_dollars": backtest["expectancy"],
        "backtest_profit_factor": backtest["profit_factor"],
        "forward_paper_status": forward_status,
        "saved_readiness_status": readiness_status,
        "rth_soak_eligibility": eligibility,
        "benchmark_label": benchmark_label,
        "blocker_reason_codes": sorted(set(blockers)),
        "evidence_notes": " ".join((backtest["note"], forward_note, benchmark_note)),
    }


def build_profile_readiness_matrix(
    *,
    repo_root: Path = REPO_ROOT,
    profiles_dir: Path | None = None,
    profile_ids: set[str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    evidence = _evidence(repo_root)
    rows: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for result in list_profiles(profiles_dir):
        profile_id = result.profile.profile_id if result.profile else Path(result.path or "").stem
        if profile_ids and profile_id not in profile_ids:
            continue
        if not result.ok or result.profile is None:
            invalid.append({"profile_id": profile_id, "errors": result.errors})
            continue
        rows.append(profile_readiness_row(result.profile, evidence))
    rows.sort(key=lambda row: (
        0 if row["benchmark_label"].startswith("Primary") else
        1 if row["benchmark_label"].startswith("Secondary") else 2,
        str(row["profile_id"]),
    ))
    comparison_config = evidence["comparison_config"]
    return {
        "generated_at": generated_at or datetime.now().astimezone().isoformat(),
        "phase": PHASE_NAME,
        "mode": "OFFLINE_LOCAL_ARTIFACT_REVIEW",
        "offline_only": True,
        "live_rth_evidence": False,
        "profile_count": len(rows),
        "invalid_profile_count": len(invalid),
        "benchmark_profile_ids": [PRIMARY_BENCHMARK, SECONDARY_BENCHMARK],
        "comparison_run_label": comparison_config.get("run_label"),
        "comparison_dates_evaluated": (comparison_config.get("counters") or {}).get(
            "dates_evaluated"
        ),
        "rows": rows,
        "invalid_profiles": invalid,
        "automatic_promotion": False,
        "safety_note": (
            "Eligibility is an offline review label, not production approval or live-RTH evidence."
        ),
    }


def build_operator_status(matrix: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": matrix["generated_at"],
        "current_phase": PHASE_NAME,
        "phase_status": "OFFLINE_IMPLEMENTATION",
        "evidence_status": "NO_REAL_RTH_EVIDENCE_CAPTURED",
        "next_rth_action": (
            "During the next real RTH session, run sanitized readiness for the two morning "
            "call-control benchmarks and start local paper only if readiness says READY."
        ),
        "next_rth_commands": [
            "python -m scripts.diagnose_rth_soak_readiness --profiles "
            f"{PRIMARY_BENCHMARK},{SECONDARY_BENCHMARK} --symbol SPX --dte 0 --json",
            "python -m scripts.run_portfolio_forward --profiles "
            f"{PRIMARY_BENCHMARK},{SECONDARY_BENCHMARK} --interval-seconds 60 "
            "--market-hours-only --contracts 1 --output-dir outputs/portfolio_forward",
        ],
        "offline_ready_tasks": [
            "Generate the operator roadmap and profile readiness matrix.",
            "Render notification and voice dry-run previews without delivery.",
            "Review saved comparison, paper, alert, readiness, and soak artifacts.",
            "Run deterministic fixtures and the full local test suite.",
        ],
        "blocked_on_rth_tasks": [
            "Confirm Tasty quote freshness, root, expiry, and required strikes during RTH.",
            "Capture the first real RTH local-paper soak.",
            "Compare real RTH alerts, regimes, and exits with historical expectations.",
        ],
        "deferred_execution_tasks": [
            "Order-ticket and manual-confirm preview schema (Phase 12A).",
            "Broker dry-run, broker paper, and live routing adapters (Phase 12B+).",
            "Kill-switch and live position reconciliation controls.",
        ],
        "deferred_hermes_ml_tasks": [
            "Advisory feature-store and dataset manifests.",
            "Model cards, experiment registry, and advisory confidence outputs.",
            "Any model training or advisory integration; no decision authority is allowed.",
        ],
        "benchmark_profiles": [
            {
                "profile_id": PRIMARY_BENCHMARK,
                "label": "Primary RTH benchmark",
                "approval": "Comparison only - human-selected local paper benchmark",
            },
            {
                "profile_id": SECONDARY_BENCHMARK,
                "label": "Secondary RTH benchmark",
                "approval": "Comparison only - human-selected local paper benchmark",
            },
        ],
        "profile_matrix_summary": {
            "profile_count": matrix["profile_count"],
            "invalid_profile_count": matrix["invalid_profile_count"],
            "comparison_dates_evaluated": matrix.get("comparison_dates_evaluated"),
        },
        "safety_boundaries": [
            "Offline artifacts do not constitute live-RTH evidence.",
            "No strategy, selector, risk-cap, or quote-validation changes.",
            "No broker execution, broker paper order, or order preview.",
            "No automatic profile promotion or lockbox automation.",
            "Notification and voice delivery remain disabled by default.",
            "Hermes/ML has no decision, profile-write, selection, or execution authority.",
            "Dashboard, API, and worker repositories remain untouched.",
        ],
        "offline_only": True,
        "live_rth_evidence": False,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATRIX_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **row,
                "blocker_reason_codes": "; ".join(row["blocker_reason_codes"]),
            })


def _matrix_markdown(matrix: dict[str, Any]) -> str:
    lines = [
        "# Profile Readiness Matrix",
        "",
        "OFFLINE REVIEW ONLY - NO LIVE-RTH EVIDENCE OR AUTOMATIC PROMOTION.",
        "",
        "| Profile | Category | DTE | Side / selector | Backtest | Paper | RTH status | Benchmark |",
        "|---|---|---:|---|---|---|---|---|",
    ]
    for row in matrix["rows"]:
        lines.append(
            f"| `{row['profile_id']}` | {row['profile_category']} | {row['target_dte']} | "
            f"{row['side_policy']} / `{row['selector_mode']}` | {row['backtest_status']} | "
            f"{row['forward_paper_status']} | {row['rth_soak_eligibility']} | "
            f"{row['benchmark_label']} |"
        )
    lines.extend([
        "",
        "Positive control results are comparison benchmarks only. Every profile still requires "
        "a real RTH readiness pass and human decision before a local-paper soak.",
        "",
    ])
    return "\n".join(lines)


def _status_markdown(status: dict[str, Any]) -> str:
    lines = [
        "# Operator Status / Roadmap",
        "",
        "OFFLINE / AFTER-HOURS SAFE - NO LIVE-RTH CLAIMS.",
        "",
        f"- Current phase: **{status['current_phase']}**",
        f"- Evidence: **{status['evidence_status']}**",
        f"- Next RTH action: {status['next_rth_action']}",
        "",
    ]
    for title, key in (
        ("Offline-ready tasks", "offline_ready_tasks"),
        ("Blocked on real RTH", "blocked_on_rth_tasks"),
        ("Deferred execution", "deferred_execution_tasks"),
        ("Deferred Hermes / ML", "deferred_hermes_ml_tasks"),
        ("Safety boundaries", "safety_boundaries"),
    ):
        lines.extend([f"## {title}", ""])
        lines.extend(f"- {value}" for value in status[key])
        lines.append("")
    return "\n".join(lines)


def write_operator_command_artifacts(
    matrix: dict[str, Any],
    status: dict[str, Any],
    *,
    output_root: Path | str = "outputs/reviews",
    run_id: str | None = None,
) -> dict[str, str]:
    root = Path(output_root)
    if not root.is_absolute():
        root = REPO_ROOT / root
    timestamp = datetime.fromisoformat(matrix["generated_at"])
    resolved_run_id = run_id or f"{timestamp.strftime('%Y-%m-%d_%H%M%S')}_phase11h_a_offline"
    run_dir = root / "runs" / resolved_run_id
    latest_dir = root / "latest"
    for directory in (run_dir, latest_dir):
        _write_json(directory / "profile_readiness_matrix.json", matrix)
        (directory / "profile_readiness_matrix.md").write_text(
            _matrix_markdown(matrix), encoding="utf-8"
        )
        _write_csv(directory / "profile_readiness_matrix.csv", matrix["rows"])
        _write_json(directory / "operator_status.json", status)
        (directory / "operator_status.md").write_text(
            _status_markdown(status), encoding="utf-8"
        )
    return {
        "run_id": resolved_run_id,
        "run_dir": str(run_dir),
        "latest_dir": str(latest_dir),
    }


def load_latest_operator_command(output_root: Path | str | None = None) -> dict[str, Any]:
    root = Path(output_root or REPO_ROOT / "outputs") / "reviews" / "latest"
    status = _read_json(root / "operator_status.json")
    matrix = _read_json(root / "profile_readiness_matrix.json")
    return {
        "available": bool(status or matrix),
        "operator_status": status,
        "profile_matrix": matrix,
        "directory": str(root),
    }

