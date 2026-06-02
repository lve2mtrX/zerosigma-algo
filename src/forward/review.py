"""Forward-run REVIEW utilities — Phase 8 (read-only inspection).

Pure helpers to discover and summarize the local forward ledger written by
`scripts/run_forward.py` (Phase 7). READ-ONLY: this module never scans, runs the
forward runner, places orders, or executes anything — it just reads JSON / JSONL /
CSV the runner already wrote, tolerating missing/empty files without tracebacks.

Ledger layout (per Phase 7):
  {root}/runs/{run_id}/   run_manifest.json  heartbeat.json  tick_log.jsonl
                         signal_log.jsonl  selected_trades.csv  no_trade_log.jsonl
  {root}/latest/          run_manifest.json  heartbeat.json  latest_run_pointer.json
  (root defaults to <repo>/outputs/forward)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def forward_root(root: Path | str | None = None) -> Path:
    if root is None:
        return REPO_ROOT / "outputs" / "forward"
    p = Path(root)
    return p if p.is_absolute() else (REPO_ROOT / p)


# ── tolerant readers (never raise on missing/corrupt files) ─────────────────

def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return out
    return out


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


# ── discovery ────────────────────────────────────────────────────────────────

def discover_runs(root: Path | str | None = None) -> list[Path]:
    """Return run directories under {root}/runs, NEWEST FIRST.

    run_id is `{YYYYMMDD_HHMMSS}_{profile_id}` so the fixed-width timestamp prefix
    sorts chronologically; we sort by name descending (mtime as a tiebreak)."""
    runs_dir = forward_root(root) / "runs"
    if not runs_dir.is_dir():
        return []
    dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: (p.name, p.stat().st_mtime), reverse=True)
    return dirs


def load_latest_pointer(root: Path | str | None = None) -> dict[str, Any] | None:
    return _read_json(forward_root(root) / "latest" / "latest_run_pointer.json")


def load_latest_manifest(root: Path | str | None = None) -> dict[str, Any] | None:
    return _read_json(forward_root(root) / "latest" / "run_manifest.json")


def load_latest_heartbeat(root: Path | str | None = None) -> dict[str, Any] | None:
    return _read_json(forward_root(root) / "latest" / "heartbeat.json")


def resolve_run_dir(run_ref: str, root: Path | str | None = None) -> Path | None:
    """Resolve a run reference to a run directory, or None if not found.

    'latest' → the most-recent run (via latest_run_pointer.json → latest manifest
    run_id → newest discovered dir). Otherwise a concrete run_id under runs/."""
    base = forward_root(root)
    if run_ref == "latest":
        ptr = load_latest_pointer(root)
        run_id = (ptr or {}).get("run_id")
        if not run_id:
            man = load_latest_manifest(root)
            run_id = (man or {}).get("run_id")
        if run_id:
            cand = base / "runs" / run_id
            if cand.is_dir():
                return cand
        runs = discover_runs(root)
        return runs[0] if runs else None
    cand = base / "runs" / run_ref
    return cand if cand.is_dir() else None


# ── per-run ledger loaders ──────────────────────────────────────────────────

def load_run_manifest(run_ref: str, root: Path | str | None = None) -> dict[str, Any] | None:
    rd = resolve_run_dir(run_ref, root)
    return _read_json(rd / "run_manifest.json") if rd else None


def load_run_heartbeat(run_ref: str, root: Path | str | None = None) -> dict[str, Any] | None:
    rd = resolve_run_dir(run_ref, root)
    return _read_json(rd / "heartbeat.json") if rd else None


def load_tick_log(run_ref: str, root: Path | str | None = None) -> list[dict[str, Any]]:
    rd = resolve_run_dir(run_ref, root)
    return _read_jsonl(rd / "tick_log.jsonl") if rd else []


def load_signal_log(run_ref: str, root: Path | str | None = None) -> list[dict[str, Any]]:
    rd = resolve_run_dir(run_ref, root)
    return _read_jsonl(rd / "signal_log.jsonl") if rd else []


def load_no_trade_log(run_ref: str, root: Path | str | None = None) -> list[dict[str, Any]]:
    rd = resolve_run_dir(run_ref, root)
    return _read_jsonl(rd / "no_trade_log.jsonl") if rd else []


def load_selected_trades(run_ref: str, root: Path | str | None = None) -> list[dict[str, Any]]:
    rd = resolve_run_dir(run_ref, root)
    return _read_csv(rd / "selected_trades.csv") if rd else []


# ── summary ──────────────────────────────────────────────────────────────────

def _signal_summary(sig: dict[str, Any]) -> dict[str, Any]:
    return {
        "tick_id": sig.get("tick_id"),
        "emitted_at": sig.get("emitted_at"),
        "symbol": sig.get("symbol"),
        "side": sig.get("side"),
        "selected_expiry": sig.get("selected_expiry"),
        "short_strike": sig.get("short_strike"),
        "long_strike": sig.get("long_strike"),
        "credit": sig.get("credit"),
        "score": sig.get("score"),
        "selector_reason": sig.get("selector_reason"),
    }


def summarize_run(run_ref: str, root: Path | str | None = None) -> dict[str, Any] | None:
    """Build the run summary dict, or None if the run can't be found.

    Tolerates any missing optional ledger file (counts default to 0, lists empty)."""
    rd = resolve_run_dir(run_ref, root)
    if rd is None:
        return None
    manifest = _read_json(rd / "run_manifest.json") or {}
    heartbeat = _read_json(rd / "heartbeat.json") or {}
    ticks = _read_jsonl(rd / "tick_log.jsonl")
    signals = _read_jsonl(rd / "signal_log.jsonl")
    no_trades = _read_jsonl(rd / "no_trade_log.jsonl")

    last_tick = ticks[-1] if ticks else {}
    last_no_trade = no_trades[-1] if no_trades else {}

    latest_no_trade_reason = (
        last_no_trade.get("no_trade_reason")
        or last_tick.get("selector_no_trade_reason")
    )

    return {
        "run_id": manifest.get("run_id") or rd.name,
        "run_path": str(rd),
        "profile_id": manifest.get("profile_id"),
        "profile_name": manifest.get("profile_name"),
        "profile_hash": manifest.get("profile_hash"),
        "status": manifest.get("status"),
        "started_at": manifest.get("started_at"),
        "ended_at": manifest.get("ended_at"),
        "interval_seconds": manifest.get("interval_seconds"),
        "daily_selector": manifest.get("daily_selector"),
        "quote_provider": manifest.get("quote_provider"),
        "target_dte": manifest.get("target_dte"),
        "no_execution": manifest.get("no_execution", True),
        "tick_count": len(ticks),
        "signal_count": len(signals),
        "duplicate_signal_count": sum(
            1 for t in ticks if t.get("duplicate_selected_signal") is True
        ),
        "no_trade_count": len(no_trades),
        "error_count": sum(1 for t in ticks if t.get("status") == "error"),
        "latest_tick_time": (
            last_tick.get("tick_finished_at") or heartbeat.get("latest_tick_time")
        ),
        "latest_decision": (
            last_tick.get("post_selector_decision")
            or last_tick.get("scanner_decision")
            or heartbeat.get("latest_decision")
        ),
        "latest_selected_trade": bool(
            last_tick.get("selected_trade", heartbeat.get("selected_trade", False))
        ),
        "latest_no_trade_reason": latest_no_trade_reason,
        "selected_trade_summaries": [_signal_summary(s) for s in signals],
        "latest_heartbeat_status": heartbeat.get("status"),
    }


def list_run_summaries(
    limit: int | None = None, root: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Compact summaries for the most-recent runs (newest first)."""
    runs = discover_runs(root)
    if limit is not None:
        runs = runs[: max(0, limit)]
    out: list[dict[str, Any]] = []
    for rd in runs:
        s = summarize_run(rd.name, root)
        if s is not None:
            out.append(s)
    return out
