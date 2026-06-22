"""Candidate evaluation pipeline for research and backtest attribution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.strategy_engine.candidates import build_credit_spread
from src.strategy_engine.regime_compatibility import (
    CompatibilityLabel,
    RegimeCompatibility,
    RegimeContext,
    evaluate_regime_compatibility,
)
from src.strategy_engine.risk_quality import (
    EvaluationStatus,
    RiskQualityAssessment,
    RiskQualityConfig,
    evaluate_risk_quality,
)
from src.strategy_engine.types import (
    LegAction,
    OptionRight,
    StrategyArchetype,
    StrategyCandidate,
    StrategyLeg,
)


@dataclass(frozen=True)
class EvaluatedCandidate:
    candidate: StrategyCandidate
    risk_quality: RiskQualityAssessment
    regime_compatibility: RegimeCompatibility
    quality_score: float
    status: EvaluationStatus
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationBatch:
    accepted: tuple[EvaluatedCandidate, ...]
    warned: tuple[EvaluatedCandidate, ...]
    rejected: tuple[EvaluatedCandidate, ...]
    ranked: tuple[EvaluatedCandidate, ...]
    rejection_reasons: dict[str, int]


def _quality_score(risk: RiskQualityAssessment, regime: RegimeCompatibility) -> float:
    score = 50.0
    score += {"GOOD": 25.0, "ACCEPTABLE": 15.0, "EOD_EXCEPTION_ONLY": 0.0, "TOO_CHEAP_FOR_RISK": -25.0, "REJECT": -35.0}[risk.label]
    score += {CompatibilityLabel.COMPATIBLE: 10.0, CompatibilityLabel.UNKNOWN: 0.0, CompatibilityLabel.INCOMPATIBLE: -30.0}[regime.label]
    score += min(15.0, max(-15.0, float(risk.risk_reward or 0.0) * 20.0))
    if risk.quote_quality_status == "unusable":
        score -= 20.0
    return round(max(0.0, min(100.0, score)), 4)


def evaluate_candidates(
    candidates: list[StrategyCandidate] | tuple[StrategyCandidate, ...],
    *,
    regime_contexts: dict[str, RegimeContext] | None = None,
    risk_config: RiskQualityConfig | None = None,
) -> EvaluationBatch:
    evaluated: list[EvaluatedCandidate] = []
    reason_counts: dict[str, int] = {}
    for candidate in candidates:
        regime = evaluate_regime_compatibility(
            candidate, (regime_contexts or {}).get(candidate.candidate_id)
        )
        risk = evaluate_risk_quality(candidate, config=risk_config, regime=regime)
        status = risk.status
        if regime.label == CompatibilityLabel.INCOMPATIBLE:
            status = EvaluationStatus.REJECT
        reasons = tuple(dict.fromkeys((*candidate.reason_codes, *regime.reason_codes, *risk.reason_codes)))
        for reason in reasons:
            if status == EvaluationStatus.REJECT:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        evaluated.append(EvaluatedCandidate(
            candidate, risk, regime, _quality_score(risk, regime), status, reasons
        ))
    ranked = tuple(sorted(evaluated, key=lambda row: (-row.quality_score, row.candidate.candidate_id)))
    return EvaluationBatch(
        accepted=tuple(row for row in ranked if row.status == EvaluationStatus.PASS),
        warned=tuple(row for row in ranked if row.status == EvaluationStatus.WARN),
        rejected=tuple(row for row in ranked if row.status == EvaluationStatus.REJECT),
        ranked=ranked,
        rejection_reasons=dict(sorted(reason_counts.items())),
    )


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _minutes_to_close(value: Any) -> int | None:
    raw = str(value or "")
    try:
        time = raw.split("T")[-1]
        hour, minute = (int(piece) for piece in time.split(":")[:2])
    except (TypeError, ValueError, IndexError):
        return None
    return max(0, 16 * 60 - (hour * 60 + minute))


def evaluate_backtest_row(row: dict[str, Any]) -> dict[str, Any]:
    """Adapt an existing credit-spread replay row into research-only quality fields."""
    side = str(row.get("side") or "")
    if side not in {"CALL_CREDIT", "PUT_CREDIT"}:
        return {
            "archetype": "UNAVAILABLE",
            "risk_quality_label": "UNAVAILABLE",
            "risk_quality_reason_codes": "",
            "regime_compatibility_label": "unknown",
            "regime_compatibility_reason_codes": "regime_unavailable",
        }
    credit = _float(row.get("entry_credit_points"), 0.0) or 0.0
    contracts = int(_float(row.get("contracts"), 1.0) or 1)
    short_strike = _float(row.get("short_strike"), 0.0) or 0.0
    long_strike = _float(row.get("long_strike"), 0.0) or 0.0
    right = OptionRight.CALL if side == "CALL_CREDIT" else OptionRight.PUT
    quote_quality = row.get("quote_quality_bucket") or (
        "usable" if str(row.get("candidate_passes_quote_filters")).lower() in {"true", "1"}
        else "unknown"
    )
    sl_label = str(row.get("sl_mode") or "")
    sl_multiple = 1.0 if "100" in sl_label else 1.5 if "150" in sl_label else 2.0 if "200" in sl_label else None
    timestamp_raw = str(row.get("entry_timestamp") or row.get("date") or "1970-01-01")
    try:
        timestamp = datetime.fromisoformat(timestamp_raw)
    except ValueError:
        timestamp = datetime(1970, 1, 1)
    candidate = build_credit_spread(
        timestamp=timestamp, symbol=str(row.get("symbol") or ""),
        dte=int(str(row.get("dte") or "0").replace("DTE", "") or 0),
        expiry=str(row.get("expiry") or row.get("date") or ""),
        archetype=(StrategyArchetype.CALL_CREDIT_SPREAD if side == "CALL_CREDIT" else StrategyArchetype.PUT_CREDIT_SPREAD),
        short_leg=StrategyLeg(f"BACKTEST:{side}:SHORT", short_strike, right, LegAction.SELL, None, None, None),
        long_leg=StrategyLeg(f"BACKTEST:{side}:LONG", long_strike, right, LegAction.BUY, None, None, None),
        credit=credit, contracts=contracts,
        expected_entry_window=str(row.get("entry_target") or ""),
        time_to_close_minutes=_minutes_to_close(row.get("entry_timestamp") or row.get("entry_target")),
        distance_to_short_strike=_float(row.get("distance_from_spot_to_short")),
        regime_label=str(row.get("gamma_regime") or "") or None,
        structure_fields={"historical_expectancy": row.get("historical_expectancy")},
        quote_quality=str(quote_quality), stop_loss_multiple=sl_multiple,
        thesis="Historical replay candidate adapted for downstream risk-quality attribution.",
    )
    context = RegimeContext(
        regime_label=str(row.get("regime_label") or "") or None,
        gamma_regime=str(row.get("gamma_regime") or "") or None,
        corridor_valid=row.get("corridor_valid") if isinstance(row.get("corridor_valid"), bool) else None,
        wds_tier=int(_float(row.get("wds_tier"), 0.0) or 0) or None,
        dominant_wing=str(row.get("dominant_wing_side") or "") or None,
        spot=_float(row.get("spot")), maxvol=_float(row.get("maxvol")),
        primary_gamma=_float(row.get("primary_gamma")), secondary_gamma=_float(row.get("secondary_gamma")),
        quote_quality=str(quote_quality),
    )
    evaluated = evaluate_candidates([candidate], regime_contexts={candidate.candidate_id: context}).ranked[0]
    risk = evaluated.risk_quality
    return {
        "archetype": candidate.archetype.value,
        "credit_pct_of_width": candidate.credit_pct_of_width,
        "max_risk_dollars": candidate.max_loss,
        "risk_reward": candidate.risk_reward,
        "stop_loss_dollar_risk": risk.stop_loss_dollar_risk,
        "credit_to_stop_risk": risk.credit_to_stop_risk,
        "eod_exception_candidate": risk.eod_exception_candidate,
        "risk_quality_label": risk.label.value,
        "risk_quality_status": risk.status.value,
        "risk_quality_reason_codes": "; ".join(risk.reason_codes),
        "regime_compatibility_label": evaluated.regime_compatibility.label.value,
        "regime_compatibility_reason_codes": "; ".join(evaluated.regime_compatibility.reason_codes),
        "risk_quality_score": evaluated.quality_score,
    }
