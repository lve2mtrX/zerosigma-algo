"""Phase 10F research-only dynamic selector attribution.

Analyzes replay records after selection. It never calls the selector, changes
strategy/risk/quote logic, or feeds recommendations back into execution.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from typing import Any

from src.backtesting import reports

_CALL = "CALL_CREDIT"
_PUT = "PUT_CREDIT"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _profile_kind(row: dict[str, Any]) -> str:
    return str(row.get("preset_kind") or "").strip().lower()


def _outcome(pnl: Any) -> str:
    value = _f(pnl)
    if value > 0:
        return "win"
    if value < 0:
        return "loss"
    return "breakeven"


def _metric_summary(rows: list[dict[str, Any]], *, pnl_key: str = "pnl_dollars") -> dict[str, Any]:
    pnls = [_f(row.get(pnl_key)) for row in rows if row.get(pnl_key) not in (None, "")]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_wins = sum(wins)
    gross_losses = sum(losses)
    return {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "total_pnl_dollars": round(sum(pnls), 2),
        "average_pnl_dollars": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "profit_factor": (
            round(gross_wins / abs(gross_losses), 3)
            if gross_losses < 0
            else ("inf" if gross_wins > 0 else 0.0)
        ),
    }


def selected_side_summary(result: Any) -> list[dict[str, Any]]:
    """Dynamic selected-side split, P&L, win rate, and opposite availability."""
    rows = result.dynamic_side_attribution
    out: list[dict[str, Any]] = []
    for side in (_CALL, _PUT):
        grouped = [row for row in rows if row.get("selected_side") == side]
        metric = _metric_summary(grouped, pnl_key="selected_pnl_dollars")
        opposite_available = sum(_truthy(row.get("opposite_available")) for row in grouped)
        opposite_simulated = sum(
            _truthy(row.get("opposite_outcome_simulated")) for row in grouped
        )
        opposite_better = sum(
            _truthy(row.get("opposite_would_have_done_better")) for row in grouped
        )
        out.append({
            "selected_side": side,
            **metric,
            "opposite_available": opposite_available,
            "opposite_available_pct": (
                round(opposite_available / len(grouped) * 100.0, 2) if grouped else None
            ),
            "opposite_simulated": opposite_simulated,
            "opposite_better_count": opposite_better,
            "opposite_better_pct": (
                round(opposite_better / len(grouped) * 100.0, 2) if grouped else None
            ),
            "simulated_opposite_pnl_dollars": round(
                sum(_f(row.get("opposite_pnl_dollars")) for row in grouped), 2
            ),
        })
    return out


def _components(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or ""))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _component_edges(row: dict[str, Any]) -> tuple[str, str]:
    selected = _components(row.get("selected_selector_score_components"))
    opposite = _components(row.get("opposite_selector_score_components"))
    names = (
        "premium_score", "distance_safety_score", "structure_score",
        "maxvol_gamma_alignment_score", "quote_quality_score",
        "existing_candidate_score", "planned_risk_penalty",
    )
    deltas = {
        name: _f(selected.get(name)) - _f(opposite.get(name))
        for name in names
        if name in selected or name in opposite
    }
    if not deltas:
        return "unavailable", "unavailable"
    selected_edge = max(deltas, key=deltas.get)
    opposite_edge = min(deltas, key=deltas.get)
    return selected_edge, opposite_edge


def _best_call_controls(result: Any) -> dict[tuple[str, str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trade in result.trades:
        if _profile_kind(trade) != "control" or trade.get("side") != _CALL:
            continue
        key = (
            str(trade.get("date") or ""),
            str(trade.get("threshold") or ""),
            str(trade.get("entry_target") or ""),
        )
        if key not in lookup or _f(trade.get("pnl_dollars")) > _f(lookup[key].get("pnl_dollars")):
            lookup[key] = trade
    return lookup


def dynamic_vs_best_opposite(result: Any) -> list[dict[str, Any]]:
    """Attribution rows enriched with component edges and matching call controls."""
    controls = _best_call_controls(result)
    out: list[dict[str, Any]] = []
    for raw in result.dynamic_side_attribution:
        row = dict(raw)
        selected_edge, opposite_edge = _component_edges(row)
        key = (
            str(row.get("date") or ""),
            str(row.get("threshold") or ""),
            str(row.get("entry_target") or ""),
        )
        control = controls.get(key)
        selected_pnl = _f(row.get("selected_pnl_dollars"))
        opposite_pnl = (
            _f(row.get("opposite_pnl_dollars"))
            if row.get("opposite_pnl_dollars") not in (None, "") else None
        )
        control_pnl = (
            _f(control.get("pnl_dollars")) if control is not None else None
        )
        row.update({
            "selected_advantage_component": selected_edge,
            "opposite_advantage_component": opposite_edge,
            "opposite_opportunity_cost_dollars": (
                round(max(0.0, opposite_pnl - selected_pnl), 2)
                if opposite_pnl is not None else None
            ),
            "matching_call_control_available": control is not None,
            "matching_call_control_profile_id": (control or {}).get("profile_id"),
            "matching_call_control_pnl_dollars": control_pnl,
            "matching_call_control_exit_reason": (control or {}).get("exit_reason"),
            "call_control_minus_dynamic_pnl_dollars": (
                round(control_pnl - selected_pnl, 2) if control_pnl is not None else None
            ),
            "call_control_would_have_done_better": (
                control_pnl > selected_pnl if control_pnl is not None else None
            ),
        })
        out.append(row)
    return out


def _credit_bucket(value: Any) -> str:
    credit = _f(value)
    if credit < 0.50:
        return "<0.50"
    if credit < 1.00:
        return "0.50-0.99"
    if credit < 1.50:
        return "1.00-1.49"
    return "1.50+"


def _distance_bucket(value: Any) -> str:
    distance = abs(_f(value))
    if distance < 10:
        return "<10"
    if distance < 25:
        return "10-24.99"
    if distance < 50:
        return "25-49.99"
    return "50+"


def _day_of_week(value: Any) -> str:
    try:
        return date.fromisoformat(str(value)).strftime("%A")
    except ValueError:
        return "Unknown"


def _entry_time_bucket(value: Any) -> str:
    raw = str(value or "")
    if "T" in raw:
        raw = raw.split("T", 1)[1]
    try:
        hour = int(raw.split(":", 1)[0])
    except (TypeError, ValueError):
        return "Unknown"
    if hour < 12:
        return "Morning"
    if hour < 15:
        return "Midday"
    return "EOD"


def call_control_winners_losers(result: Any) -> list[dict[str, Any]]:
    """Trade-level call-control edge audit rows."""
    out: list[dict[str, Any]] = []
    for trade in result.trades:
        if _profile_kind(trade) != "control" or trade.get("side") != _CALL:
            continue
        row = dict(trade)
        row.update({
            "outcome": _outcome(trade.get("pnl_dollars")),
            "credit_bucket": _credit_bucket(trade.get("entry_credit_points")),
            "distance_bucket": _distance_bucket(trade.get("distance_from_spot_to_short")),
            "day_of_week": _day_of_week(trade.get("date")),
            "entry_time_bucket": _entry_time_bucket(trade.get("entry_timestamp")),
        })
        out.append(row)
    return out


def call_control_edge_summary(result: Any) -> list[dict[str, Any]]:
    """Normalized call-control metrics by each requested research dimension."""
    rows = call_control_winners_losers(result)
    dimensions = (
        ("threshold", "threshold"),
        ("entry_window", "entry_target"),
        ("credit_bucket", "credit_bucket"),
        ("distance_bucket", "distance_bucket"),
        ("wds_tier", "wds_tier"),
        ("corridor", "corridor_valid"),
        ("gamma_regime", "gamma_regime"),
        ("gamma_relationship", "gamma_relationship"),
        ("exit_reason", "exit_reason"),
        ("day_of_week", "day_of_week"),
        ("entry_time_bucket", "entry_time_bucket"),
    )
    out: list[dict[str, Any]] = []
    for dimension, key in dimensions:
        values = sorted({str(row.get(key) or "unavailable") for row in rows})
        for value in values:
            grouped = [row for row in rows if str(row.get(key) or "unavailable") == value]
            metric = reports.metrics(grouped)
            out.append({
                "dimension": dimension,
                "value": value,
                "trades": metric["total_trades"],
                "wins": sum(_f(row.get("pnl_dollars")) > 0 for row in grouped),
                "losses": sum(_f(row.get("pnl_dollars")) < 0 for row in grouped),
                "win_rate": metric["win_rate"],
                "total_pnl_dollars": metric["total_pnl_dollars"],
                "average_pnl_dollars": metric["avg_pnl_dollars"],
                "expectancy_dollars": metric["expectancy_dollars"],
                "profit_factor": metric["profit_factor"],
                "max_drawdown_dollars": metric["max_drawdown_dollars"],
            })
    return out


def _weak_wds(row: dict[str, Any]) -> bool:
    tier = int(_f(row.get("selected_wds_tier")))
    wds = _f(row.get("selected_active_wds"), _f(row.get("selected_raw_wds")))
    return tier >= 3 or (wds > 0 and wds < 0.50)


def _gamma_conflict(row: dict[str, Any]) -> bool:
    regime = str(row.get("gamma_regime") or "").lower()
    side = row.get("selected_side")
    return (side == _PUT and regime == "negative") or (side == _CALL and regime == "positive")


def _failure_bucket(row: dict[str, Any]) -> str | None:
    selected_pnl = _f(row.get("selected_pnl_dollars"))
    opposite_pnl = (
        _f(row.get("opposite_pnl_dollars"))
        if row.get("opposite_pnl_dollars") not in (None, "") else None
    )
    if not _truthy(row.get("opposite_available")):
        return "no opposite-side candidate available"
    if row.get("selected_side") == _PUT and selected_pnl < 0:
        return "chose put-credit and put side lost"
    if (
        row.get("selected_side") == _CALL
        and _truthy(row.get("call_control_would_have_done_better"))
        and selected_pnl < 0
    ):
        return "chose call-credit but call control would have done better"
    if selected_pnl < 0 and not _truthy(row.get("selected_corridor_valid")):
        return "corridor inactive"
    if selected_pnl < 0 and _weak_wds(row):
        return "weak WDS"
    if selected_pnl < 0 and _gamma_conflict(row):
        return "gamma context conflicted"
    if opposite_pnl is not None and opposite_pnl > selected_pnl:
        advantage = str(row.get("selected_advantage_component") or "")
        if advantage == "premium_score":
            return "premium too attractive but distance poor"
        if advantage == "distance_safety_score":
            return "distance safe but credit too low"
        if (
            row.get("selected_exit_reason") == "SL"
            and row.get("opposite_exit_reason") in {"TP", "EOD"}
        ):
            return "TP/SL mismatch"
        return "unknown/needs review"
    return None


def dynamic_failure_taxonomy(result: Any) -> list[dict[str, Any]]:
    """One deterministic primary failure bucket per losing/missed opportunity."""
    comparisons = dynamic_vs_best_opposite(result)
    out: list[dict[str, Any]] = []
    for row in comparisons:
        bucket = _failure_bucket(row)
        if bucket is None:
            continue
        out.append({
            "record_type": "selected_trade",
            "date": row.get("date"),
            "profile_id": row.get("profile_id"),
            "failure_bucket": bucket,
            "selected_side": row.get("selected_side"),
            "selected_pnl_dollars": row.get("selected_pnl_dollars"),
            "opposite_pnl_dollars": row.get("opposite_pnl_dollars"),
            "matching_call_control_pnl_dollars": row.get("matching_call_control_pnl_dollars"),
            "selected_advantage_component": row.get("selected_advantage_component"),
            "selection_reason": row.get("selection_reason"),
        })
    dynamic_ids = {
        str(row.get("profile_id"))
        for row in result.candidates
        if _profile_kind(row) == "dynamic"
    }
    for row in result.no_trade_reasons:
        if str(row.get("profile_id")) not in dynamic_ids:
            continue
        if _f(row.get("quote_filtered_count")) > 0 or _f(row.get("risk_filtered_count")) > 0:
            bucket = "quote/risk validation filtered better side"
        elif _f(row.get("candidate_count")) < 2:
            bucket = "no opposite-side candidate available"
        else:
            bucket = "unknown/needs review"
        out.append({
            "record_type": "missed_opportunity",
            "date": row.get("date"),
            "profile_id": row.get("profile_id"),
            "failure_bucket": bucket,
            "selected_side": None,
            "selected_pnl_dollars": None,
            "opposite_pnl_dollars": None,
            "matching_call_control_pnl_dollars": None,
            "selected_advantage_component": None,
            "selection_reason": row.get("first_blocker") or row.get("reason"),
        })
    return out


def dynamic_failure_summary(result: Any) -> list[dict[str, Any]]:
    rows = dynamic_failure_taxonomy(result)
    out: list[dict[str, Any]] = []
    for bucket, count in Counter(str(row["failure_bucket"]) for row in rows).most_common():
        grouped = [row for row in rows if row["failure_bucket"] == bucket]
        out.append({
            "failure_bucket": bucket,
            "count": count,
            "selected_trade_count": sum(row["record_type"] == "selected_trade" for row in grouped),
            "missed_opportunity_count": sum(
                row["record_type"] == "missed_opportunity" for row in grouped
            ),
            "selected_pnl_dollars": round(
                sum(_f(row.get("selected_pnl_dollars")) for row in grouped), 2
            ),
            "opposite_pnl_dollars": round(
                sum(_f(row.get("opposite_pnl_dollars")) for row in grouped), 2
            ),
        })
    return out


def _pooled_by_kind(result: Any, kind: str) -> dict[str, Any]:
    return _metric_summary(
        [trade for trade in result.trades if _profile_kind(trade) == kind]
    )


def attribution_narrative(result: Any) -> str:
    sides = {row["selected_side"]: row for row in selected_side_summary(result)}
    put = sides.get(_PUT, {})
    controls = _pooled_by_kind(result, "control")
    availability = sum(_truthy(row.get("opposite_outcome_simulated")) for row in result.dynamic_side_attribution)
    if not result.dynamic_side_attribution or availability < len(result.dynamic_side_attribution) / 2:
        return "Insufficient attribution data. Need opposite-side simulation."
    failure_rows = dynamic_failure_summary(result)
    selected_failures = [
        row for row in failure_rows if _f(row.get("selected_trade_count")) > 0
    ]
    top = (
        max(selected_failures, key=lambda row: _f(row.get("selected_trade_count")))[
            "failure_bucket"
        ]
        if selected_failures
        else "no dominant selected-trade failure bucket"
    )
    component_counts = Counter(
        str(row.get("selected_advantage_component"))
        for row in dynamic_vs_best_opposite(result)
        if _truthy(row.get("opposite_would_have_done_better"))
    )
    component = component_counts.most_common(1)[0][0] if component_counts else "mixed factors"
    component = {
        "premium_score": "premium",
        "distance_safety_score": "distance",
        "structure_score": "structure",
        "maxvol_gamma_alignment_score": "gamma alignment",
        "existing_candidate_score": "candidate score",
    }.get(component, component.replace("_", " "))
    return (
        f"Dynamic underperformed mainly while selecting Put Credit {put.get('trades', 0)} "
        f"times with {_f(put.get('win_rate')) * 100:.1f}% win rate and "
        f"${_f(put.get('total_pnl_dollars')):,.2f} P&L, while call-only controls "
        f"produced ${_f(controls.get('total_pnl_dollars')):,.2f}. The top failure "
        f"bucket was {top}. In cases where the simulated opposite side did better, "
        f"the selected side most often held an advantage in {component}; this suggests "
        "a focused research test, not an automatic selector change."
    )


def research_recommendations(result: Any) -> list[dict[str, Any]]:
    """Deterministic notes only; never applied to profiles or selection."""
    sides = {row["selected_side"]: row for row in selected_side_summary(result)}
    failures = {row["failure_bucket"]: row for row in dynamic_failure_summary(result)}
    dynamic = _pooled_by_kind(result, "dynamic")
    controls = _pooled_by_kind(result, "control")
    recommendations: list[dict[str, Any]] = []

    def add(label: str, reason: str) -> None:
        recommendations.append({"recommendation": label, "reason": reason, "research_only": True})

    put = sides.get(_PUT, {})
    if _f(put.get("total_pnl_dollars")) < 0 and _f(controls.get("total_pnl_dollars")) > 0:
        add(
            "Consider testing dynamic-call-biased selector",
            "Put-credit selections lost money while call-only controls remained positive.",
        )
    if "gamma context conflicted" in failures or (
        _f(put.get("total_pnl_dollars")) < 0 and _f(put.get("trades")) >= 5
    ):
        add(
            "Consider requiring bullish regime for put-credit selection",
            "Put-credit performance warrants a regime-gated research comparison.",
        )
    if "corridor inactive" in failures or "weak WDS" in failures:
        add(
            "Consider WDS/corridor gate before allowing put-credit",
            "Attribution found losses linked to inactive corridor or weak WDS context.",
        )
    if _f(controls.get("total_pnl_dollars")) > 0 and _f(dynamic.get("total_pnl_dollars")) <= 0:
        add(
            "Consider call-only as current live-paper benchmark while dynamic is revised",
            "Positive control result does not mean production approval; it means the control "
            "is the current benchmark.",
        )
    morning = [row for row in result.trades if _profile_kind(row) == "dynamic" and row.get("entry_target") == "11:00"]
    eod = [row for row in result.trades if _profile_kind(row) == "dynamic" and row.get("entry_target") != "11:00"]
    if morning and eod and _metric_summary(morning)["total_pnl_dollars"] != _metric_summary(eod)["total_pnl_dollars"]:
        add(
            "Consider separate selector weights for morning vs EOD",
            "Morning and EOD dynamic profiles show different aggregate outcomes.",
        )
    return recommendations


def build_attribution_reports(result: Any) -> dict[str, Any]:
    """Build every Phase 10F attribution table and deterministic narrative."""
    return {
        "dynamic_side_attribution": list(result.dynamic_side_attribution),
        "selected_side_summary": selected_side_summary(result),
        "dynamic_vs_best_opposite": dynamic_vs_best_opposite(result),
        "call_control_edge_summary": call_control_edge_summary(result),
        "call_control_winners_losers": call_control_winners_losers(result),
        "dynamic_failure_taxonomy": dynamic_failure_taxonomy(result),
        "dynamic_failure_summary": dynamic_failure_summary(result),
        "research_recommendations": research_recommendations(result),
        "attribution_narrative": attribution_narrative(result),
        "control_benchmark_note": (
            "Positive control result does not mean production approval; it means the "
            "control is the current benchmark."
        ),
    }
