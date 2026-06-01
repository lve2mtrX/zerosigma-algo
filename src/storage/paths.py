"""Path resolution for all generated outputs.

Honors `DATA_DIR` / `OUTPUT_DIR` env vars so the cockpit is portable.
Creates directories on demand.
"""

from __future__ import annotations

from pathlib import Path

from src.utils.time import today_et_date


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_dir(output_root: Path) -> Path:
    return _ensure(output_root / "latest")


def run_dir(output_root: Path, date_str: str | None = None) -> Path:
    d = date_str or today_et_date()
    return _ensure(output_root / "runs" / d)


def daily_dir(output_root: Path, date_str: str | None = None) -> Path:
    d = date_str or today_et_date()
    return _ensure(output_root / "daily" / d)


def decision_log_path(output_root: Path, date_str: str | None = None) -> Path:
    return run_dir(output_root, date_str) / "decision_log.jsonl"


def ranked_candidates_path(output_root: Path, date_str: str | None = None) -> Path:
    return run_dir(output_root, date_str) / "ranked_candidates.csv"


def manual_trades_path(output_root: Path, date_str: str | None = None) -> Path:
    return run_dir(output_root, date_str) / "manual_trades.csv"


def paper_trades_path(output_root: Path, date_str: str | None = None) -> Path:
    return run_dir(output_root, date_str) / "paper_trades.csv"


def paper_positions_path(output_root: Path, date_str: str | None = None) -> Path:
    return run_dir(output_root, date_str) / "paper_positions.csv"


def paper_equity_curve_path(output_root: Path, date_str: str | None = None) -> Path:
    return run_dir(output_root, date_str) / "paper_equity_curve.csv"


def missed_signals_path(output_root: Path, date_str: str | None = None) -> Path:
    return run_dir(output_root, date_str) / "missed_signals.csv"


def config_change_log_path(output_root: Path, date_str: str | None = None) -> Path:
    return run_dir(output_root, date_str) / "config_change_log.jsonl"


def eod_summary_md_path(output_root: Path, date_str: str | None = None) -> Path:
    return daily_dir(output_root, date_str) / "eod_summary.md"


def eod_summary_json_path(output_root: Path, date_str: str | None = None) -> Path:
    return daily_dir(output_root, date_str) / "eod_summary.json"
