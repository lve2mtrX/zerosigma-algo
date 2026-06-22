"""Phase 9B — portfolio paper-trade ledger: paths, writers, tolerant readers,
P&L summary, and LOCAL-ONLY reconciliation.

LOCAL PAPER ACCOUNTING ONLY. No brokerage, no orders, no order preview, no
external process calls. Reconciliation compares the *local* ledgers against each
other only; broker position reconciliation is explicitly deferred
(``broker_position_reconciliation: "deferred"``).

Ledger layout (under --output-dir, default outputs/portfolio_forward):
  runs/{portfolio_run_id}/
    portfolio_manifest.json   portfolio_tick_log.jsonl   profile_tick_log.jsonl
    paper_trades_open.csv     paper_trades_closed.csv     paper_trade_events.jsonl
    portfolio_summary.json    heartbeat.json              reconciliation_report.json
    scanner/{profile_id}/     (each profile's own scanner outputs)
  latest/  ← mirror of manifest + heartbeat + summary + open/closed + pointer
"""

from __future__ import annotations

import csv
import json
from dataclasses import fields
from pathlib import Path

from src.paper.models import (
    EXECUTION_MODE,
    ExecutionJournalEvent,
    PaperMark,
    PaperTrade,
)
from src.regime.types import RegimeChangeEvent
from src.utils.time import now_et

REPO_ROOT = Path(__file__).resolve().parents[2]

PAPER_TRADE_FIELDS: list[str] = PaperTrade.field_names()
PAPER_MARK_FIELDS: list[str] = [field.name for field in fields(PaperMark)]


# ── paths ────────────────────────────────────────────────────────────────────

def portfolio_root(root: Path | str | None = None) -> Path:
    if root is None:
        return REPO_ROOT / "outputs" / "portfolio_forward"
    p = Path(root)
    return p if p.is_absolute() else (REPO_ROOT / p)


def portfolio_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "run_dir": run_dir,
        "manifest": run_dir / "portfolio_manifest.json",
        "heartbeat": run_dir / "heartbeat.json",
        "portfolio_tick_log": run_dir / "portfolio_tick_log.jsonl",
        "profile_tick_log": run_dir / "profile_tick_log.jsonl",
        "open_csv": run_dir / "paper_trades_open.csv",
        "closed_csv": run_dir / "paper_trades_closed.csv",
        "events": run_dir / "paper_trade_events.jsonl",
        "execution_journal": run_dir / "paper_execution_journal.jsonl",
        "execution_journal_md": run_dir / "paper_execution_journal.md",
        "marks": run_dir / "paper_marks.csv",
        "regime_events": run_dir / "paper_regime_events.jsonl",
        "latest_open_positions": run_dir / "latest_open_positions.json",
        "summary": run_dir / "portfolio_summary.json",
        "reconciliation": run_dir / "reconciliation_report.json",
        "scanner": run_dir / "scanner",
    }


# ── low-level writers ────────────────────────────────────────────────────────

def _write_json(path: Path, record: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, default=str)


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, default=str) + "\n")


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})


# ── high-level ledger writers ────────────────────────────────────────────────

def write_manifest(run_dir: Path, latest_dir: Path, manifest: dict) -> None:
    _write_json(run_dir / "portfolio_manifest.json", manifest)
    _write_json(latest_dir / "portfolio_manifest.json", manifest)
    _write_json(latest_dir / "latest_run_pointer.json", {
        "portfolio_run_id": manifest.get("portfolio_run_id"),
        "run_path": str(run_dir),
        "status": manifest.get("status"),
        "updated_at": manifest.get("ended_at") or manifest.get("started_at"),
    })


def write_heartbeat(run_dir: Path, latest_dir: Path, hb: dict) -> None:
    _write_json(run_dir / "heartbeat.json", hb)
    _write_json(latest_dir / "heartbeat.json", hb)


def write_open_trades(run_dir: Path, latest_dir: Path, open_trades: list[PaperTrade]) -> None:
    rows = [t.to_row() for t in open_trades]
    _write_csv(run_dir / "paper_trades_open.csv", rows, PAPER_TRADE_FIELDS)
    _write_csv(latest_dir / "paper_trades_open.csv", rows, PAPER_TRADE_FIELDS)


def write_closed_trades(run_dir: Path, latest_dir: Path, closed_trades: list[PaperTrade]) -> None:
    rows = [t.to_row() for t in closed_trades]
    _write_csv(run_dir / "paper_trades_closed.csv", rows, PAPER_TRADE_FIELDS)
    _write_csv(latest_dir / "paper_trades_closed.csv", rows, PAPER_TRADE_FIELDS)


def write_execution_journal(
    run_dir: Path,
    latest_dir: Path,
    events: list[ExecutionJournalEvent],
) -> None:
    rows = [event.to_dict() for event in events]
    _write_jsonl(run_dir / "paper_execution_journal.jsonl", rows)
    _write_jsonl(latest_dir / "paper_execution_journal.jsonl", rows)
    lines = [
        "# Local Paper Execution Journal",
        "",
        "LOCAL PAPER ONLY - NO BROKER ORDER SENT.",
        "",
        "| Timestamp | Action | Trade | Reason codes | Explanation | P&L impact |",
        "|---|---|---|---|---|---:|",
    ]
    for event in events:
        explanation = event.plain_english_explanation.replace("|", "/")
        reasons = "; ".join(event.reason_codes).replace("|", "/")
        pnl = "" if event.pnl_impact is None else f"{event.pnl_impact:.2f}"
        lines.append(
            f"| {event.timestamp} | {event.action} | {event.paper_trade_id or ''} | "
            f"{reasons} | {explanation} | {pnl} |"
        )
    text = "\n".join(lines) + "\n"
    for directory in (run_dir, latest_dir):
        path = directory / "paper_execution_journal.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def write_paper_marks(
    run_dir: Path,
    latest_dir: Path,
    marks: list[PaperMark],
) -> None:
    rows = [mark.to_csv_row() for mark in marks]
    _write_csv(run_dir / "paper_marks.csv", rows, PAPER_MARK_FIELDS)
    _write_csv(latest_dir / "paper_marks.csv", rows, PAPER_MARK_FIELDS)


def write_regime_events(
    run_dir: Path,
    latest_dir: Path,
    events: list[RegimeChangeEvent],
) -> None:
    rows = [event.to_dict() for event in events]
    _write_jsonl(run_dir / "paper_regime_events.jsonl", rows)
    _write_jsonl(latest_dir / "paper_regime_events.jsonl", rows)


def write_latest_open_positions(
    run_dir: Path,
    latest_dir: Path,
    open_trades: list[PaperTrade],
) -> None:
    rows = [trade.to_row() for trade in open_trades]
    _write_json(run_dir / "latest_open_positions.json", rows)
    _write_json(latest_dir / "latest_open_positions.json", rows)


def append_event(run_dir: Path, event: dict) -> None:
    _append_jsonl(run_dir / "paper_trade_events.jsonl", event)


def append_portfolio_tick(run_dir: Path, record: dict) -> None:
    _append_jsonl(run_dir / "portfolio_tick_log.jsonl", record)


def append_profile_tick(run_dir: Path, record: dict) -> None:
    _append_jsonl(run_dir / "profile_tick_log.jsonl", record)


def write_summary(run_dir: Path, latest_dir: Path, summary: dict) -> None:
    _write_json(run_dir / "portfolio_summary.json", summary)
    _write_json(latest_dir / "portfolio_summary.json", summary)


def make_event(
    *, event_type: str, timestamp: str, paper_trade_id: str | None, profile_id: str,
    reason: str | None, trade: PaperTrade | None,
) -> dict:
    """Build a paper_trade_events.jsonl record. Always stamps no_execution."""
    return {
        "event_type": event_type,
        "timestamp": timestamp,
        "paper_trade_id": paper_trade_id,
        "profile_id": profile_id,
        "reason": reason,
        "trade": trade.to_row() if trade is not None else None,
        "no_execution": True,
    }


# ── P&L summary ──────────────────────────────────────────────────────────────

def compute_summary(
    open_trades: list[PaperTrade], closed_trades: list[PaperTrade], *,
    max_open_trades_seen: int = 0, duplicate_skipped_count: int = 0,
    blocked_by_limits_count: int = 0,
) -> dict:
    realized = round(sum(t.realized_pnl or 0.0 for t in closed_trades), 2)
    unrealized = round(sum(t.unrealized_pnl or 0.0 for t in open_trades), 2)
    wins = sum(1 for t in closed_trades if (t.realized_pnl or 0.0) > 0)
    losses = sum(1 for t in closed_trades if (t.realized_pnl or 0.0) < 0)
    decided = wins + losses
    return {
        "open_trade_count": len(open_trades),
        "closed_trade_count": len(closed_trades),
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": round(realized + unrealized, 2),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / decided, 4) if decided else 0.0,
        "max_open_trades_seen": max_open_trades_seen,
        "duplicate_skipped_count": duplicate_skipped_count,
        "blocked_by_limits_count": blocked_by_limits_count,
        "no_execution": True,
        "execution_mode": EXECUTION_MODE,
    }


# ── tolerant readers (mirror src/forward/review.py) ──────────────────────────

def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def discover_portfolio_runs(root: Path | str | None = None) -> list[Path]:
    runs_dir = portfolio_root(root) / "runs"
    if not runs_dir.is_dir():
        return []
    dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    return sorted(dirs, key=lambda p: (p.name, p.stat().st_mtime), reverse=True)


def latest_dir(root: Path | str | None = None) -> Path:
    return portfolio_root(root) / "latest"


def resolve_portfolio_run_dir(run_ref: str, root: Path | str | None = None) -> Path | None:
    """Resolve a portfolio_run_id or the alias ``latest`` to a run dir."""
    runs_dir = portfolio_root(root) / "runs"
    if run_ref and run_ref != "latest":
        cand = runs_dir / run_ref
        return cand if cand.is_dir() else None
    # latest: pointer → manifest → newest discovered
    ptr = _read_json(latest_dir(root) / "latest_run_pointer.json")
    if ptr and ptr.get("portfolio_run_id"):
        cand = runs_dir / ptr["portfolio_run_id"]
        if cand.is_dir():
            return cand
    man = _read_json(latest_dir(root) / "portfolio_manifest.json")
    if man and man.get("portfolio_run_id"):
        cand = runs_dir / man["portfolio_run_id"]
        if cand.is_dir():
            return cand
    runs = discover_portfolio_runs(root)
    return runs[0] if runs else None


def load_manifest(run_ref: str, root: Path | str | None = None) -> dict | None:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_json(rd / "portfolio_manifest.json") if rd else None


def load_heartbeat(run_ref: str, root: Path | str | None = None) -> dict | None:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_json(rd / "heartbeat.json") if rd else None


def load_summary(run_ref: str, root: Path | str | None = None) -> dict | None:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_json(rd / "portfolio_summary.json") if rd else None


def load_open_trades(run_ref: str, root: Path | str | None = None) -> list[dict]:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_csv(rd / "paper_trades_open.csv") if rd else []


def load_closed_trades(run_ref: str, root: Path | str | None = None) -> list[dict]:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_csv(rd / "paper_trades_closed.csv") if rd else []


def load_events(run_ref: str, root: Path | str | None = None) -> list[dict]:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_jsonl(rd / "paper_trade_events.jsonl") if rd else []


def load_execution_journal(run_ref: str, root: Path | str | None = None) -> list[dict]:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_jsonl(rd / "paper_execution_journal.jsonl") if rd else []


def load_paper_marks(run_ref: str, root: Path | str | None = None) -> list[dict]:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_csv(rd / "paper_marks.csv") if rd else []


def load_regime_events(run_ref: str, root: Path | str | None = None) -> list[dict]:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_jsonl(rd / "paper_regime_events.jsonl") if rd else []


def load_latest_open_positions(
    run_ref: str,
    root: Path | str | None = None,
) -> list[dict]:
    rd = resolve_portfolio_run_dir(run_ref, root)
    if rd is None:
        return []
    path = rd / "latest_open_positions.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def load_reconciliation(run_ref: str, root: Path | str | None = None) -> dict | None:
    rd = resolve_portfolio_run_dir(run_ref, root)
    return _read_json(rd / "reconciliation_report.json") if rd else None


def load_latest_heartbeat(root: Path | str | None = None) -> dict | None:
    return _read_json(latest_dir(root) / "heartbeat.json")


def load_latest_summary(root: Path | str | None = None) -> dict | None:
    return _read_json(latest_dir(root) / "portfolio_summary.json")


def list_portfolio_run_summaries(limit: int | None = None, root: Path | str | None = None) -> list[dict]:
    runs = discover_portfolio_runs(root)
    if limit is not None:
        runs = runs[:limit]
    out: list[dict] = []
    for rd in runs:
        man = _read_json(rd / "portfolio_manifest.json") or {}
        summ = _read_json(rd / "portfolio_summary.json") or {}
        out.append({
            "portfolio_run_id": man.get("portfolio_run_id") or rd.name,
            "status": man.get("status"),
            "profiles": man.get("profiles"),
            "open": summ.get("open_trade_count"),
            "closed": summ.get("closed_trade_count"),
            "realized_pnl": summ.get("realized_pnl"),
            "total_pnl": summ.get("total_pnl"),
        })
    return out


# ── local-only reconciliation ────────────────────────────────────────────────

def reconcile_run(run_ref: str, root: Path | str | None = None,
                  reconciliation_mode: str = "local_only") -> dict | None:
    """Reconcile a run's LOCAL ledgers against each other. Never touches a brokerage.
    Returns the report dict (also written to reconciliation_report.json), or None
    if the run can't be resolved."""
    rd = resolve_portfolio_run_dir(run_ref, root)
    if rd is None:
        return None
    paths = portfolio_paths(rd)
    open_rows = _read_csv(paths["open_csv"])
    closed_rows = _read_csv(paths["closed_csv"])
    events = _read_jsonl(paths["events"])

    open_ids = [r.get("paper_trade_id") for r in open_rows]
    closed_ids = [r.get("paper_trade_id") for r in closed_rows]
    opened_event_ids = {e.get("paper_trade_id") for e in events if e.get("event_type") == "open"}
    closed_event_ids = {e.get("paper_trade_id") for e in events if e.get("event_type") == "close"}

    issues: list[dict] = []

    # open trade missing an 'open' event
    for r in open_rows:
        if r.get("paper_trade_id") not in opened_event_ids:
            issues.append({"type": "open_trade_missing_open_event",
                           "paper_trade_id": r.get("paper_trade_id")})

    # closed trade still present in the open file
    open_id_set = set(open_ids)
    for cid in closed_ids:
        if cid in open_id_set:
            issues.append({"type": "closed_trade_still_in_open_file", "paper_trade_id": cid})

    # duplicate open identity (same trade_identity open more than once)
    seen: dict[str, int] = {}
    for r in open_rows:
        ident = r.get("trade_identity")
        if ident:
            seen[ident] = seen.get(ident, 0) + 1
    for ident, n in seen.items():
        if n > 1:
            issues.append({"type": "duplicate_open_identity", "trade_identity": ident, "count": n})

    # invalid status transitions
    for cid in closed_event_ids:
        if cid not in opened_event_ids:
            issues.append({"type": "close_without_open", "paper_trade_id": cid})
    for r in open_rows:
        if str(r.get("status")) != "open":
            issues.append({"type": "non_open_status_in_open_file",
                           "paper_trade_id": r.get("paper_trade_id"), "status": r.get("status")})
    for r in closed_rows:
        if str(r.get("status")) != "closed":
            issues.append({"type": "non_closed_status_in_closed_file",
                           "paper_trade_id": r.get("paper_trade_id"), "status": r.get("status")})

    report = {
        "portfolio_run_id": rd.name,
        "checked_at": now_et().isoformat(),
        "reconciliation_mode": reconciliation_mode,
        "open_count": len(open_rows),
        "closed_count": len(closed_rows),
        "event_count": len(events),
        "issues": issues,
        "ok": len(issues) == 0,
        "broker_position_reconciliation": "deferred",
        "no_execution": True,
    }
    _write_json(paths["reconciliation"], report)
    # also mirror into latest for quick UI access
    _write_json(latest_dir(root) / "reconciliation_report.json", report)
    return report
