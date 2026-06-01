"""Decision log — append a JSONL record per scan tick."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.storage.csv_writer import append_jsonl
from src.storage.paths import decision_log_path
from src.strategies.base import StrategyDecision


def log_decision(
    output_root: Path,
    decision: StrategyDecision,
    snapshot_summary: dict[str, Any],
    ts: datetime,
    date_str: str | None = None,
) -> Path:
    path = decision_log_path(output_root, date_str)
    record = {
        "ts": ts.isoformat(),
        "strategy_id": decision.strategy_id,
        "decision": decision.decision,
        "selected_candidate": _candidate_to_dict(decision.selected) if decision.selected else None,
        "all_candidates": [_candidate_to_dict(c) for c in decision.all_candidates],
        "score": decision.selected.score if decision.selected else None,
        "rejection_reasons": decision.rejection_reasons,
        "explanation": decision.explanation,
        "snapshot_summary": snapshot_summary,
    }
    append_jsonl(path, record)
    return path


def _candidate_to_dict(c) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "side": c.side,
        "symbol": c.symbol,
        "expiry": c.expiry,
        "short_strike": c.short_strike,
        "long_strike": c.long_strike,
        "credit": c.credit,
        "max_risk": c.max_risk,
        "reward_risk": c.reward_risk,
        "breakeven": c.breakeven,
        "distance_from_spot": c.distance_from_spot,
        "score": c.score,
        "score_breakdown": c.score_breakdown,
        "rejected": c.rejected,
        "rejection_reasons": c.rejection_reasons,
        "meta": c.meta,
    }
