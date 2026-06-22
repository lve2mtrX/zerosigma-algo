"""Deterministic risk-quality gates for research strategy candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.strategy_engine.regime_compatibility import CompatibilityLabel, RegimeCompatibility
from src.strategy_engine.types import StrategyCandidate


class EvaluationStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    REJECT = "reject"


class RiskQualityLabel(StrEnum):
    GOOD = "GOOD"
    ACCEPTABLE = "ACCEPTABLE"
    TOO_CHEAP_FOR_RISK = "TOO_CHEAP_FOR_RISK"
    EOD_EXCEPTION_ONLY = "EOD_EXCEPTION_ONLY"
    REJECT = "REJECT"


@dataclass(frozen=True)
class RiskQualityConfig:
    min_standard_credit: float = 0.50
    low_credit_threshold: float = 0.50
    min_credit_pct_of_width: float = 0.10
    min_risk_reward: float = 0.10
    good_credit_pct_of_width: float = 0.25
    good_risk_reward: float = 0.30
    eod_max_minutes: int = 15
    eod_min_distance: float = 25.0
    max_long_quote_spread_pct: float = 0.25
    min_long_target_multiple: float = 1.5
    slippage_haircut: float = 0.05
    minimum_required_edge: float = 0.05


@dataclass(frozen=True)
class RiskQualityAssessment:
    status: EvaluationStatus
    label: RiskQualityLabel
    max_risk: float | None
    max_profit: float | None
    risk_reward: float | None
    expected_value: float | None
    quote_spread_width: float | None
    quote_quality_status: str
    slippage_haircut: float
    minimum_required_edge: float
    stop_loss_dollar_risk: float | None
    credit_to_stop_risk: float | None
    distance_bucket: str | None
    eod_exception_candidate: bool
    reason_codes: tuple[str, ...]
    explanation: str


def _distance_bucket(value: float | None) -> str | None:
    if value is None:
        return None
    value = abs(value)
    if value < 15:
        return "<15"
    if value < 25:
        return "15-24.99"
    if value < 40:
        return "25-39.99"
    return "40+"


def _quote_status(candidate: StrategyCandidate) -> str:
    raw = str(candidate.quote_quality or "unknown").strip().upper()
    if raw in {"GOOD", "ACCEPTABLE", "USABLE", "PASS", "PASSED"}:
        return "usable"
    if raw in {"POOR", "REJECT", "REJECTED", "INVALID", "CROSSED", "STALE"}:
        return "unusable"
    return "unknown"


def _credit_stop_risk(candidate: StrategyCandidate) -> tuple[float | None, float | None]:
    credit = candidate.entry_credit
    if credit is None:
        return None, None
    if candidate.stop_loss_debit is not None:
        loss_points = max(0.0, candidate.stop_loss_debit - credit)
    elif candidate.stop_loss_multiple is not None:
        loss_points = max(0.0, credit * candidate.stop_loss_multiple)
    else:
        return None, None
    theoretical_points = (candidate.max_loss or 0.0) / (100.0 * candidate.contracts)
    loss_points = min(loss_points, theoretical_points) if theoretical_points else loss_points
    dollars = loss_points * 100.0 * candidate.contracts
    ratio = (candidate.max_profit or 0.0) / dollars if dollars > 0 else None
    return round(dollars, 2), round(ratio, 4) if ratio is not None else None


def evaluate_risk_quality(
    candidate: StrategyCandidate,
    *,
    config: RiskQualityConfig | None = None,
    regime: RegimeCompatibility | None = None,
) -> RiskQualityAssessment:
    config = config or RiskQualityConfig()
    regime = regime or RegimeCompatibility(CompatibilityLabel.UNKNOWN, (), "Regime not evaluated.")
    quote_status = _quote_status(candidate)
    reasons: list[str] = []
    stop_risk, credit_to_stop = _credit_stop_risk(candidate)
    eod_candidate = False

    if regime.label == CompatibilityLabel.INCOMPATIBLE:
        reasons.extend(regime.reason_codes or ("regime_incompatible",))
    if candidate.is_credit_spread:
        credit = float(candidate.entry_credit or 0.0)
        credit_pct = float(candidate.credit_pct_of_width or 0.0)
        risk_reward = float(candidate.risk_reward or 0.0)
        eod_candidate = (
            candidate.time_to_close_minutes is not None
            and candidate.time_to_close_minutes <= config.eod_max_minutes
            and candidate.distance_to_short_strike is not None
            and candidate.distance_to_short_strike >= config.eod_min_distance
            and quote_status == "usable"
            and regime.label != CompatibilityLabel.INCOMPATIBLE
        )
        cheap = credit <= config.low_credit_threshold
        poor_pct = credit_pct < config.min_credit_pct_of_width
        poor_rr = risk_reward < config.min_risk_reward
        if cheap:
            reasons.append("credit_at_or_below_general_floor")
        if credit < config.min_standard_credit:
            reasons.append("credit_below_standard_minimum")
        if poor_pct:
            reasons.append("credit_pct_of_width_too_low")
        if poor_rr:
            reasons.append("max_risk_too_high_relative_to_reward")
        if quote_status == "unusable":
            reasons.append("quote_quality_unusable")
        if eod_candidate and (cheap or poor_pct or poor_rr):
            expectancy = candidate.structure_fields.get("historical_expectancy")
            if expectancy is None:
                reasons.append("eod_exception_expectancy_unproven")
            return RiskQualityAssessment(
                EvaluationStatus.WARN, RiskQualityLabel.EOD_EXCEPTION_ONLY,
                candidate.max_loss, candidate.max_profit, candidate.risk_reward, None,
                candidate.quote_spread_width, quote_status, config.slippage_haircut,
                config.minimum_required_edge, stop_risk, credit_to_stop,
                _distance_bucket(candidate.distance_to_short_strike), True,
                tuple(dict.fromkeys(reasons)),
                "The spread is too cheap for standard use and qualifies only as a strict research EOD exception.",
            )
        if cheap or credit < config.min_standard_credit or poor_pct or poor_rr or quote_status == "unusable" or regime.label == CompatibilityLabel.INCOMPATIBLE:
            label = RiskQualityLabel.TOO_CHEAP_FOR_RISK if cheap or poor_pct or poor_rr else RiskQualityLabel.REJECT
            return RiskQualityAssessment(
                EvaluationStatus.REJECT, label, candidate.max_loss, candidate.max_profit,
                candidate.risk_reward, None, candidate.quote_spread_width, quote_status,
                config.slippage_haircut, config.minimum_required_edge, stop_risk,
                credit_to_stop, _distance_bucket(candidate.distance_to_short_strike),
                eod_candidate, tuple(dict.fromkeys(reasons)),
                "Credit and defined stop/reward do not compensate for the spread's maximum risk.",
            )
        label = (
            RiskQualityLabel.GOOD
            if credit_pct >= config.good_credit_pct_of_width and risk_reward >= config.good_risk_reward
            else RiskQualityLabel.ACCEPTABLE
        )
        status = EvaluationStatus.PASS if quote_status == "usable" else EvaluationStatus.WARN
        if quote_status == "unknown":
            reasons.append("quote_quality_unknown")
        return RiskQualityAssessment(
            status, label, candidate.max_loss, candidate.max_profit, candidate.risk_reward,
            None, candidate.quote_spread_width, quote_status, config.slippage_haircut,
            config.minimum_required_edge, stop_risk, credit_to_stop,
            _distance_bucket(candidate.distance_to_short_strike), False,
            tuple(dict.fromkeys(reasons)),
            "Credit, spread width, maximum loss, and defined stop risk are proportionate enough for research evaluation.",
        )

    if candidate.is_long_premium:
        debit = float(candidate.entry_debit or 0.0)
        spread_pct = (
            candidate.quote_spread_width / debit
            if debit > 0 and candidate.quote_spread_width is not None else None
        )
        if debit <= 0:
            reasons.append("long_premium_debit_missing")
        if spread_pct is not None and spread_pct > config.max_long_quote_spread_pct:
            reasons.append("long_premium_quote_too_wide")
        if (candidate.minimum_target_multiple or 0.0) < config.min_long_target_multiple:
            reasons.append("long_premium_target_multiple_too_low")
        if candidate.invalidation_level is None:
            reasons.append("long_premium_invalidation_missing")
        if regime.label == CompatibilityLabel.INCOMPATIBLE:
            reasons.append("long_premium_regime_incompatible")
        rejected = any(code in reasons for code in (
            "long_premium_debit_missing", "long_premium_quote_too_wide",
            "long_premium_target_multiple_too_low", "long_premium_regime_incompatible",
        ))
        return RiskQualityAssessment(
            EvaluationStatus.REJECT if rejected else EvaluationStatus.WARN if reasons else EvaluationStatus.PASS,
            RiskQualityLabel.REJECT if rejected else RiskQualityLabel.ACCEPTABLE,
            candidate.max_loss, candidate.max_profit, candidate.risk_reward, None,
            candidate.quote_spread_width, quote_status, config.slippage_haircut,
            config.minimum_required_edge, candidate.debit_at_risk, None, None, False,
            tuple(dict.fromkeys(reasons)),
            "Long premium risk is the debit paid; quote width, target multiple, invalidation, and regime must justify that debit.",
        )

    return RiskQualityAssessment(
        EvaluationStatus.WARN, RiskQualityLabel.ACCEPTABLE, candidate.max_loss,
        candidate.max_profit, candidate.risk_reward, None, candidate.quote_spread_width,
        quote_status, config.slippage_haircut, config.minimum_required_edge, None, None,
        None, False, ("archetype_payoff_deferred",),
        "This archetype is modeled as a placeholder; detailed payoff evaluation is deferred.",
    )
