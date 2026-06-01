"""End-of-day summary generator.

Reads today's outputs/runs/{date}/* and produces:
  outputs/daily/{date}/eod_summary.md
  outputs/daily/{date}/eod_summary.json
  outputs/latest/eod_summary.md
  outputs/latest/eod_summary.json

The "latest" copies let the Streamlit cockpit always render the most-recent
summary without having to know today's date.

Phase 1: emits a minimal-but-complete summary so the cockpit always has a
file to point at — even on no-trade days.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.risk.limits import load_profile
from src.storage.paths import (
    decision_log_path,
    eod_summary_json_path,
    eod_summary_md_path,
    latest_dir,
    manual_trades_path,
    paper_positions_path,
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


def _max_col(rows: list[dict[str, str]], col: str) -> float:
    best = 0.0
    for r in rows:
        v = r.get(col)
        try:
            if v not in (None, ""):
                best = max(best, float(v))
        except (TypeError, ValueError):
            continue
    return best


def generate_eod_summary(repo_root: Path, date_str: str | None = None) -> Path:
    cfg = load_config(repo_root)
    output_root = cfg.output_dir
    d = date_str or today_et_date()
    _ = run_dir(output_root, d)  # ensure exists

    decisions = _read_jsonl(decision_log_path(output_root, d))
    manuals = _read_csv(manual_trades_path(output_root, d))
    papers = _read_csv(paper_trades_path(output_root, d))
    positions = _read_csv(paper_positions_path(output_root, d))

    profile = load_profile(cfg.risk_profiles, cfg.active_risk_profile)
    starting_balance = profile.starting_balance

    n_decisions = len(decisions)
    n_no_trade = sum(1 for r in decisions if r.get("decision") == "NO_TRADE")
    n_trade = n_decisions - n_no_trade

    realized = _pnl_sum(papers) + _pnl_sum(manuals)
    unrealized = _pnl_sum(positions, "unrealized_pnl")
    open_positions = [p for p in positions if not (p.get("exit_time") or "").strip()]

    # rejection / planned-risk stats from the decision log
    rejected_count = 0
    max_planned = 0.0
    max_theoretical = 0.0
    for rec in decisions:
        for c in rec.get("all_candidates") or []:
            if c.get("rejected"):
                rejected_count += 1
    max_planned = max(_max_col(manuals, "planned_loss_dollars"),
                      _max_col(papers, "planned_loss_dollars"))
    max_theoretical = max(_max_col(manuals, "theoretical_max_loss_dollars"),
                          _max_col(papers, "theoretical_max_loss_dollars"))

    best_candidate = None
    best_score = -1.0
    for rec in decisions:
        sc = rec.get("selected_candidate")
        if sc and (sc.get("score") or 0) > best_score:
            best_score = float(sc.get("score") or 0)
            best_candidate = sc

    # MaxVol / structure notes drawn from snapshot summaries
    notes = []
    if decisions:
        last = decisions[-1].get("snapshot_summary") or {}
        if last.get("maxvol") is not None:
            notes.append(f"Last MaxVol: {last['maxvol']}")
        if last.get("gamma_regime"):
            notes.append(f"Gamma regime: {last['gamma_regime']}")
        if last.get("put_ceiling_2k") is not None:
            notes.append(f"PUT_CEILING (2K): {last['put_ceiling_2k']}")
        if last.get("call_floor_2k") is not None:
            notes.append(f"CALL_FLOOR (2K): {last['call_floor_2k']}")

    summary = {
        "date": d,
        "risk_profile": cfg.active_risk_profile,
        "starting_balance": starting_balance,
        "ending_balance": starting_balance + realized + unrealized,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "trades_taken_paper": len(papers),
        "trades_taken_manual": len(manuals),
        "trades_closed": sum(1 for r in [*manuals, *papers] if (r.get("exit_ts") or "").strip()),
        "open_positions": len(open_positions),
        "decision_ticks": n_decisions,
        "trade_decisions": n_trade,
        "no_trade_decisions": n_no_trade,
        "rejected_candidate_count": rejected_count,
        "max_planned_stop_risk_dollars": max_planned,
        "max_theoretical_risk_dollars": max_theoretical,
        "best_candidate_of_day": best_candidate,
        "notes": notes,
    }

    # write per-day
    json_path = eod_summary_json_path(output_root, d)
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    md_path = eod_summary_md_path(output_root, d)
    md_path.write_text(_render_md(summary), encoding="utf-8")

    # mirror to outputs/latest
    latest = latest_dir(output_root)
    (latest / "eod_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    (latest / "eod_summary.md").write_text(_render_md(summary), encoding="utf-8")

    return md_path


def _render_md(s: dict[str, Any]) -> str:
    lines = [
        f"# EOD Summary — {s['date']}",
        "",
        f"- Risk profile: **{s.get('risk_profile') or '—'}**",
        f"- Starting balance: **${s['starting_balance']:,.2f}**",
        f"- Ending balance: **${s['ending_balance']:,.2f}**",
        f"- Realized P&L: **${s['realized_pnl']:,.2f}**",
        f"- Unrealized P&L: ${s['unrealized_pnl']:,.2f}",
        "",
        f"- Paper trades taken: {s['trades_taken_paper']}",
        f"- Manual trades taken: {s['trades_taken_manual']}",
        f"- Trades closed: {s['trades_closed']}",
        f"- Open positions: {s['open_positions']}",
        "",
        f"- Scan ticks: {s['decision_ticks']} ({s['trade_decisions']} trade, {s['no_trade_decisions']} no-trade)",
        f"- Candidates rejected (across all ticks): {s['rejected_candidate_count']}",
        f"- Max planned stop risk realized today: ${s['max_planned_stop_risk_dollars']:,.2f}",
        f"- Max theoretical risk realized today: ${s['max_theoretical_risk_dollars']:,.2f}",
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
    if s.get("notes"):
        lines += ["## Structure / MaxVol notes", *[f"- {n}" for n in s["notes"]], ""]
    return "\n".join(lines)
