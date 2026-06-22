"""Phase 10B — backtest reporting: aggregation + repo-local CSV/JSON writers.

Pure aggregation over the runner's trade/candidate records (no I/O except the
explicit ``write_reports`` sink). Produces the trade-level files plus daily P&L,
equity curve / drawdown, and summaries by profile / symbol / corridor / WDS tier.
Outputs land ONLY under ``outputs/backtests/`` — never the raw data folders.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

# ── column orders (stable headers even when a run is empty) ──────────────────

_CANDIDATE_COLUMNS: list[str] = [
    "symbol", "date", "dte", "profile_id", "preset_kind", "entry_target",
    "entry_timestamp", "entry_offset_minutes", "spot", "threshold", "volume_threshold",
    "threshold_scheme", "threshold_warning", "selector_mode",
    "side", "anchor_source", "wing_strike", "short_strike", "long_strike", "width_points",
    "entry_credit_points", "entry_credit_dollars", "max_risk_points", "max_risk_dollars",
    "distance_from_spot_to_short", "distance_pct_from_spot_to_short", "reward_risk",
    "corridor_valid", "cw1", "pw1", "corridor_reason", "active_wds", "raw_wds", "wds_tier",
    "dominant_wing_side", "primary_gamma", "secondary_gamma", "gamma_regime",
    "gamma_relationship",
    "score", "score_threshold", "rejected", "rejection_reasons", "quote_quality_bucket",
    "selector_score", "selector_score_components", "selector_reason",
    "rejection_type", "risk_rejection_type", "risk_rejection_reason",
    "quote_quality_reason", "candidate_passes_trade_filters",
    "candidate_passes_risk_filters", "candidate_passes_quote_filters",
    "candidate_passes_score_threshold", "candidate_passes_score_edge",
    "selector_eligible_base", "selector_blockers", "side_allowed_by_config",
    "selected_trade", "skipped_reason",
]

_PHASE11F_RESEARCH_COLUMNS: list[str] = [
    "regime_snapshot_json", "regime_label", "regime_confidence",
    "regime_quality_label", "regime_reason_codes", "regime_summary",
    "da_gex_signed", "maxvol", "maxvol_migration",
    "total_gex_bn", "total_raw_gex_bn", "total_dex_bn", "total_vex_bn", "total_cex_bn",
    "greek_api_available_fields", "greek_api_missing_fields",
    "greek_api_source_endpoint", "greek_api_units",
    "daily_regime_code", "daily_regime_label", "daily_regime_reason_codes",
    "da_gex_path_observations", "da_gex_sign_changes", "da_gex_path_summary",
    "context_regime_code", "context_regime_label", "context_regime_reason_codes",
    "opex_context", "days_to_opex", "expiration_context",
    "alerts_emitted", "alert_reason_codes",
]
_CANDIDATE_COLUMNS.extend(_PHASE11F_RESEARCH_COLUMNS)

_EXIT_COLUMNS: list[str] = [
    "contracts", "tp_mode", "sl_mode", "exit_timestamp", "exit_reason",
    "exit_debit_points", "exit_debit_dollars", "pnl_points", "pnl_dollars",
    "credit_kept_pct", "hold_minutes", "max_spot_after_entry", "min_spot_after_entry",
    "short_touched_after_entry", "long_touched_after_entry",
    "stop_triggered", "tp_triggered", "event_conflict",
    "missing_price_count", "snapshots_checked", "settlement_method",
]

_TRADE_COLUMNS: list[str] = _CANDIDATE_COLUMNS + _EXIT_COLUMNS

_NO_TRADE_COLUMNS: list[str] = [
    "date", "symbol", "dte", "profile_id", "entry_target", "entry_timestamp",
    "entry_offset_minutes", "status", "reason", "detail", "first_blocker",
    "candidate_count", "eligible_candidate_count", "selected_count",
    "risk_filtered_count", "quote_filtered_count", "score_filtered_count",
    "selector_filtered_count", "corridor_valid", "active_wds", "raw_wds", "wds_tier",
    "top_selector_reason", "top_risk_reason", "top_quote_reason",
    "side_allowed_by_config", "missing_price_count",
    "daily_regime_code", "daily_regime_label", "context_regime_code",
    "context_regime_label", "greek_api_available_fields", "greek_api_missing_fields",
    "alerts_emitted", "alert_reason_codes",
]

_METRIC_KEYS: list[str] = [
    "starting_balance", "contracts", "ending_balance", "return_pct",
    "total_trades", "win_rate", "total_pnl_dollars", "avg_pnl_dollars", "expectancy_dollars",
    "avg_win_dollars", "avg_loss_dollars", "largest_win_dollars", "largest_loss_dollars",
    "worst_trade_dollars", "avg_hold_minutes", "max_consecutive_losses",
    "gross_wins_dollars", "gross_losses_dollars", "profit_factor",
    "max_drawdown_dollars", "max_drawdown_pct", "max_drawdown_duration_trades",
    "best_day", "best_day_pnl_dollars", "worst_day", "worst_day_pnl_dollars",
    "avg_credit_points", "avg_max_risk_points", "avg_distance_to_short",
    "tp_count", "sl_count", "eod_count", "skipped_exit_count",
    "call_selected", "put_selected", "active_corridor_trades", "inactive_corridor_trades",
    "call_pnl_dollars", "put_pnl_dollars",
    "active_corridor_pnl_dollars", "inactive_corridor_pnl_dollars",
    "wds_tier1", "wds_tier2", "wds_tier3", "wds_tier4",
    "wds_tier1_pnl_dollars", "wds_tier2_pnl_dollars",
    "wds_tier3_pnl_dollars", "wds_tier4_pnl_dollars",
]


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _contracts_from(trades: list[dict[str, Any]], default: int = 1) -> int:
    for t in trades:
        try:
            c = int(float(t.get("contracts")))
        except (TypeError, ValueError):
            continue
        if c > 0:
            return c
    return default


def _drawdown(pnls: list[float], *, starting_balance: float = 0.0) -> tuple[float, float, int]:
    """Max peak-to-trough drawdown (dollars + pct of prior equity peak) and duration."""
    peak = float(starting_balance)
    equity = float(starting_balance)
    max_dd = 0.0
    max_dd_pct = 0.0
    cur_dur = 0
    max_dur = 0
    for p in pnls:
        equity += p
        if equity >= peak:
            peak = equity
            cur_dur = 0
        else:
            cur_dur += 1
            max_dur = max(max_dur, cur_dur)
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = (dd / peak * 100.0) if peak > 0 else 0.0
    return round(max_dd, 2), round(max_dd_pct, 4), max_dur


def _avg(xs: list[float], *, decimals: int = 4) -> float | None:
    return round(sum(xs) / len(xs), decimals) if xs else None


def _sum_pnl(rows: list[dict[str, Any]]) -> float:
    return round(sum(_f(t.get("pnl_dollars")) or 0.0 for t in rows), 2)


def _max_consecutive_losses(pnls: list[float]) -> int:
    run = 0
    worst = 0
    for pnl in pnls:
        if pnl < 0:
            run += 1
            worst = max(worst, run)
        else:
            run = 0
    return worst


def _day_pnls(trades: list[dict[str, Any]]) -> dict[str, float]:
    by_day: dict[str, float] = {}
    for t in trades:
        day = str(t.get("date") or "")
        if not day:
            continue
        by_day[day] = by_day.get(day, 0.0) + (_f(t.get("pnl_dollars")) or 0.0)
    return {day: round(pnl, 2) for day, pnl in by_day.items()}


def metrics(
    trades: list[dict[str, Any]],
    *,
    starting_balance: float = 0.0,
    contracts: int | None = None,
) -> dict[str, Any]:
    """Compute the full metric set for a list of trade records."""
    try:
        starting_balance = float(starting_balance or 0.0)
    except (TypeError, ValueError):
        starting_balance = 0.0
    qty = int(contracts) if contracts is not None else _contracts_from(trades)
    chrono = sorted(trades, key=lambda t: (str(t.get("date", "")), str(t.get("entry_timestamp", ""))))
    pnls = [p for p in (_f(t.get("pnl_dollars")) for t in chrono) if p is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(pnls)
    gross_w = sum(wins)
    gross_l = sum(losses)
    n = len(pnls)
    pf = (gross_w / abs(gross_l)) if gross_l < 0 else (float("inf") if gross_w > 0 else 0.0)
    max_dd, max_dd_pct, dd_dur = _drawdown(pnls, starting_balance=starting_balance)
    creds = [c for c in (_f(t.get("entry_credit_points")) for t in trades) if c is not None]
    risks = [r for r in (_f(t.get("max_risk_points")) for t in trades) if r is not None]
    dists = [d for d in (_f(t.get("distance_from_spot_to_short")) for t in trades) if d is not None]
    holds = [h for h in (_f(t.get("hold_minutes")) for t in trades) if h is not None]
    day_pnls = _day_pnls(chrono)
    best_day = max(day_pnls, key=day_pnls.get) if day_pnls else None
    worst_day = min(day_pnls, key=day_pnls.get) if day_pnls else None

    def _tier(t: dict[str, Any], k: int) -> bool:
        return _f(t.get("wds_tier")) == k

    call_trades = [t for t in trades if t.get("side") == "CALL_CREDIT"]
    put_trades = [t for t in trades if t.get("side") == "PUT_CREDIT"]
    active_corridor = [t for t in trades if t.get("corridor_valid") is True]
    inactive_corridor = [t for t in trades if t.get("corridor_valid") is not True]
    tiered = {k: [t for t in trades if _tier(t, k)] for k in range(1, 5)}

    return {
        "starting_balance": round(starting_balance, 2),
        "contracts": qty,
        "ending_balance": round(starting_balance + total, 2),
        "return_pct": round(total / starting_balance * 100.0, 4) if starting_balance > 0 else None,
        "total_trades": len(trades),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "total_pnl_dollars": round(total, 2),
        "avg_pnl_dollars": round(total / n, 2) if n else None,
        "expectancy_dollars": round(total / n, 2) if n else None,
        "avg_win_dollars": _avg(wins, decimals=2),
        "avg_loss_dollars": _avg(losses, decimals=2),
        "largest_win_dollars": round(max(wins), 2) if wins else None,
        "largest_loss_dollars": round(min(losses), 2) if losses else None,
        "worst_trade_dollars": round(min(pnls), 2) if pnls else None,
        "avg_hold_minutes": _avg(holds, decimals=1),
        "max_consecutive_losses": _max_consecutive_losses(pnls),
        "gross_wins_dollars": round(gross_w, 2),
        "gross_losses_dollars": round(gross_l, 2),
        "profit_factor": (round(pf, 3) if pf != float("inf") else "inf"),
        "max_drawdown_dollars": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_duration_trades": dd_dur,
        "best_day": best_day,
        "best_day_pnl_dollars": day_pnls.get(best_day) if best_day else None,
        "worst_day": worst_day,
        "worst_day_pnl_dollars": day_pnls.get(worst_day) if worst_day else None,
        "avg_credit_points": _avg(creds),
        "avg_max_risk_points": _avg(risks),
        "avg_distance_to_short": _avg(dists),
        "tp_count": sum(1 for t in trades if t.get("exit_reason") == "TP"),
        "sl_count": sum(1 for t in trades if t.get("exit_reason") == "SL"),
        "eod_count": sum(1 for t in trades if t.get("exit_reason") == "EOD"),
        "skipped_exit_count": sum(1 for t in trades if t.get("exit_reason") == "SKIPPED"),
        "call_selected": sum(1 for t in trades if t.get("side") == "CALL_CREDIT"),
        "put_selected": sum(1 for t in trades if t.get("side") == "PUT_CREDIT"),
        "call_pnl_dollars": _sum_pnl(call_trades),
        "put_pnl_dollars": _sum_pnl(put_trades),
        "active_corridor_trades": sum(1 for t in trades if t.get("corridor_valid") is True),
        "inactive_corridor_trades": sum(1 for t in trades if t.get("corridor_valid") is not True),
        "active_corridor_pnl_dollars": _sum_pnl(active_corridor),
        "inactive_corridor_pnl_dollars": _sum_pnl(inactive_corridor),
        "wds_tier1": len(tiered[1]),
        "wds_tier2": len(tiered[2]),
        "wds_tier3": len(tiered[3]),
        "wds_tier4": len(tiered[4]),
        "wds_tier1_pnl_dollars": _sum_pnl(tiered[1]),
        "wds_tier2_pnl_dollars": _sum_pnl(tiered[2]),
        "wds_tier3_pnl_dollars": _sum_pnl(tiered[3]),
        "wds_tier4_pnl_dollars": _sum_pnl(tiered[4]),
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r.get(key), []).append(r)
    return out


def daily_pnl(
    trades: list[dict[str, Any]],
    *,
    starting_balance: float = 0.0,
    contracts: int | None = None,
) -> list[dict[str, Any]]:
    """One row per (profile_id, date): trades, wins, losses, pnl, cumulative pnl."""
    rows: list[dict[str, Any]] = []
    qty = int(contracts) if contracts is not None else _contracts_from(trades)
    by_profile = _group(trades, "profile_id")
    for pid in sorted(by_profile, key=lambda x: str(x)):
        cum = 0.0
        by_date = _group(by_profile[pid], "date")
        for date in sorted(by_date, key=lambda x: str(x)):
            ts = by_date[date]
            pnls = [p for p in (_f(t.get("pnl_dollars")) for t in ts) if p is not None]
            day_pnl = sum(pnls)
            cum += day_pnl
            equity = float(starting_balance or 0.0) + cum
            rows.append({
                "profile_id": pid, "date": date, "trades": len(ts),
                "starting_balance": round(float(starting_balance or 0.0), 2),
                "contracts": qty,
                "wins": sum(1 for p in pnls if p > 0),
                "losses": sum(1 for p in pnls if p < 0),
                "pnl_dollars": round(day_pnl, 2), "cum_pnl_dollars": round(cum, 2),
                "account_equity": round(equity, 2),
                "account_return_pct": (
                    round(cum / float(starting_balance) * 100.0, 4)
                    if float(starting_balance or 0.0) > 0 else None
                ),
            })
    return rows


def equity_curve(
    trades: list[dict[str, Any]],
    *,
    starting_balance: float = 0.0,
    contracts: int | None = None,
) -> list[dict[str, Any]]:
    """Per-profile chronological equity curve with running peak + drawdown."""
    rows: list[dict[str, Any]] = []
    qty = int(contracts) if contracts is not None else _contracts_from(trades)
    base = float(starting_balance or 0.0)
    by_profile = _group(trades, "profile_id")
    for pid in sorted(by_profile, key=lambda x: str(x)):
        chrono = sorted(
            by_profile[pid],
            key=lambda t: (str(t.get("date", "")), str(t.get("entry_timestamp", ""))),
        )
        equity = 0.0
        peak = base
        for i, t in enumerate(chrono, start=1):
            pnl = _f(t.get("pnl_dollars")) or 0.0
            equity += pnl
            account_equity = base + equity
            peak = max(peak, account_equity)
            dd = peak - account_equity
            rows.append({
                "profile_id": pid, "trade_index": i, "date": t.get("date"),
                "entry_timestamp": t.get("entry_timestamp"), "side": t.get("side"),
                "starting_balance": round(base, 2), "contracts": qty,
                "pnl_dollars": round(pnl, 2), "cum_pnl_dollars": round(equity, 2),
                "account_equity": round(account_equity, 2),
                "account_return_pct": round(equity / base * 100.0, 4) if base > 0 else None,
                "peak_dollars": round(peak, 2), "drawdown_dollars": round(dd, 2),
                "drawdown_pct": round(dd / peak * 100.0, 4) if peak > 0 else None,
            })
    return rows


def _summary_rows(
    trades, candidates, key, label, *, starting_balance: float = 0.0,
    contracts: int | None = None,
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    by_trade = _group(trades, key)
    by_cand = _group(candidates, key)
    keys = sorted(set(by_trade) | set(by_cand), key=lambda x: str(x))
    rows: list[dict[str, Any]] = []
    for k in keys:
        m = metrics(
            by_trade.get(k, []),
            starting_balance=starting_balance,
            contracts=contracts,
        )
        cands = by_cand.get(k, [])
        rows.append({
            label: k,
            "candidates": len(cands),
            "selected_trades": m["total_trades"],
            "skipped_candidates": sum(1 for c in cands if not c.get("selected_trade")),
            **m,
        })
    return rows


def summary_by_profile(
    trades, candidates, *, starting_balance: float = 0.0, contracts: int | None = None,
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(
        trades, candidates, "profile_id", "profile_id",
        starting_balance=starting_balance, contracts=contracts,
    )


def summary_by_symbol(
    trades, candidates, *, starting_balance: float = 0.0, contracts: int | None = None,
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(
        trades, candidates, "symbol", "symbol",
        starting_balance=starting_balance, contracts=contracts,
    )


def summary_by_corridor(
    trades, candidates, *, starting_balance: float = 0.0, contracts: int | None = None,
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    # Normalize the grouping key to a clean bool-ish label.
    def _norm(rows: list[dict[str, Any]]) -> None:
        for r in rows:
            r["corridor_valid"] = bool(r.get("corridor_valid") is True)
    t2 = [dict(t) for t in trades]
    c2 = [dict(c) for c in candidates]
    _norm(t2)
    _norm(c2)
    return _summary_rows(
        t2, c2, "corridor_valid", "corridor_valid",
        starting_balance=starting_balance, contracts=contracts,
    )


def summary_by_wds_tier(
    trades, candidates, *, starting_balance: float = 0.0, contracts: int | None = None,
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(
        trades, candidates, "wds_tier", "wds_tier",
        starting_balance=starting_balance, contracts=contracts,
    )


def summary_by_side(
    trades, candidates, *, starting_balance: float = 0.0, contracts: int | None = None,
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(
        trades, candidates, "side", "side",
        starting_balance=starting_balance, contracts=contracts,
    )


def summary_by_exit_reason(
    trades, *, starting_balance: float = 0.0, contracts: int | None = None,
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(
        trades, [], "exit_reason", "exit_reason",
        starting_balance=starting_balance, contracts=contracts,
    )


def summary_by_day(
    trades, candidates, *, starting_balance: float = 0.0, contracts: int | None = None,
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(
        trades, candidates, "date", "date",
        starting_balance=starting_balance, contracts=contracts,
    )


# ── writers ──────────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    cols = columns or (list(rows[0].keys()) if rows else [])
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def build_reports(result) -> dict[str, list[dict[str, Any]]]:  # type: ignore[no-untyped-def]
    """Compute every report table (pure — no I/O). Returns name -> rows."""
    trades, cands = result.trades, result.candidates
    starting_balance = float(result.run_config.get("starting_balance") or 0.0)
    contracts = int(result.run_config.get("contracts") or _contracts_from(trades))
    return {
        "trades": trades,
        "candidates": cands,
        "daily_pnl": daily_pnl(trades, starting_balance=starting_balance, contracts=contracts),
        "equity_curve": equity_curve(trades, starting_balance=starting_balance, contracts=contracts),
        "summary_by_profile": summary_by_profile(
            trades, cands, starting_balance=starting_balance, contracts=contracts),
        "summary_by_symbol": summary_by_symbol(
            trades, cands, starting_balance=starting_balance, contracts=contracts),
        "summary_by_side": summary_by_side(
            trades, cands, starting_balance=starting_balance, contracts=contracts),
        "summary_by_exit_reason": summary_by_exit_reason(
            trades, starting_balance=starting_balance, contracts=contracts),
        "summary_by_corridor": summary_by_corridor(
            trades, cands, starting_balance=starting_balance, contracts=contracts),
        "summary_by_wds_tier": summary_by_wds_tier(
            trades, cands, starting_balance=starting_balance, contracts=contracts),
        "summary_by_day": summary_by_day(
            trades, cands, starting_balance=starting_balance, contracts=contracts),
        "no_trade_reasons": result.no_trade_reasons,
    }


def write_reports(result, out_dirs: list[Path], *, stamp: str | None = None) -> list[Path]:  # type: ignore[no-untyped-def]
    """Write every report CSV + run_config.json into each directory in ``out_dirs``."""
    tables = build_reports(result)
    written: list[Path] = []
    starting_balance = float(result.run_config.get("starting_balance") or 0.0)
    contracts = int(result.run_config.get("contracts") or _contracts_from(result.trades))
    run_config = {**result.run_config, "stamp": stamp, "counters": result.counters,
                  "overall": metrics(
                      result.trades,
                      starting_balance=starting_balance,
                      contracts=contracts,
                  )}
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)
        _write_csv(d / "trades.csv", tables["trades"], _TRADE_COLUMNS)
        _write_csv(d / "candidates.csv", tables["candidates"], _CANDIDATE_COLUMNS)
        _write_csv(d / "daily_pnl.csv", tables["daily_pnl"])
        _write_csv(d / "equity_curve.csv", tables["equity_curve"])
        _write_csv(d / "summary_by_profile.csv", tables["summary_by_profile"])
        _write_csv(d / "summary_by_symbol.csv", tables["summary_by_symbol"])
        _write_csv(d / "summary_by_side.csv", tables["summary_by_side"])
        _write_csv(d / "summary_by_exit_reason.csv", tables["summary_by_exit_reason"])
        _write_csv(d / "summary_by_corridor.csv", tables["summary_by_corridor"])
        _write_csv(d / "summary_by_wds_tier.csv", tables["summary_by_wds_tier"])
        _write_csv(d / "summary_by_day.csv", tables["summary_by_day"])
        _write_csv(d / "no_trade_reasons.csv", tables["no_trade_reasons"], _NO_TRADE_COLUMNS)
        (d / "run_config.json").write_text(
            json.dumps(run_config, indent=2, default=str), encoding="utf-8")
        written.append(d)
    return written
