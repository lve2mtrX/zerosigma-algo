"""Lightweight compatibility bridge over regime fields already available today."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.strategy_engine.types import StrategyArchetype, StrategyCandidate


class CompatibilityLabel(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RegimeContext:
    regime_label: str | None = None
    gamma_regime: str | None = None
    corridor_valid: bool | None = None
    wds_tier: int | None = None
    dominant_wing: str | None = None
    spot: float | None = None
    call_structure: float | None = None
    put_structure: float | None = None
    maxvol: float | None = None
    primary_gamma: float | None = None
    secondary_gamma: float | None = None
    quote_quality: str | None = None
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class RegimeCompatibility:
    label: CompatibilityLabel
    reason_codes: tuple[str, ...]
    explanation: str


def _regime_text(candidate: StrategyCandidate, context: RegimeContext) -> str:
    return " ".join(
        str(value or "").strip().lower().replace("-", "_")
        for value in (context.regime_label, candidate.regime_label, context.gamma_regime)
    )


def evaluate_regime_compatibility(
    candidate: StrategyCandidate,
    context: RegimeContext | None = None,
) -> RegimeCompatibility:
    context = context or RegimeContext(regime_label=candidate.regime_label)
    text = _regime_text(candidate, context)
    up = any(token in text for token in ("acceleration_up", "upside_acceleration", "breakout_up", "reclaim"))
    down = any(token in text for token in ("acceleration_down", "downside_acceleration", "breakdown", "breakout_down"))
    above_call = (
        context.spot is not None and context.call_structure is not None
        and context.spot > context.call_structure
    )
    below_put = (
        context.spot is not None and context.put_structure is not None
        and context.spot < context.put_structure
    )
    archetype = candidate.archetype
    if archetype == StrategyArchetype.CALL_CREDIT_SPREAD and (up or above_call):
        return RegimeCompatibility(
            CompatibilityLabel.INCOMPATIBLE,
            ("call_credit_hostile_upside_acceleration",),
            "Call Credit is incompatible with known upside acceleration or a break above call-side structure.",
        )
    if archetype == StrategyArchetype.PUT_CREDIT_SPREAD and (down or below_put):
        return RegimeCompatibility(
            CompatibilityLabel.INCOMPATIBLE,
            ("put_credit_hostile_downside_acceleration",),
            "Put Credit is incompatible with known downside acceleration or a break below put-side structure.",
        )
    if archetype == StrategyArchetype.LONG_CALL:
        if down:
            return RegimeCompatibility(CompatibilityLabel.INCOMPATIBLE, ("long_call_conflicts_with_downside_acceleration",), "Long Call conflicts with the known downside regime.")
        if up:
            return RegimeCompatibility(CompatibilityLabel.COMPATIBLE, ("long_call_supported_by_upside_acceleration",), "Long Call is supported by the known upside acceleration/reclaim regime.")
    if archetype == StrategyArchetype.LONG_PUT:
        if up:
            return RegimeCompatibility(CompatibilityLabel.INCOMPATIBLE, ("long_put_conflicts_with_upside_acceleration",), "Long Put conflicts with the known upside regime.")
        if down:
            return RegimeCompatibility(CompatibilityLabel.COMPATIBLE, ("long_put_supported_by_downside_acceleration",), "Long Put is supported by the known downside acceleration/breakdown regime.")
    if not text.strip() and context.spot is None:
        return RegimeCompatibility(CompatibilityLabel.UNKNOWN, ("regime_unavailable",), "Regime evidence is unavailable; compatibility is unknown.")
    return RegimeCompatibility(CompatibilityLabel.UNKNOWN, ("no_decisive_regime_signal",), "Available regime fields do not provide a decisive compatibility signal.")
