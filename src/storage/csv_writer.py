"""Safe append-only CSV / JSONL writers.

Append mode with header-aware open. Never overwrites; never deletes.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def append_csv_row(path: Path, row: dict[str, Any], fieldnames: Iterable[str]) -> None:
    """Append a single row. Writes the header if the file is new/empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = (not path.exists()) or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames))
        if new_file:
            w.writeheader()
        w.writerow(row)


def write_csv_snapshot(path: Path, rows: list[dict[str, Any]], fieldnames: Iterable[str]) -> None:
    """Overwrite the file with the given rows. For point-in-time snapshots
    (e.g. paper_positions.csv)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON object as one line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
