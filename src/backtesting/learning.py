"""Phase 11A deterministic learning layer over existing backtest records.

Research-only: this module normalizes replay outputs, summarizes empirical
feature performance, writes an assumption audit, and generates explainable
optimization hypotheses. It never changes selection, risk, pricing, lifecycle,
profiles on disk, or any live behavior.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.backtesting import mappers as M
from src.backtesting import reports
from src.backtesting.replay_runner import BacktestResult

MIN_EVIDENCE_TRADES = 5
MAX_LEARNED_PARAMETER_SETS = 24

_COMMON_FEATURES = (
    "symbol", "date", "dte", "profile_id", "profile_kind", "profile_family",
    "entry_target", "entry_time_bucket", "side", "threshold", "corridor_valid",
    "active_wds", "raw_wds", "wds_tier", "dominant_wing_side", "gamma_regime",
    "gamma_relationship", "primary_gamma", "secondary_gamma",
    "spot_relation_primary_gamma", "spot_relation_secondary_gamma",
    "credit", "credit_bucket", "max_risk", "reward_risk", "distance_to_short",
    "distance_bucket", "score", "selector_score", "selector_score_components",
    "tp_mode", "sl_mode", "exit_reason", "hold_minutes", "pnl_dollars",
    "outcome", "month",
)

_PERFORMANCE_DIMENSIONS = (
    ("entry_window", "entry_time_bucket"),
    ("side", "side"),
    ("threshold", "threshold"),
    ("wds_tier", "wds_tier"),
    ("corridor", "corridor_valid"),
    ("credit_bucket", "credit_bucket"),
    ("distance_bucket", "distance_bucket"),
    ("exit_reason", "exit_reason"),
    ("month", "month"),
    ("profile_family", "profile_family"),
    ("tp_mode", "tp_mode"),
    ("sl_mode", "sl_mode"),
    ("gamma_regime", "gamma_regime"),
    ("gamma_relationship", "gamma_relationship"),
)

_BENCHMARK_PROFILES = (
    "morning_5k_call_tp75_control",
    "morning_2k_call_no_tp_control",
    "morning_5k_dynamic_tp75",
    "morning_2k_dynamic_no_tp",
)


@dataclass(frozen=True)
class LearningConfig:
    symbol: str = "SPX"
    dte: int = 0
    profiles: tuple[str, ...] = ()
    run_label: str = "learn"
    starting_balance: float = 10000.0
    contracts: int = 1
    date_mode: str = "all_data"


@dataclass
class LearningResult:
    run_config: dict[str, Any]
    trade_features: list[dict[str, Any]]
    candidate_features: list[dict[str, Any]]
    no_trade_features: list[dict[str, Any]]
    performance_tables: dict[str, list[dict[str, Any]]]
    no_trade_blockers: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    learned_parameter_sets: list[dict[str, Any]]
    audit_markdown: str
    hypotheses_markdown: str


def _f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    if raw in {"true", "1", "yes", "y"}:
        return True
    if raw in {"false", "0", "no", "n"}:
        return False
    return None


def credit_bucket(value: Any) -> str:
    credit = _f(value)
    if credit is None:
        return "Unavailable"
    if credit < 0.50:
        return "<0.50"
    if credit < 1.00:
        return "0.50-0.99"
    if credit < 1.50:
        return "1.00-1.49"
    if credit < 2.00:
        return "1.50-1.99"
    return "2.00+"


def distance_bucket(value: Any) -> str:
    distance = _f(value)
    if distance is None:
        return "Unavailable"
    distance = abs(distance)
    if distance < 10:
        return "<10"
    if distance < 25:
        return "10-24.99"
    if distance < 50:
        return "25-49.99"
    return "50+"


def entry_time_bucket(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Unavailable"
    try:
        hour = int(raw.split("T")[-1].split(":")[0])
    except (TypeError, ValueError, IndexError):
        return raw
    if hour < 12:
        return "Morning"
    if hour >= 15:
        return "EOD"
    return "Midday"


def _relation(spot: Any, level: Any) -> str:
    spot_f, level_f = _f(spot), _f(level)
    if spot_f is None or level_f is None:
        return "unavailable"
    if spot_f < level_f:
        return "below"
    if spot_f > level_f:
        return "above"
    return "at"


def _profile_family(row: dict[str, Any]) -> str:
    kind = str(row.get("preset_kind") or row.get("profile_kind") or "").strip().lower()
    side = str(row.get("side") or "").strip().upper()
    profile = str(row.get("profile_id") or "").lower()
    if kind:
        return kind
    if "control" in profile:
        return "control"
    if "dynamic" in profile:
        return "dynamic"
    if side == "PUT_CREDIT" or "put" in profile:
        return "put_only"
    return "research"


def _selector_components(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _feature_row(row: dict[str, Any], *, outcome_row: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(row)
    outcome_row = outcome_row or row
    credit = _f(row.get("entry_credit_points"))
    distance = _f(row.get("distance_from_spot_to_short"))
    pnl = _f(outcome_row.get("pnl_dollars"))
    date = str(row.get("date") or "")
    out.update({
        "profile_kind": row.get("preset_kind") or row.get("profile_kind"),
        "profile_family": _profile_family(row),
        "entry_time_bucket": entry_time_bucket(
            row.get("entry_target") or row.get("entry_timestamp")
        ),
        "corridor_valid": _bool(row.get("corridor_valid")),
        "spot_relation_primary_gamma": _relation(row.get("spot"), row.get("primary_gamma")),
        "spot_relation_secondary_gamma": _relation(row.get("spot"), row.get("secondary_gamma")),
        "credit": credit,
        "credit_bucket": credit_bucket(credit),
        "max_risk": _f(row.get("max_risk_points")),
        "distance_to_short": distance,
        "distance_bucket": distance_bucket(distance),
        "pnl_dollars": pnl,
        "outcome": (
            "win" if pnl is not None and pnl > 0
            else "loss" if pnl is not None and pnl < 0
            else "breakeven" if pnl == 0
            else "unavailable"
        ),
        "month": date[:7] if len(date) >= 7 else "Unavailable",
    })
    for key, value in _selector_components(row.get("selector_score_components")).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[f"selector_component_{key}"] = value
    return out


def extract_feature_tables(result: BacktestResult) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Normalize trades, candidates, and no-trade rows without changing replay."""
    trades = [_feature_row(row) for row in result.trades]
    trade_index = {
        (
            str(row.get("date")), str(row.get("profile_id")), str(row.get("side")),
            str(row.get("short_strike")), str(row.get("long_strike")),
        ): row
        for row in result.trades
    }
    candidates: list[dict[str, Any]] = []
    for row in result.candidates:
        key = (
            str(row.get("date")), str(row.get("profile_id")), str(row.get("side")),
            str(row.get("short_strike")), str(row.get("long_strike")),
        )
        candidates.append(_feature_row(row, outcome_row=trade_index.get(key)))
    no_trades: list[dict[str, Any]] = []
    for row in result.no_trade_reasons:
        out = dict(row)
        out.update({
            "profile_kind": None,
            "profile_family": _profile_family(row),
            "entry_time_bucket": entry_time_bucket(
                row.get("entry_target") or row.get("entry_timestamp")
            ),
            "corridor_valid": _bool(row.get("corridor_valid")),
            "credit_bucket": "Unavailable",
            "distance_bucket": "Unavailable",
            "top_blocker": (
                row.get("first_blocker") or row.get("top_selector_reason")
                or row.get("top_risk_reason") or row.get("top_quote_reason")
                or row.get("reason") or "unknown"
            ),
            "month": str(row.get("date") or "")[:7] or "Unavailable",
        })
        no_trades.append(out)
    return trades, candidates, no_trades


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = row.get(key)
        label = "Unavailable" if value in (None, "") else str(value)
        grouped.setdefault(label, []).append(row)
    return grouped


def performance_summary(
    trades: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    dimension: str,
    feature_key: str,
    starting_balance: float,
    contracts: int,
) -> list[dict[str, Any]]:
    trade_groups = _group(trades, feature_key)
    candidate_groups = _group(candidates, feature_key)
    rows: list[dict[str, Any]] = []
    for bucket in sorted(set(trade_groups) | set(candidate_groups)):
        bucket_trades = trade_groups.get(bucket, [])
        metric = reports.metrics(
            bucket_trades,
            starting_balance=starting_balance,
            contracts=contracts,
        )
        rows.append({
            "feature": dimension,
            "bucket": bucket,
            "candidate_count": len(candidate_groups.get(bucket, [])),
            "trade_count": metric["total_trades"],
            "win_rate": metric["win_rate"],
            "total_pnl_dollars": metric["total_pnl_dollars"],
            "expectancy_dollars": metric["expectancy_dollars"],
            "profit_factor": metric["profit_factor"],
            "max_drawdown_dollars": metric["max_drawdown_dollars"],
            "max_drawdown_pct": metric["max_drawdown_pct"],
            "avg_credit_points": metric["avg_credit_points"],
            "avg_distance_to_short": metric["avg_distance_to_short"],
            "positive_expectancy": (
                metric["expectancy_dollars"] is not None
                and float(metric["expectancy_dollars"]) > 0
            ),
            "low_sample_warning": metric["total_trades"] < MIN_EVIDENCE_TRADES,
        })
    return rows


def build_performance_tables(
    trades: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    starting_balance: float,
    contracts: int,
) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    combined: list[dict[str, Any]] = []
    for name, key in _PERFORMANCE_DIMENSIONS:
        rows = performance_summary(
            trades,
            candidates,
            dimension=name,
            feature_key=key,
            starting_balance=starting_balance,
            contracts=contracts,
        )
        tables[name] = rows
        combined.extend(rows)
    tables["feature_performance_summary"] = combined
    return tables


def no_trade_blocker_summary(no_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = _group(no_trades, "top_blocker")
    total = len(no_trades)
    rows: list[dict[str, Any]] = []
    for blocker, values in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        rows.append({
            "blocker": blocker,
            "no_trade_rows": len(values),
            "share_of_no_trade_rows": round(len(values) / total, 4) if total else 0.0,
            "candidate_count": sum(int(_f(row.get("candidate_count")) or 0) for row in values),
            "eligible_candidate_count": sum(
                int(_f(row.get("eligible_candidate_count")) or 0) for row in values
            ),
            "risk_filtered_count": sum(
                int(_f(row.get("risk_filtered_count")) or 0) for row in values
            ),
            "quote_filtered_count": sum(
                int(_f(row.get("quote_filtered_count")) or 0) for row in values
            ),
            "score_filtered_count": sum(
                int(_f(row.get("score_filtered_count")) or 0) for row in values
            ),
            "selector_filtered_count": sum(
                int(_f(row.get("selector_filtered_count")) or 0) for row in values
            ),
            "potential_trade_slots_if_removed": len(values),
            "interpretation": (
                "Upper bound only; removing a blocker does not prove the skipped trade "
                "would be profitable."
            ),
        })
    return rows


def _best(tables: dict[str, list[dict[str, Any]]], key: str) -> dict[str, Any]:
    rows = tables.get(key, [])
    if not rows:
        return {}
    return max(
        rows,
        key=lambda row: (
            int(_f(row.get("trade_count")) or 0) >= MIN_EVIDENCE_TRADES,
            _f(row.get("expectancy_dollars")) or -1e12,
            _f(row.get("total_pnl_dollars")) or -1e12,
            str(row.get("bucket")),
        ),
    )


def _evidence(row: dict[str, Any]) -> str:
    if not row:
        return "No usable evidence was available."
    return (
        f"{row.get('bucket')} produced {int(_f(row.get('trade_count')) or 0)} trades, "
        f"${_f(row.get('expectancy_dollars')) or 0:,.2f} expectancy, "
        f"${_f(row.get('total_pnl_dollars')) or 0:,.2f} total P&L, and "
        f"{(_f(row.get('win_rate')) or 0) * 100:.1f}% win rate."
    )


def _parameter_template() -> dict[str, Any]:
    return {
        "entry_target": "11:00",
        "threshold": "5k",
        "side_policy": "call_only",
        "selector": "score_best_valid",
        "take_profit": 0.75,
        "stop_loss": 1.50,
        "corridor_gate": "off",
        "wds_gate": "off",
        "min_credit": None,
        "distance_rule": "none",
    }


def _base_profile(parameters: dict[str, Any]) -> str:
    morning = parameters["entry_target"] == "11:00"
    threshold = parameters["threshold"]
    side = parameters["side_policy"]
    tp = parameters["take_profit"]
    if side == "call_only":
        if morning and threshold == "2k" and tp is None:
            return "morning_2k_call_no_tp_control"
        if morning:
            return "morning_5k_call_tp75_control"
        return "eod_5k_call_sl150_no_tp_control"
    if morning and threshold == "2k" and tp is None:
        return "morning_2k_dynamic_no_tp"
    if morning:
        return "morning_5k_dynamic_tp75"
    return "eod_5k_dynamic_sl150_no_tp"


def _parameters_from_evidence(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    parameters = _parameter_template()
    entry = str(_best(tables, "entry_window").get("bucket") or "")
    parameters["entry_target"] = "15:15" if entry == "EOD" else "11:00"
    side = str(_best(tables, "side").get("bucket") or "")
    parameters["side_policy"] = {
        "CALL_CREDIT": "call_only",
        "PUT_CREDIT": "put_only",
    }.get(side, "dynamic_both")
    threshold = str(_best(tables, "threshold").get("bucket") or "").lower()
    parameters["threshold"] = threshold if threshold in {"2k", "5k", "10k"} else "5k"
    corridor = _best(tables, "corridor")
    parameters["corridor_gate"] = (
        "active_required"
        if str(corridor.get("bucket")).lower() == "true"
        and (_f(corridor.get("expectancy_dollars")) or 0) > 0
        else "off"
    )
    wds = str(_best(tables, "wds_tier").get("bucket") or "")
    parameters["wds_gate"] = "tier_1_2" if wds in {"1", "2", "1.0", "2.0"} else "off"
    credit = str(_best(tables, "credit_bucket").get("bucket") or "")
    parameters["min_credit"] = 1.50 if credit.startswith("1.50") or credit == "2.00+" else (
        1.00 if credit.startswith("1.00") else None
    )
    distance = str(_best(tables, "distance_bucket").get("bucket") or "")
    parameters["distance_rule"] = "avoid_too_close" if distance in {"10-24.99", "25-49.99", "50+"} else "none"
    tp = str(_best(tables, "tp_mode").get("bucket") or "")
    parameters["take_profit"] = 0.75 if tp == "TP75" else 0.50 if tp == "TP50" else None
    sl = str(_best(tables, "sl_mode").get("bucket") or "")
    parameters["stop_loss"] = 2.00 if sl == "SL200" else 1.50
    parameters["selector"] = (
        "balanced_structure_premium_valid"
        if parameters["side_policy"] == "dynamic_both"
        else "score_best_valid"
    )
    return parameters


def generate_hypotheses(
    tables: dict[str, list[dict[str, Any]]],
    blockers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate deterministic hypotheses and a bounded learned optimizer grid."""
    best = {name: _best(tables, name) for name in (
        "entry_window", "side", "threshold", "wds_tier", "corridor",
        "credit_bucket", "distance_bucket", "tp_mode", "sl_mode", "gamma_regime",
    )}
    primary = _parameters_from_evidence(tables)
    hypothesis_specs = [
        (
            "LEARNED_CORE",
            "Test the strongest observed entry, side, threshold, and exit family together.",
            " ".join(_evidence(best[key]) for key in ("entry_window", "side", "threshold")),
            primary,
            "The apparent edge may be caused by a few concentrated dates or interactions "
            "that do not survive chronological validation.",
        ),
        (
            "LEARNED_STRUCTURE_GATE",
            "Test whether empirically favored corridor and WDS conditions improve robustness.",
            " ".join(_evidence(best[key]) for key in ("corridor", "wds_tier")),
            {**primary, "corridor_gate": "active_required", "wds_gate": "tier_1_2"},
            "Structure gates may reduce trade count below a useful validation sample.",
        ),
        (
            "LEARNED_PREMIUM_DISTANCE",
            "Test the empirically favored premium and distance family without changing pricing.",
            " ".join(_evidence(best[key]) for key in ("credit_bucket", "distance_bucket")),
            primary,
            "Credit and distance buckets may proxy for volatility regimes rather than a "
            "repeatable standalone edge.",
        ),
        (
            "LEARNED_SIDE_CHECK",
            "Retest side policy explicitly because dynamic underperformance may be driven by "
            "put-credit selection.",
            _evidence(best["side"]),
            {**primary, "side_policy": "call_only", "selector": "score_best_valid"},
            "A call-only result can be regime-specific and is a benchmark, not production approval.",
        ),
    ]
    hypotheses: list[dict[str, Any]] = []
    learned_sets: list[dict[str, Any]] = []
    for hypothesis_id, idea, evidence, parameters, failure in hypothesis_specs:
        low_sample = any(
            int(_f(best[key].get("trade_count")) or 0) < MIN_EVIDENCE_TRADES
            for key in ("entry_window", "side", "threshold") if best[key]
        )
        hypotheses.append({
            "hypothesis_id": hypothesis_id,
            "idea": idea,
            "evidence": evidence,
            "required_data_fields": (
                "entry_target, side, threshold, corridor_valid, wds_tier, credit, "
                "distance_to_short, TP/SL/EOD, pnl_dollars"
            ),
            "proposed_strategy_profile": _base_profile(parameters),
            "proposed_parameter_grid": parameters,
            "expected_failure_mode": failure,
            "validation_plan": (
                "Run deterministic chronological train/validation/holdout optimization; "
                "rank without holdout and reject low-sample or unstable results."
            ),
            "status": "proposed",
            "research_stage": "backtest_only" if low_sample else "backtest_then_forward_paper_review",
            "low_sample_warning": low_sample,
        })
        variants = [
            dict(parameters),
            {**parameters, "take_profit": None},
            {**parameters, "stop_loss": 2.00},
            {**parameters, "corridor_gate": "off", "wds_gate": "off"},
        ]
        for index, variant in enumerate(variants, start=1):
            learned_sets.append({
                "hypothesis_id": hypothesis_id,
                "variant_id": f"{hypothesis_id}_V{index}",
                "base_profile_id": _base_profile(variant),
                "parameters": variant,
                "benchmark": False,
            })
    base_params = _parameter_template()
    for profile_id in _BENCHMARK_PROFILES:
        learned_sets.insert(0, {
            "hypothesis_id": "BENCHMARK",
            "variant_id": f"BENCHMARK_{profile_id}",
            "base_profile_id": profile_id,
            "parameters": {
                **base_params,
                "entry_target": "base",
                "threshold": "base",
                "side_policy": "base",
                "selector": "base",
                "take_profit": "base",
                "stop_loss": "base",
                "min_credit": "base",
                "distance_rule": "base",
            },
            "benchmark": True,
        })
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in learned_sets:
        signature = json.dumps(
            {"base": row["base_profile_id"], "parameters": row["parameters"]},
            sort_keys=True,
            default=str,
        )
        if signature not in seen:
            seen.add(signature)
            deduped.append(row)
    return hypotheses, deduped[:MAX_LEARNED_PARAMETER_SETS]


def build_assumption_audit(result: BacktestResult) -> str:
    c = result.counters
    rows = [
        ("Entry time/window", "Closest saved snapshot inside configured windows; morning is 10:55-11:05 and EOD targets use +/-15 minutes.", "Exploratory scripts may have used exact timestamps, first-after, wider windows, or a different intraday time.", "High: changes fills, trade count, and remaining lifecycle."),
        ("Wing threshold", "Profile-driven 2K/5K/10K volume threshold.", "Earlier studies may have focused on 10K Wingonomics or one threshold only.", "High: changes anchor strikes and available premium."),
        ("Side policy", "Profile-driven call-only, put-only, or dynamic selector using the live selector.", "Earlier results may have been call-only or selected a side after seeing outcomes.", "High: Phase 10F already found put selection can hurt dynamic results."),
        ("Structure/corridor", "Wings derive from saved per-strike volume; corridor and WDS are recorded. Gates apply only to generated research profiles that explicitly request them.", "Exploratory work may have required an active 10K corridor or treated any wing as active.", "Medium/high: gates can improve quality but sharply reduce sample size."),
        ("Quote/pricing", "Entry credit uses saved bid/ask-derived option-chain mids; exits reprice mid-to-mid.", "Earlier scripts may have used theoretical values, different marks, or favorable fills.", "High: fill assumptions directly change P&L."),
        ("Spread width", "Profile-driven width, typically 5 points.", "Earlier work may have used a different width or single-leg proxy.", "High: changes credit, risk, and lifecycle thresholds."),
        ("DTE", "0DTE and 1DTE are separate data buckets; CLI DTE overrides profile target DTE.", "Earlier findings may have mixed 0DTE and 1DTE or used after-hours fallback expiries.", "High: different surface and settlement behavior."),
        ("TP/SL", "TP is credit capture; SL is loss-on-credit; first event wins and SL wins a same-snapshot conflict.", "Prior scripts may have interpreted SL150 as 1.5x debit instead of 150% loss, or used close-only exits.", "High: lifecycle semantics materially change tails."),
        ("EOD/settlement", "Unclosed positions cash-settle from the first snapshot at/after 16:00.", "Earlier work may have exited at 15:55, last quote, or ignored intrinsic settlement.", "High on breach days."),
        ("Risk/selector filters", "Live risk filters, readiness gates, score threshold/edge, allowed sides, and live selector are reused.", "Exploratory scripts may have selected every structurally valid candidate.", "High: realistic filters reduce trade count and can remove attractive-looking rough signals."),
        ("Missing snapshots/prices", "Missing entry snapshots become no-trade rows; unpriceable exits become SKIPPED with counters.", "Earlier scripts may have carried prices forward or dropped missing days silently.", "Medium/high: changes denominator and can hide difficult days."),
        ("Sizing", "Fixed contracts scale P&L; account metrics start at configured balance.", "Earlier reports may have treated one spread point as one dollar or computed equity from zero.", "High for dollar results; none for selection."),
        ("Duplicate/skip handling", "One evaluation per profile/date; selected trade count is capped by profile selector settings.", "Earlier scripts may have allowed multiple entries or repeated snapshots.", "High for trade count and concentration."),
    ]
    lines = [
        "# Backtest Assumption Audit",
        "",
        "This is an evidence checklist, not a claim that prior exploratory work was wrong. "
        "The current replay is stricter because it reuses the live candidate, risk, readiness, "
        "selector, and lifecycle paths over saved bid/ask snapshots.",
        "",
        f"Current run: {c.get('dates_evaluated', 0)} dates, {c.get('candidates', 0)} candidates, "
        f"{c.get('selected_trades', 0)} selected trades, and {c.get('no_trade_rows', 0)} no-trade rows.",
        "",
        "| Assumption | Current engine | Possible exploratory difference to verify | Materiality |",
        "|---|---|---|---|",
    ]
    lines.extend(f"| {a} | {b} | {d} | {m} |" for a, b, d, m in rows)
    lines.extend([
        "",
        "## What needs testing rather than guessing",
        "",
        "Run controlled one-factor comparisons for entry window, threshold, side, corridor/WDS "
        "gates, credit/distance buckets, TP/SL, and DTE. Preserve the same dates and pricing "
        "source. Report skipped/missing rows and use chronological validation/holdout before "
        "calling any apparent edge repeatable.",
    ])
    return "\n".join(lines) + "\n"


def _hypotheses_markdown(hypotheses: list[dict[str, Any]]) -> str:
    lines = ["# Generated Strategy Hypotheses", "", "Deterministic research suggestions only. "
             "They do not change live strategy behavior.", ""]
    for row in hypotheses:
        lines.extend([
            f"## {row['hypothesis_id']}: {row['idea']}",
            "",
            f"- Evidence: {row['evidence']}",
            f"- Proposed base profile: `{row['proposed_strategy_profile']}`",
            f"- Expected failure mode: {row['expected_failure_mode']}",
            f"- Validation plan: {row['validation_plan']}",
            f"- Stage: {row['research_stage']}",
            "",
        ])
    return "\n".join(lines)


def run_learning(result: BacktestResult, config: LearningConfig | None = None) -> LearningResult:
    config = config or LearningConfig(
        symbol=str(result.run_config.get("symbol") or "SPX"),
        dte=1 if str(result.run_config.get("dte")) == "1DTE" else 0,
        profiles=tuple(result.run_config.get("profiles") or ()),
        run_label=str(result.run_config.get("run_label") or "learn"),
        starting_balance=float(result.run_config.get("starting_balance") or 10000.0),
        contracts=int(result.run_config.get("contracts") or 1),
    )
    trades, candidates, no_trades = extract_feature_tables(result)
    tables = build_performance_tables(
        trades,
        candidates,
        starting_balance=config.starting_balance,
        contracts=config.contracts,
    )
    blockers = no_trade_blocker_summary(no_trades)
    hypotheses, learned_sets = generate_hypotheses(tables, blockers)
    run_config = {
        **asdict(config),
        "learning": True,
        "source_backtest_run_config": result.run_config,
        "source_counters": result.counters,
        "hypothesis_count": len(hypotheses),
        "learned_parameter_set_count": len(learned_sets),
        "min_evidence_trades": MIN_EVIDENCE_TRADES,
        "no_broker": True,
        "no_execution": True,
        "no_order_preview": True,
        "live_strategy_behavior_changed": False,
    }
    return LearningResult(
        run_config=run_config,
        trade_features=trades,
        candidate_features=candidates,
        no_trade_features=no_trades,
        performance_tables=tables,
        no_trade_blockers=blockers,
        hypotheses=hypotheses,
        learned_parameter_sets=learned_sets,
        audit_markdown=build_assumption_audit(result),
        hypotheses_markdown=_hypotheses_markdown(hypotheses),
    )


def research_base() -> Path:
    return M.output_base().parent / "research"


def research_latest_dir() -> Path:
    path = research_base() / "latest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_run_dir(run_id: str) -> Path:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in run_id)[:80]
    path = research_base() / "runs" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: tuple[str, ...] = ()) -> None:
    resolved = list(columns)
    for row in rows:
        for key in row:
            if key not in resolved:
                resolved.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=resolved, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_learning_reports(result: LearningResult, out_dirs: list[Path]) -> list[Path]:
    file_tables = {
        "trade_feature_table": (result.trade_features, _COMMON_FEATURES),
        "candidate_feature_table": (result.candidate_features, _COMMON_FEATURES),
        "no_trade_feature_table": (result.no_trade_features, ()),
        "feature_performance_summary": (
            result.performance_tables["feature_performance_summary"], ()
        ),
        "by_entry_window": (result.performance_tables["entry_window"], ()),
        "by_side": (result.performance_tables["side"], ()),
        "by_threshold": (result.performance_tables["threshold"], ()),
        "by_wds_tier": (result.performance_tables["wds_tier"], ()),
        "by_corridor": (result.performance_tables["corridor"], ()),
        "by_credit_bucket": (result.performance_tables["credit_bucket"], ()),
        "by_distance_bucket": (result.performance_tables["distance_bucket"], ()),
        "by_exit_reason": (result.performance_tables["exit_reason"], ()),
        "by_month": (result.performance_tables["month"], ()),
        "by_profile_family": (result.performance_tables["profile_family"], ()),
        "no_trade_blocker_summary": (result.no_trade_blockers, ()),
    }
    payload = {
        "hypotheses": result.hypotheses,
        "learned_parameter_sets": result.learned_parameter_sets,
        "benchmark_profile_ids": list(_BENCHMARK_PROFILES),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "research_only": True,
    }
    for directory in out_dirs:
        directory.mkdir(parents=True, exist_ok=True)
        for name, (rows, columns) in file_tables.items():
            _write_csv(directory / f"{name}.csv", rows, columns)
        (directory / "backtest_assumption_audit.md").write_text(
            result.audit_markdown, encoding="utf-8"
        )
        (directory / "generated_strategy_hypotheses.md").write_text(
            result.hypotheses_markdown, encoding="utf-8"
        )
        (directory / "generated_strategy_hypotheses.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        (directory / "run_config.json").write_text(
            json.dumps(result.run_config, indent=2, default=str), encoding="utf-8"
        )
    return out_dirs


def load_learned_parameter_sets(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = Path(path) if path else research_latest_dir() / "generated_strategy_hypotheses.json"
    if source.is_dir():
        source = source / "generated_strategy_hypotheses.json"
    if not source.is_file():
        raise ValueError(
            f"learned hypotheses not found at {source}; run python -m scripts.backtest_learn first"
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"learned hypotheses are unreadable: {source}") from exc
    rows = payload.get("learned_parameter_sets") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("learned hypotheses contain no parameter sets")
    return rows[:MAX_LEARNED_PARAMETER_SETS]


def load_backtest_result(directory: str | Path) -> BacktestResult:
    """Load existing backtest CSV outputs for research-only re-analysis."""
    root = Path(directory)

    def _read(name: str) -> list[dict[str, Any]]:
        path = root / name
        if not path.is_file():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    try:
        config = json.loads((root / "run_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    return BacktestResult(
        run_config=config,
        trades=_read("trades.csv"),
        candidates=_read("candidates.csv"),
        no_trade_reasons=_read("no_trade_reasons.csv"),
        counters=dict(config.get("counters") or {}),
    )
