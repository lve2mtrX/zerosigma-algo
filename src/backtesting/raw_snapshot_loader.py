"""Phase 10A — pure raw daily-CSV loader for the local historical backtester.

Loads one symbol/date/DTE raw snapshot CSV, parses MIXED timestamp formats to
America/New_York wall time, filters RTH, and detects the symbol-specific spot
column. No pandas; stdlib only. No network, no writes, no execution.

Path resolution (no hardcoded username):
    --trading-root CLI  →  $ZSA_TRADING_ROOT  →  ~/Dropbox/Trading
The exposures live under  <trading_root>/TOS Data/Daily Exposures/<SYMBOL[/_1DTE]>.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.backtesting import schemas

_ET = ZoneInfo("America/New_York")
_TS_COLS = ("timestamp", "Timestamp", "datetime")
_SESSION_COLS = ("session", "Session")


# ── path resolution (env / home / CLI; never hardcode a user) ────────────────

def trading_root(cli_root: str | None = None) -> Path:
    if cli_root:
        return Path(cli_root).expanduser()
    env = os.environ.get("ZSA_TRADING_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Dropbox" / "Trading"


def exposures_dir(symbol: str, dte: str, *, root: str | Path | None = None) -> Path:
    base = Path(root).expanduser() if root else trading_root(None)
    return base / "TOS Data" / "Daily Exposures" / schemas.exposures_subdir(symbol, dte)


def _date_from_name(name: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else None


def list_raw_files(symbol: str, dte: str, *, root: str | Path | None = None) -> list[Path]:
    """Sorted raw CSVs for a symbol + DTE bucket (0DTE excludes 1DTE files)."""
    d = exposures_dir(symbol, dte, root=root)
    if not d.is_dir():
        return []
    files = sorted(d.glob(schemas.raw_glob(symbol, dte)))
    if dte == schemas.DTE_0:
        files = [p for p in files if "1DTE" not in p.name.upper()]
    return files


def available_dates(symbol: str, dte: str, *, root: str | Path | None = None) -> list[str]:
    return [d for d in (_date_from_name(p.name) for p in list_raw_files(symbol, dte, root=root)) if d]


def file_for_date(symbol: str, dte: str, date: str, *, root: str | Path | None = None) -> Path | None:
    for p in list_raw_files(symbol, dte, root=root):
        if _date_from_name(p.name) == date:
            return p
    return None


# ── timestamp parsing (mixed formats → naive ET wall time) ───────────────────

def parse_timestamp(value: Any) -> datetime | None:
    """Parse a mixed-format timestamp → naive America/New_York wall time.

    Handles ISO with offset ('2026-06-03T12:45:15-04:00'), ISO/space without
    offset ('2026-06-03 12:45:00'), and compact ('20260603 124500'). tz-aware
    values convert to ET then drop tz; naive values are assumed already ET."""
    if value is None:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    if not s:
        return None
    dt: datetime | None = None
    for cand in (s, s.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(cand)
            break
        except ValueError:
            dt = None
    if dt is None:
        m = re.match(r"^(\d{4})(\d{2})(\d{2})[ T]?(\d{2})(\d{2})(\d{2})?", s)
        if m:
            y, mo, d, h, mi, se = m.groups()
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(se or 0))
        else:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_ET).replace(tzinfo=None)
    return dt


def _is_rth(dt: datetime | None) -> bool:
    if dt is None:
        return False
    return time(*schemas.RTH_START) <= dt.time() <= time(*schemas.RTH_END)


def _get(row: dict, names: tuple[str, ...]) -> Any:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


# ── load ─────────────────────────────────────────────────────────────────────

def load_raw_rows(path: str | Path, symbol: str, *, rth_only: bool = True) -> list[dict]:
    """Read a raw daily CSV → row dicts, each with a parsed `_ts` (naive ET). RTH
    filter uses the `session` column when present, else the RTH time window."""
    out: list[dict] = []
    with Path(path).open("r", encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            ts = parse_timestamp(_get(raw, _TS_COLS))
            if ts is None:
                continue
            if rth_only:
                sess = _get(raw, _SESSION_COLS)
                if sess is not None:
                    if str(sess).strip().upper() != "RTH":
                        continue
                elif not _is_rth(ts):
                    continue
            raw["_ts"] = ts
            out.append(raw)
    return out


def available_timestamps(rows: list[dict]) -> list[datetime]:
    """Distinct timestamps (sorted ascending)."""
    seen = sorted({r["_ts"] for r in rows if r.get("_ts") is not None})
    return seen


def header_columns(path: str | Path) -> list[str]:
    """The header row of a CSV (for discovery column checks)."""
    try:
        with Path(path).open("r", encoding="utf-8", newline="") as fh:
            return next(csv.reader(fh), [])
    except OSError:
        return []
