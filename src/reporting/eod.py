"""End-of-day summary generator.

Reads today's outputs/runs/{date}/* and produces:
  outputs/daily/{date}/eod_summary.md
  outputs/daily/{date}/eod_summary.json

Phase 1: emits a minimal-but-complete summary so the cockpit always has a
file to point at — even on no-trade days.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.storage.paths import (
    decision_log_path,
    eod_summary_json_path,
    eod_summary_md_path,
    manual_trades_path,
    paper_trades_path,
    run_dir,
)
from src.utils.config import load_config
from src.utils.time import today_et_date


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _pnl_sum(rows: list[dict[str, str]], col: str = "realized_pnl") -> float:
    total = 0.0
    for r in rows:
        v = r.get(col)
        try:
            total += float(v) if v not in (None, "") else 0.0
        except (TypeError, ValueError):
            continue
    return total


def generate_eod_summary(repo_root: Path, date_str: str | None = None) -> Path:
    cfg = load_config(repo_root)
    output_root = cfg.output_dir
    d = date_str or today_et_date()
    _ = run_dir(output_root, d)  # ensure exists

    decisions = _read_jsonl(decision_log_path(output_root, d))
    manuals = _read_csv(manual_trades_path(output_root, d))
    papers = _read_csv(paper_trades_path(output_root, d))

    n_decisions = len(decisions)
    n_no_trade = sum(1 for r in decisions if r.get("decision") == "NO_TRADE")
    n_trade = n_decisions - n_no_trade
    realized = _pnl_sum(papers) + _pnl_sum(manuals)

    best_candidate = None
    best_score = -1.0
    for rec in decisions:
        sc = rec.get("selected_candidate")
        if sc and (sc.get("score") or 0) > best_score:
            best_score = float(sc.get("score") or 0)
            best_candidate = sc

    summary = {
        "date": d,
        "starting_balance": 10000,  # TODO: read from risk profile actually used today
        "realized_pnl": realized,
        "unrealized_pnl": 0.0,      # TODO: read from latest positions snapshot
        "trades_taken_paper": len(papers),
        "trades_taken_manual": len(manuals),
        "decision_ticks": n_decisions,
        "trade_decisions": n_trade,
        "no_trade_decisions": n_no_trade,
        "best_candidate_of_day": best_candidate,
        "notes": [],
    }

    json_path = eod_summary_json_path(output_root, d)
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    md_path = eod_summary_md_path(output_root, d)
    md_path.write_text(_render_md(summary), encoding="utf-8")
    return md_path


def _render_md(s: dict[str, Any]) -> str:
    lines = [
        f"# EOD Summary — {s['date']}",
        "",
        f"- Starting balance: **${s['starting_balance']:,.2f}**",
        f"- Realized P&L: **${s['realized_pnl']:,.2f}**",
        f"- Unrealized P&L: ${s['unrealized_pnl']:,.2f}",
        f"- Paper trades taken: {s['trades_taken_paper']}",
        f"- Manual trades taken: {s['trades_taken_manual']}",
        f"- Scan ticks: {s['decision_ticks']} ({s['trade_decisions']} trade, {s['no_trade_decisions']} no-trade)",
        "",
    ]
    if s.get("best_candidate_of_day"):
        bc = s["best_candidate_of_day"]
        lines += [
            "## Best candidate of the day",
            f"- Side: **{bc.get('side')}**",
            f"- Short/Long: {bc.get('short_strike')} / {bc.get('long_strike')}",
            f"- Credit: ${bc.get('credit'):.2f}  ·  Max risk: ${bc.get('max_risk'):.2f}  ·  R:R = {bc.get('reward_risk'):.2f}",
            f"- Score: {bc.get('score'):.2f}",
            "",
        ]
    else:
        lines += ["## Best candidate of the day", "_None recorded._", ""]
    return "\n".join(lines)
