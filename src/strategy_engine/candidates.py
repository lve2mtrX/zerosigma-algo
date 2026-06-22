"""Deterministic payoff construction for supported research archetypes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from src.strategy_engine.types import (
    DirectionalBias,
    EntryPriceType,
    LegAction,
    StrategyArchetype,
    StrategyCandidate,
    StrategyLeg,
)

OPTION_MULTIPLIER = 100.0


def _candidate_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return "research_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def build_credit_spread(
    *,
    timestamp: datetime,
    symbol: str,
    dte: int,
    expiry: str,
    archetype: StrategyArchetype,
    short_leg: StrategyLeg,
    long_leg: StrategyLeg,
    credit: float,
    contracts: int = 1,
    expected_entry_window: str | None = None,
    time_to_close_minutes: int | None = None,
    distance_to_short_strike: float | None = None,
    regime_label: str | None = None,
    structure_fields: dict[str, Any] | None = None,
    quote_quality: str | None = None,
    thesis: str = "",
    stop_loss_debit: float | None = None,
    stop_loss_multiple: float | None = None,
) -> StrategyCandidate:
    if archetype not in {
        StrategyArchetype.CALL_CREDIT_SPREAD,
        StrategyArchetype.PUT_CREDIT_SPREAD,
    }:
        raise ValueError("credit spread factory requires a credit-spread archetype")
    if short_leg.action != LegAction.SELL or long_leg.action != LegAction.BUY:
        raise ValueError("credit spread requires SELL short leg and BUY long leg")
    qty = max(1, int(contracts))
    width = abs(float(long_leg.strike) - float(short_leg.strike))
    credit = max(0.0, float(credit))
    max_profit = credit * OPTION_MULTIPLIER * qty
    max_loss = max(0.0, width - credit) * OPTION_MULTIPLIER * qty
    risk_reward = max_profit / max_loss if max_loss > 0 else None
    credit_pct = credit / width if width > 0 else None
    bias = (
        DirectionalBias.BEARISH
        if archetype == StrategyArchetype.CALL_CREDIT_SPREAD
        else DirectionalBias.BULLISH
    )
    payload = {
        "timestamp": timestamp.isoformat(), "symbol": symbol, "expiry": expiry,
        "archetype": archetype, "short": short_leg.strike, "long": long_leg.strike,
        "credit": credit, "contracts": qty,
    }
    return StrategyCandidate(
        candidate_id=_candidate_id(payload), timestamp=timestamp, symbol=symbol,
        dte=int(dte), expiry=expiry, archetype=archetype, directional_bias=bias,
        legs=(short_leg, long_leg), entry_price_type=EntryPriceType.CREDIT,
        entry_credit=credit, width=width, max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2), risk_reward=round(risk_reward, 4) if risk_reward is not None else None,
        credit_pct_of_width=round(credit_pct, 4) if credit_pct is not None else None,
        contracts=qty, expected_entry_window=expected_entry_window,
        time_to_close_minutes=time_to_close_minutes,
        distance_to_short_strike=distance_to_short_strike, regime_label=regime_label,
        structure_fields=dict(structure_fields or {}), quote_quality=quote_quality,
        thesis=thesis, stop_loss_debit=stop_loss_debit,
        stop_loss_multiple=stop_loss_multiple,
    )


def build_long_option(
    *,
    timestamp: datetime,
    symbol: str,
    dte: int,
    expiry: str,
    archetype: StrategyArchetype,
    leg: StrategyLeg,
    debit: float,
    contracts: int = 1,
    expected_entry_window: str | None = None,
    time_to_close_minutes: int | None = None,
    distance_to_target_level: float | None = None,
    regime_label: str | None = None,
    structure_fields: dict[str, Any] | None = None,
    quote_quality: str | None = None,
    thesis: str = "",
    target_move_required: float | None = None,
    invalidation_level: float | None = None,
    minimum_target_multiple: float | None = None,
) -> StrategyCandidate:
    if archetype not in {StrategyArchetype.LONG_CALL, StrategyArchetype.LONG_PUT}:
        raise ValueError("long-option factory requires LONG_CALL or LONG_PUT")
    if leg.action != LegAction.BUY:
        raise ValueError("long premium requires a BUY leg")
    qty = max(1, int(contracts))
    debit = max(0.0, float(debit))
    at_risk = debit * OPTION_MULTIPLIER * qty
    bias = DirectionalBias.BULLISH if archetype == StrategyArchetype.LONG_CALL else DirectionalBias.BEARISH
    payload = {
        "timestamp": timestamp.isoformat(), "symbol": symbol, "expiry": expiry,
        "archetype": archetype, "strike": leg.strike, "debit": debit, "contracts": qty,
    }
    return StrategyCandidate(
        candidate_id=_candidate_id(payload), timestamp=timestamp, symbol=symbol,
        dte=int(dte), expiry=expiry, archetype=archetype, directional_bias=bias,
        legs=(leg,), entry_price_type=EntryPriceType.DEBIT, entry_debit=debit,
        max_loss=round(at_risk, 2), debit_at_risk=round(at_risk, 2),
        risk_reward=minimum_target_multiple, contracts=qty,
        expected_entry_window=expected_entry_window,
        time_to_close_minutes=time_to_close_minutes,
        distance_to_target_level=distance_to_target_level, regime_label=regime_label,
        structure_fields=dict(structure_fields or {}), quote_quality=quote_quality,
        thesis=thesis, target_move_required=target_move_required,
        invalidation_level=invalidation_level,
        minimum_target_multiple=minimum_target_multiple,
    )
