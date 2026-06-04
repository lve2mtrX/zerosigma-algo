"""Phase 10A (prep) — SPX_RAW CSV loader → StructureSnapshot (read-only).

Reads the saved per-strike, per-timestamp SPX exposure CSVs
(`SPX_RAW_<date>.csv`, located by `scripts/discover_backtest_sources.py`) and maps
ONE timestamp into the SAME `StructureSnapshot` the live provider produces — by
reusing `snapshot_loader.map_payload_to_snapshot` (the shared mapper). So replay
derives 2K/5K/10K wings + the W2/WDS inputs identically to live; there is NO
strategy/structure fork.

PURE data loading: no network, no writes, no execution. This is a LOADER scaffold
for Phase 10 — it does NOT run the scanner / selector / lifecycle yet.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from src.providers.structure.types import StructureSnapshot
from src.replay.snapshot_loader import map_payload_to_snapshot

# Column aliases — the TOS export uses spaced header names.
_STRIKE_COLS = ("Strike", "strike")
_CALL_VOL_COLS = ("CALL Volume", "call_volume", "CallVolume")
_PUT_VOL_COLS = ("PUT Volume", "put_volume", "PutVolume")
_SPOT_COLS = ("SPX_Spot", "spot", "Spot")
_TS_COLS = ("timestamp", "Timestamp", "datetime")
_SESSION_COLS = ("session", "Session")


def _get(row: dict, names: tuple[str, ...]) -> Any:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_rows(path: str | Path, *, rth_only: bool = True) -> list[dict]:
    """Read all rows from an SPX_RAW CSV as dicts (optionally RTH-session only)."""
    out: list[dict] = []
    with Path(path).open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if rth_only:
                sess = _get(row, _SESSION_COLS)
                if sess is not None and str(sess).strip().upper() != "RTH":
                    continue
            out.append(row)
    return out


def available_timestamps(path: str | Path, *, rth_only: bool = True) -> list[str]:
    """Distinct timestamps present in the CSV, in file order."""
    seen: list[str] = []
    s: set[str] = set()
    for row in read_rows(path, rth_only=rth_only):
        ts = _get(row, _TS_COLS)
        if ts and ts not in s:
            s.add(ts)
            seen.append(ts)
    return seen


def exposure_series_at(rows: list[dict], timestamp: str) -> dict[str, Any]:
    """Build {strikes, calls, puts, spot} for one timestamp. Strikes ascending;
    per-strike side-specific CALL/PUT volume (the WDS / wing inputs)."""
    triples: list[tuple[float, float, float]] = []
    spot = None
    for r in rows:
        if _get(r, _TS_COLS) != timestamp:
            continue
        k = _num(_get(r, _STRIKE_COLS))
        if k is None:
            continue
        triples.append((k, _num(_get(r, _CALL_VOL_COLS)) or 0.0,
                        _num(_get(r, _PUT_VOL_COLS)) or 0.0))
        if spot is None:
            spot = _num(_get(r, _SPOT_COLS))
    triples.sort(key=lambda t: t[0])
    return {
        "strikes": [t[0] for t in triples],
        "calls": [t[1] for t in triples],
        "puts": [t[2] for t in triples],
        "spot": spot,
    }


def snapshot_at(path: str | Path, timestamp: str | None = None, *,
                symbol: str = "SPX", rth_only: bool = True) -> StructureSnapshot:
    """Map one timestamp of an SPX_RAW CSV → StructureSnapshot (reusing the live
    mapper, so 2K/5K/10K wings + W2 derive identically). When `timestamp` is None,
    uses the FIRST available timestamp. Source tag = 'spx_raw_replay'."""
    rows = read_rows(path, rth_only=rth_only)
    if not rows:
        raise ValueError(f"no usable rows in {path}")
    if timestamp is None:
        timestamp = _get(rows[0], _TS_COLS)
    series = exposure_series_at(rows, timestamp)
    snap_payload = {"spot": {"spot": series["spot"]}, "timestamp": timestamp, "exposures": {}}
    vol_series = {"strikes": series["strikes"], "calls": series["calls"], "puts": series["puts"]}
    return map_payload_to_snapshot(snap_payload, vol_series, symbol=symbol,
                                   source="spx_raw_replay")


def available_dates(directory: str | Path) -> list[str]:
    """Dates parsed from `SPX_RAW_*.csv` filenames under a directory (sorted)."""
    d = Path(directory)
    if not d.is_dir():
        return []
    dates: list[str] = []
    for p in sorted(d.glob("SPX_RAW_*.csv")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
        if m:
            dates.append(m.group(1))
    return dates


def file_for_date(directory: str | Path, date: str) -> Path | None:
    """The SPX_RAW CSV for a given date (first match), or None."""
    matches = sorted(Path(directory).glob(f"SPX_RAW*{date}*.csv")) if Path(directory).is_dir() else []
    return matches[0] if matches else None
