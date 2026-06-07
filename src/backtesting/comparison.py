"""Phase 10E research-only profile comparison reports.

Consumes one multi-profile ``BacktestResult`` produced by the existing replay
runner. It does not select trades, change profile behavior, or call live APIs.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.backtesting import mappers as M
from src.backtesting import reports
from src.backtesting.replay_runner import CONTROL_PROFILES, PRIMARY_PROFILES
from src.config.strategy_profiles import list_profiles, load_profile_file

MAIN_CONTROL_PROFILES: tuple[str, ...] = CONTROL_PROFILES[:4]
MIN_PROMOTION_TRADES = 10
PROMOTION_MAX_DRAWDOWN_PCT = 10.0
AVOID_MAX_DRAWDOWN_PCT = 15.0

RANKING_METHOD = (
    "Research score = 50 + expectancy component (-20..20) + profit-factor "
    "component (-20..20) + return component (-20..20) + trade-count component "
    "(0..15) - low-trade floor penalty (0..20) - drawdown penalty (0..30) - "
    "consecutive-loss penalty (0..15). "
    "Ties sort by positive expectancy, lower max drawdown %, higher profit "
    "factor, trade count, then total P&L."
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def custom_profile_ids() -> list[str]:
    """Return valid profiles without a built-in preset kind."""
    out: list[str] = []
    for loaded in list_profiles():
        if loaded.ok and loaded.profile is not None and not loaded.profile.preset_kind:
            out.append(loaded.profile.profile_id)
    return out


def resolve_comparison_profiles(profile_args: str | Iterable[str]) -> list[str]:
    """Resolve comparison cohorts or an explicit comma/space-separated list.

    Comparison ``all-main`` deliberately includes the four primary dynamic
    profiles and their four paired call-only controls. Research/observe profiles
    remain available through ``all`` or explicit selection.
    """
    raw = [profile_args] if isinstance(profile_args, str) else list(profile_args)
    tokens: list[str] = []
    for item in raw:
        tokens.extend(part.strip() for part in str(item or "").split(",") if part.strip())
    aliases = {
        "all-main": [*PRIMARY_PROFILES, *MAIN_CONTROL_PROFILES],
        "all_main": [*PRIMARY_PROFILES, *MAIN_CONTROL_PROFILES],
        "main": [*PRIMARY_PROFILES, *MAIN_CONTROL_PROFILES],
        "dynamic-only": list(PRIMARY_PROFILES),
        "dynamic": list(PRIMARY_PROFILES),
        "main-dynamic": list(PRIMARY_PROFILES),
        "controls-only": list(MAIN_CONTROL_PROFILES),
        "controls": list(MAIN_CONTROL_PROFILES),
        "all": [*PRIMARY_PROFILES, *CONTROL_PROFILES],
        "everything": [*PRIMARY_PROFILES, *CONTROL_PROFILES],
        "custom": custom_profile_ids(),
    }
    resolved: list[str] = []
    for token in tokens or ["all-main"]:
        resolved.extend(aliases.get(token.lower(), [token]))
    return _dedupe(resolved)


def _profile_metadata(profile_id: str) -> dict[str, Any]:
    loaded = load_profile_file(profile_id)
    if not loaded.ok or loaded.profile is None:
        return {
            "profile_id": profile_id,
            "profile_name": profile_id,
            "profile_kind": "custom",
            "entry_window": None,
        }
    profile = loaded.profile
    return {
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "profile_kind": profile.preset_kind or "custom",
        "entry_window": profile.target_time or profile.entry_window_start,
    }


def ranking_components(metric_row: dict[str, Any]) -> dict[str, float]:
    """Transparent deterministic research score components."""
    balance = max(_f(metric_row.get("starting_balance")), 1.0)
    expectancy_pct = _f(metric_row.get("expectancy_dollars")) / balance * 100.0
    profit_factor = _f(metric_row.get("profit_factor"))
    return_pct = _f(metric_row.get("return_pct"))
    trades = max(0.0, _f(metric_row.get("total_trades")))
    drawdown_pct = max(0.0, _f(metric_row.get("max_drawdown_pct")))
    loss_streak = max(0.0, _f(metric_row.get("max_consecutive_losses")))
    return {
        "rank_expectancy_component": round(_clamp(expectancy_pct * 10.0, -20.0, 20.0), 4),
        "rank_profit_factor_component": round(
            _clamp((profit_factor - 1.0) * 10.0, -20.0, 20.0), 4
        ),
        "rank_return_component": round(_clamp(return_pct * 2.0, -20.0, 20.0), 4),
        "rank_trade_count_component": round(min(trades, 20.0) / 20.0 * 15.0, 4),
        "rank_low_trade_penalty": round(
            max(0.0, MIN_PROMOTION_TRADES - trades) / MIN_PROMOTION_TRADES * 20.0, 4
        ),
        "rank_drawdown_penalty": round(min(drawdown_pct, 20.0) / 20.0 * 30.0, 4),
        "rank_loss_streak_penalty": round(min(loss_streak, 5.0) / 5.0 * 15.0, 4),
    }


def ranking_score(metric_row: dict[str, Any]) -> float:
    parts = ranking_components(metric_row)
    return round(
        50.0
        + parts["rank_expectancy_component"]
        + parts["rank_profit_factor_component"]
        + parts["rank_return_component"]
        + parts["rank_trade_count_component"]
        - parts["rank_low_trade_penalty"]
        - parts["rank_drawdown_penalty"]
        - parts["rank_loss_streak_penalty"],
        4,
    )


def promotion_label(metric_row: dict[str, Any], *, profile_kind: str = "") -> tuple[str, str]:
    """Return a research-only candidate label and its deterministic reason."""
    kind = str(profile_kind or "").lower()
    trades = int(_f(metric_row.get("total_trades")))
    expectancy = _f(metric_row.get("expectancy_dollars"))
    pf = _f(metric_row.get("profit_factor"))
    dd_pct = _f(metric_row.get("max_drawdown_pct"))
    if kind in {"control", "observe"}:
        return "Avoid / Control Only", f"{kind.title()} profile retained for comparison only."
    if trades < MIN_PROMOTION_TRADES:
        return "Needs More Data", f"{trades} trades is below the {MIN_PROMOTION_TRADES}-trade floor."
    if expectancy <= 0 or pf <= 1.0 or dd_pct > AVOID_MAX_DRAWDOWN_PCT:
        return (
            "Avoid / Control Only",
            "Negative expectancy, profit factor at or below 1, or excessive drawdown.",
        )
    if dd_pct <= PROMOTION_MAX_DRAWDOWN_PCT:
        return (
            "Promote to Live Paper Candidate",
            "Positive expectancy, profit factor above 1, sufficient trades, and controlled drawdown.",
        )
    return "Watchlist", "Positive profile, but drawdown is above the promotion threshold."


def _metric_row(
    result: Any,
    profile_id: str,
    *,
    starting_balance: float,
    contracts: int,
) -> dict[str, Any]:
    trades = [t for t in result.trades if t.get("profile_id") == profile_id]
    candidates = [c for c in result.candidates if c.get("profile_id") == profile_id]
    meta = _profile_metadata(profile_id)
    metric = reports.metrics(trades, starting_balance=starting_balance, contracts=contracts)
    label, reason = promotion_label(metric, profile_kind=meta["profile_kind"])
    parts = ranking_components(metric)
    return {
        **meta,
        "candidates": len(candidates),
        "selected_trades": metric["total_trades"],
        **metric,
        "average_credit_dollars_per_contract": (
            round(_f(metric.get("avg_credit_points")) * 100.0, 2)
            if metric.get("avg_credit_points") is not None else None
        ),
        "ranking_score": ranking_score(metric),
        **parts,
        "promotion_status": label,
        "promotion_reason": reason,
    }


def profile_summary(result: Any) -> list[dict[str, Any]]:
    starting_balance = _f(result.run_config.get("starting_balance"))
    contracts = int(_f(result.run_config.get("contracts"), 1.0))
    return [
        _metric_row(
            result,
            profile_id,
            starting_balance=starting_balance,
            contracts=contracts,
        )
        for profile_id in result.run_config.get("profiles", [])
    ]


def profile_rankings(result: Any) -> list[dict[str, Any]]:
    rows = profile_summary(result)

    def _sort(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -_f(row.get("ranking_score")),
            -int(_f(row.get("expectancy_dollars")) > 0),
            _f(row.get("max_drawdown_pct")),
            -_f(row.get("profit_factor")),
            -_f(row.get("total_trades")),
            -_f(row.get("total_pnl_dollars")),
            str(row.get("profile_id")),
        )

    ranked = sorted(rows, key=_sort)
    return [{"rank": index, **row} for index, row in enumerate(ranked, start=1)]


def _group_metrics(
    result: Any,
    rows: list[dict[str, Any]],
    *,
    group_name: str,
    group_value: Any,
) -> dict[str, Any]:
    starting_balance = _f(result.run_config.get("starting_balance"))
    contracts = int(_f(result.run_config.get("contracts"), 1.0))
    metric = reports.metrics(rows, starting_balance=starting_balance, contracts=contracts)
    return {group_name: group_value, **metric}


def dynamic_vs_control(result: Any) -> list[dict[str, Any]]:
    kinds = {
        pid: _profile_metadata(pid)["profile_kind"]
        for pid in result.run_config.get("profiles", [])
    }
    rows: list[dict[str, Any]] = []
    for kind in ("dynamic", "control"):
        pids = [pid for pid, value in kinds.items() if value == kind]
        trades = [t for t in result.trades if t.get("profile_id") in pids]
        row = _group_metrics(result, trades, group_name="profile_group", group_value=kind)
        row["profiles"] = len(pids)
        row["profile_ids"] = "; ".join(pids)
        rows.append(row)
    return rows


def breakdown_by_profile(result: Any, key: str, label: str) -> list[dict[str, Any]]:
    """Metrics grouped by profile and one research dimension."""
    rows: list[dict[str, Any]] = []
    for profile_id in result.run_config.get("profiles", []):
        profile_trades = [t for t in result.trades if t.get("profile_id") == profile_id]
        values = sorted({t.get(key) for t in profile_trades}, key=lambda value: str(value))
        for value in values:
            grouped = [t for t in profile_trades if t.get(key) == value]
            rows.append({
                "profile_id": profile_id,
                "profile_name": _profile_metadata(profile_id)["profile_name"],
                **_group_metrics(result, grouped, group_name=label, group_value=value),
            })
    return rows


def comparison_narrative(result: Any, rankings: list[dict[str, Any]]) -> str:
    sessions = int(result.counters.get("dates_evaluated", 0))
    symbol = str(result.run_config.get("symbol") or "Unknown")
    dte = str(result.run_config.get("dte") or "0DTE")
    active = [row for row in rankings if int(_f(row.get("total_trades"))) > 0]
    if not active:
        return (
            f"Across {sessions} {symbol} {dte} sessions, none of the compared profiles "
            "selected a trade. Review candidate blockers before considering promotion."
        )
    best_exp = max(active, key=lambda row: (_f(row.get("expectancy_dollars")), -_f(row.get("max_drawdown_pct"))))
    best_dd = min(active, key=lambda row: (_f(row.get("max_drawdown_pct")), -_f(row.get("expectancy_dollars"))))
    best_return = max(active, key=lambda row: _f(row.get("return_pct")))
    parts = [
        f"Across {sessions} {symbol} {dte} sessions, {best_exp['profile_name']} had the "
        f"strongest expectancy at ${_f(best_exp.get('expectancy_dollars')):,.2f} per trade "
        f"with {int(_f(best_exp.get('total_trades')))} selected trades.",
        f"{best_dd['profile_name']} had the cleanest max drawdown at "
        f"{_f(best_dd.get('max_drawdown_pct')):.2f}%.",
        f"{best_return['profile_name']} produced the best return at "
        f"{_f(best_return.get('return_pct')):.2f}%.",
    ]
    groups = {row["profile_group"]: row for row in dynamic_vs_control(result)}
    dynamic = groups.get("dynamic", {})
    control = groups.get("control", {})
    if int(_f(dynamic.get("total_trades"))) and int(_f(control.get("total_trades"))):
        delta = _f(dynamic.get("total_pnl_dollars")) - _f(control.get("total_pnl_dollars"))
        direction = "outperformed" if delta >= 0 else "underperformed"
        parts.append(
            f"Dynamic profiles {direction} call-only controls by ${abs(delta):,.2f} "
            "in pooled historical P&L; this is research context only."
        )
    candidates = [
        row["profile_name"]
        for row in rankings
        if row.get("promotion_status") == "Promote to Live Paper Candidate"
    ]
    if candidates:
        parts.append("Research-only live-paper candidates: " + ", ".join(candidates) + ".")
    else:
        parts.append("No profile currently clears the research-only live-paper promotion rules.")
    return " ".join(parts)


def build_comparison_reports(result: Any) -> dict[str, Any]:
    summary = profile_summary(result)
    rankings = profile_rankings(result)
    narrative = comparison_narrative(result, rankings)
    return {
        "comparison_summary": summary,
        "profile_rankings": rankings,
        "dynamic_vs_control": dynamic_vs_control(result),
        "by_profile": summary,
        "by_side": breakdown_by_profile(result, "side", "side"),
        "by_exit_reason": breakdown_by_profile(result, "exit_reason", "exit_reason"),
        "by_corridor": breakdown_by_profile(result, "corridor_valid", "corridor_valid"),
        "by_wds_tier": breakdown_by_profile(result, "wds_tier", "wds_tier"),
        "by_entry_window": breakdown_by_profile(result, "entry_target", "entry_window"),
        "trades": result.trades,
        "candidates": result.candidates,
        "narrative": narrative,
    }


def comparison_base() -> Path:
    return M.output_base() / "comparisons"


def comparison_latest_dir() -> Path:
    path = comparison_base() / "latest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def comparison_run_dir(stamp: str, label: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(label))[:48] or "compare"
    path = comparison_base() / "runs" / f"{stamp}_{safe}"
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


def write_comparison_reports(
    result: Any,
    out_dirs: list[Path],
    *,
    stamp: str | None = None,
) -> list[Path]:
    tables = build_comparison_reports(result)
    promotions = Counter(row["promotion_status"] for row in tables["profile_rankings"])
    run_config = {
        **result.run_config,
        "stamp": stamp,
        "comparison": True,
        "ranking_method": RANKING_METHOD,
        "promotion_rules": {
            "minimum_trades": MIN_PROMOTION_TRADES,
            "promotion_max_drawdown_pct": PROMOTION_MAX_DRAWDOWN_PCT,
            "avoid_max_drawdown_pct": AVOID_MAX_DRAWDOWN_PCT,
        },
        "promotion_counts": dict(promotions),
        "counters": result.counters,
        "no_broker": True,
        "no_execution": True,
        "no_order_preview": True,
    }
    names = (
        "comparison_summary", "profile_rankings", "dynamic_vs_control", "by_profile",
        "by_side", "by_exit_reason", "by_corridor", "by_wds_tier", "by_entry_window",
        "trades", "candidates",
    )
    written: list[Path] = []
    for directory in out_dirs:
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            _write_csv(directory / f"{name}.csv", tables[name])
        (directory / "run_config.json").write_text(
            json.dumps(run_config, indent=2, default=str), encoding="utf-8"
        )
        (directory / "narrative_summary.md").write_text(
            "# Backtest Comparison Summary\n\n" + tables["narrative"] + "\n",
            encoding="utf-8",
        )
        written.append(directory)
    return written
