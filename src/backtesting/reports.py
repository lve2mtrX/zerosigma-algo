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
    "dominant_wing_side", "primary_gamma", "secondary_gamma",
    "score", "score_threshold", "rejected", "rejection_reasons", "quote_quality_bucket",
    "selector_score", "selector_score_components", "selector_reason",
    "selected_trade", "skipped_reason",
]

_EXIT_COLUMNS: list[str] = [
    "tp_mode", "sl_mode", "exit_timestamp", "exit_reason",
    "exit_debit_points", "exit_debit_dollars", "pnl_points", "pnl_dollars",
    "credit_kept_pct", "hold_minutes", "max_spot_after_entry", "min_spot_after_entry",
    "short_touched_after_entry", "long_touched_after_entry",
    "stop_triggered", "tp_triggered", "event_conflict",
    "missing_price_count", "snapshots_checked", "settlement_method",
]

_TRADE_COLUMNS: list[str] = _CANDIDATE_COLUMNS + _EXIT_COLUMNS

_METRIC_KEYS: list[str] = [
    "total_trades", "win_rate", "total_pnl_dollars", "avg_pnl_dollars", "expectancy_dollars",
    "gross_wins_dollars", "gross_losses_dollars", "profit_factor",
    "max_drawdown_dollars", "max_drawdown_duration_trades",
    "avg_credit_points", "avg_max_risk_points", "avg_distance_to_short",
    "tp_count", "sl_count", "eod_count", "skipped_exit_count",
    "call_selected", "put_selected", "active_corridor_trades", "inactive_corridor_trades",
    "wds_tier1", "wds_tier2", "wds_tier3", "wds_tier4",
]


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _drawdown(pnls: list[float]) -> tuple[float, int]:
    """Max peak-to-trough drawdown (dollars) + its longest underwater run (trades)."""
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
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
            max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2), max_dur


def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the full metric set for a list of trade records."""
    chrono = sorted(trades, key=lambda t: (str(t.get("date", "")), str(t.get("entry_timestamp", ""))))
    pnls = [p for p in (_f(t.get("pnl_dollars")) for t in chrono) if p is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(pnls)
    gross_w = sum(wins)
    gross_l = sum(losses)
    n = len(pnls)
    pf = (gross_w / abs(gross_l)) if gross_l < 0 else (float("inf") if gross_w > 0 else 0.0)
    max_dd, dd_dur = _drawdown(pnls)
    creds = [c for c in (_f(t.get("entry_credit_points")) for t in trades) if c is not None]
    risks = [r for r in (_f(t.get("max_risk_points")) for t in trades) if r is not None]
    dists = [d for d in (_f(t.get("distance_from_spot_to_short")) for t in trades) if d is not None]

    def _avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    def _tier(t: dict[str, Any], k: int) -> bool:
        return _f(t.get("wds_tier")) == k

    return {
        "total_trades": len(trades),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "total_pnl_dollars": round(total, 2),
        "avg_pnl_dollars": round(total / n, 2) if n else None,
        "expectancy_dollars": round(total / n, 2) if n else None,
        "gross_wins_dollars": round(gross_w, 2),
        "gross_losses_dollars": round(gross_l, 2),
        "profit_factor": (round(pf, 3) if pf != float("inf") else "inf"),
        "max_drawdown_dollars": max_dd,
        "max_drawdown_duration_trades": dd_dur,
        "avg_credit_points": _avg(creds),
        "avg_max_risk_points": _avg(risks),
        "avg_distance_to_short": _avg(dists),
        "tp_count": sum(1 for t in trades if t.get("exit_reason") == "TP"),
        "sl_count": sum(1 for t in trades if t.get("exit_reason") == "SL"),
        "eod_count": sum(1 for t in trades if t.get("exit_reason") == "EOD"),
        "skipped_exit_count": sum(1 for t in trades if t.get("exit_reason") == "SKIPPED"),
        "call_selected": sum(1 for t in trades if t.get("side") == "CALL_CREDIT"),
        "put_selected": sum(1 for t in trades if t.get("side") == "PUT_CREDIT"),
        "active_corridor_trades": sum(1 for t in trades if t.get("corridor_valid") is True),
        "inactive_corridor_trades": sum(1 for t in trades if t.get("corridor_valid") is not True),
        "wds_tier1": sum(1 for t in trades if _tier(t, 1)),
        "wds_tier2": sum(1 for t in trades if _tier(t, 2)),
        "wds_tier3": sum(1 for t in trades if _tier(t, 3)),
        "wds_tier4": sum(1 for t in trades if _tier(t, 4)),
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r.get(key), []).append(r)
    return out


def daily_pnl(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (profile_id, date): trades, wins, losses, pnl, cumulative pnl."""
    rows: list[dict[str, Any]] = []
    by_profile = _group(trades, "profile_id")
    for pid in sorted(by_profile, key=lambda x: str(x)):
        cum = 0.0
        by_date = _group(by_profile[pid], "date")
        for date in sorted(by_date, key=lambda x: str(x)):
            ts = by_date[date]
            pnls = [p for p in (_f(t.get("pnl_dollars")) for t in ts) if p is not None]
            day_pnl = sum(pnls)
            cum += day_pnl
            rows.append({
                "profile_id": pid, "date": date, "trades": len(ts),
                "wins": sum(1 for p in pnls if p > 0),
                "losses": sum(1 for p in pnls if p < 0),
                "pnl_dollars": round(day_pnl, 2), "cum_pnl_dollars": round(cum, 2),
            })
    return rows


def equity_curve(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-profile chronological equity curve with running peak + drawdown."""
    rows: list[dict[str, Any]] = []
    by_profile = _group(trades, "profile_id")
    for pid in sorted(by_profile, key=lambda x: str(x)):
        chrono = sorted(
            by_profile[pid],
            key=lambda t: (str(t.get("date", "")), str(t.get("entry_timestamp", ""))),
        )
        equity = 0.0
        peak = 0.0
        for i, t in enumerate(chrono, start=1):
            pnl = _f(t.get("pnl_dollars")) or 0.0
            equity += pnl
            peak = max(peak, equity)
            rows.append({
                "profile_id": pid, "trade_index": i, "date": t.get("date"),
                "entry_timestamp": t.get("entry_timestamp"), "side": t.get("side"),
                "pnl_dollars": round(pnl, 2), "cum_pnl_dollars": round(equity, 2),
                "peak_dollars": round(peak, 2), "drawdown_dollars": round(peak - equity, 2),
            })
    return rows


def _summary_rows(trades, candidates, key, label) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    by_trade = _group(trades, key)
    by_cand = _group(candidates, key)
    keys = sorted(set(by_trade) | set(by_cand), key=lambda x: str(x))
    rows: list[dict[str, Any]] = []
    for k in keys:
        m = metrics(by_trade.get(k, []))
        cands = by_cand.get(k, [])
        rows.append({
            label: k,
            "candidates": len(cands),
            "selected_trades": m["total_trades"],
            "skipped_candidates": sum(1 for c in cands if not c.get("selected_trade")),
            **m,
        })
    return rows


def summary_by_profile(trades, candidates) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(trades, candidates, "profile_id", "profile_id")


def summary_by_symbol(trades, candidates) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(trades, candidates, "symbol", "symbol")


def summary_by_corridor(trades, candidates) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    # Normalize the grouping key to a clean bool-ish label.
    def _norm(rows: list[dict[str, Any]]) -> None:
        for r in rows:
            r["corridor_valid"] = bool(r.get("corridor_valid") is True)
    t2 = [dict(t) for t in trades]
    c2 = [dict(c) for c in candidates]
    _norm(t2)
    _norm(c2)
    return _summary_rows(t2, c2, "corridor_valid", "corridor_valid")


def summary_by_wds_tier(trades, candidates) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return _summary_rows(trades, candidates, "wds_tier", "wds_tier")


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
    return {
        "trades": trades,
        "candidates": cands,
        "daily_pnl": daily_pnl(trades),
        "equity_curve": equity_curve(trades),
        "summary_by_profile": summary_by_profile(trades, cands),
        "summary_by_symbol": summary_by_symbol(trades, cands),
        "summary_by_corridor": summary_by_corridor(trades, cands),
        "summary_by_wds_tier": summary_by_wds_tier(trades, cands),
        "no_trade_reasons": result.no_trade_reasons,
    }


def write_reports(result, out_dirs: list[Path], *, stamp: str | None = None) -> list[Path]:  # type: ignore[no-untyped-def]
    """Write every report CSV + run_config.json into each directory in ``out_dirs``."""
    tables = build_reports(result)
    written: list[Path] = []
    run_config = {**result.run_config, "stamp": stamp, "counters": result.counters,
                  "overall": metrics(result.trades)}
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)
        _write_csv(d / "trades.csv", tables["trades"], _TRADE_COLUMNS)
        _write_csv(d / "candidates.csv", tables["candidates"], _CANDIDATE_COLUMNS)
        _write_csv(d / "daily_pnl.csv", tables["daily_pnl"])
        _write_csv(d / "equity_curve.csv", tables["equity_curve"])
        _write_csv(d / "summary_by_profile.csv", tables["summary_by_profile"])
        _write_csv(d / "summary_by_symbol.csv", tables["summary_by_symbol"])
        _write_csv(d / "summary_by_corridor.csv", tables["summary_by_corridor"])
        _write_csv(d / "summary_by_wds_tier.csv", tables["summary_by_wds_tier"])
        _write_csv(d / "no_trade_reasons.csv", tables["no_trade_reasons"],
                   ["date", "symbol", "profile_id", "reason"])
        (d / "run_config.json").write_text(
            json.dumps(run_config, indent=2, default=str), encoding="utf-8")
        written.append(d)
    return written
