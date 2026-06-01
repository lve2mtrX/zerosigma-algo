"""Manual trade tracker — Streamlit-facing helpers.

`record_manual_trade(...)` appends to outputs/runs/{date}/manual_trades.csv.
`record_paper_trade(...)` appends to outputs/runs/{date}/paper_trades.csv.
`snapshot_positions(...)` overwrites outputs/runs/{date}/paper_positions.csv.
"""

from __future__ import annotations

from pathlib import Path

from src.paper.positions import PaperPosition
from src.storage.csv_writer import append_csv_row, write_csv_snapshot
from src.storage.paths import (
    manual_trades_path,
    paper_equity_curve_path,
    paper_positions_path,
    paper_trades_path,
)

MANUAL_FIELDS = [
    "ts", "strategy_id", "side", "symbol", "expiry",
    "short_strike", "long_strike", "credit", "contracts",
    "entry_spot", "stop_variant", "profit_targets",
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


def record_manual_trade(
    output_root: Path,
    *,
    row: dict,
    date_str: str | None = None,
) -> Path:
    path = manual_trades_path(output_root, date_str)
    append_csv_row(path, {k: row.get(k) for k in MANUAL_FIELDS}, MANUAL_FIELDS)
    return path


def record_paper_trade(
    output_root: Path,
    *,
    row: dict,
    date_str: str | None = None,
) -> Path:
    path = paper_trades_path(output_root, date_str)
    append_csv_row(path, {k: row.get(k) for k in PAPER_FIELDS}, PAPER_FIELDS)
    return path


def snapshot_positions(
    output_root: Path,
    positions: list[PaperPosition],
    date_str: str | None = None,
) -> Path:
    path = paper_positions_path(output_root, date_str)
    rows = [{k: getattr(p, k, None) for k in POSITION_SNAPSHOT_FIELDS} for p in positions]
    write_csv_snapshot(path, rows, POSITION_SNAPSHOT_FIELDS)
    return path


def append_equity_point(
    output_root: Path,
    ts: str,
    equity: float,
    date_str: str | None = None,
) -> Path:
    path = paper_equity_curve_path(output_root, date_str)
    append_csv_row(path, {"ts": ts, "equity": equity}, ["ts", "equity"])
    return path
