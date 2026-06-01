"""Manual trade tracker — Streamlit-facing helpers + tiny P&L math.

`record_manual_trade(...)` appends to outputs/runs/{date}/manual_trades.csv
                              AND mirrors to outputs/latest/manual_trades.csv.
`record_paper_trade(...)`  appends to outputs/runs/{date}/paper_trades.csv
                              AND mirrors to outputs/latest/paper_trades.csv.
`snapshot_positions(...)`  overwrites outputs/runs/{date}/paper_positions.csv
                              AND outputs/latest/paper_positions.csv.
`append_equity_point(...)` appends to both per-day and outputs/latest equity-curve CSVs.

The math helpers (`unrealized_pnl_dollars`, `realized_pnl_dollars`,
`build_manual_trade_record`) live here so the Streamlit form, scanner runner,
and tests all use the same arithmetic — no duplicated P&L formulas.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.paper.positions import PaperPosition
from src.risk.limits import (
    planned_loss_dollars,
    theoretical_max_loss_dollars,
)
from src.storage.csv_writer import append_csv_row, write_csv_snapshot
from src.storage.paths import (
    latest_dir,
    manual_trades_path,
    paper_equity_curve_path,
    paper_positions_path,
    paper_trades_path,
)

OPTION_MULTIPLIER = 100

MANUAL_FIELDS = [
    "ts", "strategy_id", "side", "symbol", "expiry",
    "short_strike", "long_strike", "credit", "contracts",
    "spread_width", "entry_spot", "stop_variant", "profit_target",
    "planned_loss_dollars", "theoretical_max_loss_dollars",
    "current_mark", "unrealized_pnl",
    "exit_ts", "exit_debit", "exit_reason", "realized_pnl",
    "notes",
]

PAPER_FIELDS = [*MANUAL_FIELDS, "fill_source", "order_id"]

POSITION_SNAPSHOT_FIELDS = [
    "position_id", "strategy_id", "side", "symbol", "expiry",
    "short_strike", "long_strike", "credit", "contracts",
    "entry_time", "entry_spot", "stop_variant",
    "current_mark", "unrealized_pnl", "high_water_pnl", "low_water_pnl",
    "exit_time", "exit_debit", "exit_reason", "realized_pnl",
    "source", "notes",
]


# ──────────────────────────────────────────────────────────────────────
# P&L math (shared by UI + scanner + tests)
# ──────────────────────────────────────────────────────────────────────

def unrealized_pnl_dollars(credit: float, current_mark: float, contracts: int) -> float:
    """P&L if the trade closes RIGHT NOW at `current_mark` (per-spread debit)."""
    return (float(credit) - float(current_mark)) * OPTION_MULTIPLIER * int(contracts)


def realized_pnl_dollars(credit: float, exit_debit: float, contracts: int) -> float:
    """P&L when the trade actually closed at `exit_debit`."""
    return (float(credit) - float(exit_debit)) * OPTION_MULTIPLIER * int(contracts)


def spread_width_from_strikes(short_strike: float, long_strike: float) -> float:
    return abs(float(long_strike) - float(short_strike))


def build_manual_trade_record(
    *,
    ts: datetime,
    strategy_id: str,
    side: str,
    symbol: str,
    expiry: str,
    short_strike: float,
    long_strike: float,
    credit: float,
    contracts: int,
    entry_spot: float | None,
    stop_variant: str,
    profit_target: float | None,
    notes: str | None,
    current_mark: float | None = None,
    exit_ts: datetime | None = None,
    exit_debit: float | None = None,
    exit_reason: str | None = None,
) -> dict[str, Any]:
    """Build a fully-populated manual-trade CSV row (includes planned/theoretical $)."""
    spread_width = spread_width_from_strikes(short_strike, long_strike)
    max_risk = max(spread_width - float(credit), 0.0)
    planned_d = planned_loss_dollars(credit, max_risk, stop_variant, contracts)
    theoretical_d = theoretical_max_loss_dollars(max_risk, contracts)
    unrealized = (
        unrealized_pnl_dollars(credit, current_mark, contracts) if current_mark is not None else None
    )
    realized = (
        realized_pnl_dollars(credit, exit_debit, contracts) if exit_debit is not None else None
    )
    return {
        "ts": ts.isoformat() if ts else None,
        "strategy_id": strategy_id,
        "side": side,
        "symbol": symbol,
        "expiry": expiry,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "credit": round(float(credit), 4),
        "contracts": int(contracts),
        "spread_width": round(spread_width, 4),
        "entry_spot": entry_spot,
        "stop_variant": stop_variant,
        "profit_target": profit_target,
        "planned_loss_dollars": round(planned_d, 2),
        "theoretical_max_loss_dollars": round(theoretical_d, 2),
        "current_mark": current_mark,
        "unrealized_pnl": round(unrealized, 2) if unrealized is not None else None,
        "exit_ts": exit_ts.isoformat() if exit_ts else None,
        "exit_debit": exit_debit,
        "exit_reason": exit_reason,
        "realized_pnl": round(realized, 2) if realized is not None else None,
        "notes": notes,
    }


# ──────────────────────────────────────────────────────────────────────
# Persistence (run-dated + outputs/latest mirror)
# ──────────────────────────────────────────────────────────────────────

def record_manual_trade(
    output_root: Path,
    *,
    row: dict,
    date_str: str | None = None,
) -> Path:
    path = manual_trades_path(output_root, date_str)
    row_filtered = {k: row.get(k) for k in MANUAL_FIELDS}
    append_csv_row(path, row_filtered, MANUAL_FIELDS)
    append_csv_row(latest_dir(output_root) / "manual_trades.csv", row_filtered, MANUAL_FIELDS)
    return path


def record_paper_trade(
    output_root: Path,
    *,
    row: dict,
    date_str: str | None = None,
) -> Path:
    path = paper_trades_path(output_root, date_str)
    row_filtered = {k: row.get(k) for k in PAPER_FIELDS}
    append_csv_row(path, row_filtered, PAPER_FIELDS)
    append_csv_row(latest_dir(output_root) / "paper_trades.csv", row_filtered, PAPER_FIELDS)
    return path


def snapshot_positions(
    output_root: Path,
    positions: list[PaperPosition],
    date_str: str | None = None,
) -> Path:
    path = paper_positions_path(output_root, date_str)
    rows = [{k: getattr(p, k, None) for k in POSITION_SNAPSHOT_FIELDS} for p in positions]
    write_csv_snapshot(path, rows, POSITION_SNAPSHOT_FIELDS)
    write_csv_snapshot(latest_dir(output_root) / "paper_positions.csv", rows, POSITION_SNAPSHOT_FIELDS)
    return path


def append_equity_point(
    output_root: Path,
    ts: str,
    equity: float,
    date_str: str | None = None,
) -> Path:
    path = paper_equity_curve_path(output_root, date_str)
    row = {"ts": ts, "equity": equity}
    append_csv_row(path, row, ["ts", "equity"])
    append_csv_row(latest_dir(output_root) / "paper_equity_curve.csv", row, ["ts", "equity"])
    return path
