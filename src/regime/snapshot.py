"""Conservative regime classification from fields already present in ZeroSigma."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date as date_type
from datetime import datetime
from typing import Any

from src.regime.daily_path import DaGexPathState, classify_daily_path
from src.regime.opex import classify_opex_context
from src.regime.types import RegimeLabel, RegimeSnapshot

NEAR_LEVEL_POINTS = 5.0
MUTED_CORRIDOR_RANGE_FRACTION = 0.25
EXPANDING_RANGE_MULTIPLE = 1.25


def _value(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _relation(spot: float | None, level: float | None) -> str | None:
    if spot is None or level is None:
        return None
    difference = spot - level
    if abs(difference) <= NEAR_LEVEL_POINTS:
        return "near"
    return "above" if difference > 0 else "below"


def _wds(w1_volume: float | None, w2_volume: float | None) -> float | None:
    if w1_volume is None or w1_volume <= 0 or w2_volume is None:
        return None
    return round(1.0 - (w2_volume / w1_volume), 4)


def _wds_tier(value: float | None) -> int | None:
    if value is None:
        return None
    if value >= 0.75:
        return 1
    if value >= 0.50:
        return 2
    if value >= 0.30:
        return 3
    return 4


def _realized_range(history: Sequence[float] | None) -> float | None:
    values = [_number(value) for value in (history or ())]
    usable = [value for value in values if value is not None]
    if len(usable) < 2:
        return None
    return round(max(usable) - min(usable), 4)


def _timestamp(value: Any, structure: Any) -> str:
    resolved = value if value is not None else _value(structure, "quote_ts")
    if isinstance(resolved, datetime):
        return resolved.isoformat()
    return str(resolved or "unknown")


def build_regime_snapshot(
    structure: Any,
    *,
    timestamp: datetime | str | None = None,
    spot: float | None = None,
    quote_quality_status: str | None = None,
    spot_history: Sequence[float] | None = None,
    previous: RegimeSnapshot | None = None,
    da_gex_path: DaGexPathState | None = None,
) -> RegimeSnapshot:
    """Build one pure snapshot without fetching or synthesizing external data."""
    exposures = _value(structure, "exposures") or structure
    symbol = str(_value(structure, "symbol") or _value(exposures, "symbol") or "")
    current_spot = _number(spot if spot is not None else _value(structure, "spot"))
    gamma_regime = str(_value(exposures, "gamma_regime") or "").lower() or None
    da_gex = _number(_value(exposures, "da_gex_signed"))
    if gamma_regime not in {"positive", "negative"} and da_gex is not None:
        gamma_regime = "positive" if da_gex > 0 else "negative" if da_gex < 0 else None

    gamma_flip = _number(_value(exposures, "gamma_flip"))
    distance_to_flip = (
        round(current_spot - gamma_flip, 4)
        if current_spot is not None and gamma_flip is not None
        else None
    )
    primary = _number(_value(exposures, "gamma_primary"))
    secondary = _number(_value(exposures, "gamma_secondary"))
    reasons: list[str] = []
    if primary is None:
        levels = [
            _number(_value(exposures, "call_wall")),
            _number(_value(exposures, "put_wall")),
            gamma_flip,
        ]
        available = list(dict.fromkeys(level for level in levels if level is not None))
        if current_spot is not None:
            available.sort(key=lambda level: abs(level - current_spot))
        if available:
            primary = available[0]
            secondary = secondary or (available[1] if len(available) > 1 else None)
            reasons.append("primary_gamma_derived_from_available_levels")

    call_2k = _number(_value(exposures, "call_floor_2k"))
    call_5k = _number(_value(exposures, "call_floor_5k"))
    call_10k = _number(_value(exposures, "call_floor_10k"))
    put_2k = _number(_value(exposures, "put_ceiling_2k"))
    put_5k = _number(_value(exposures, "put_ceiling_5k"))
    put_10k = _number(_value(exposures, "put_ceiling_10k"))
    corridor = None
    if current_spot is not None and call_10k is not None and put_10k is not None:
        corridor = call_10k < current_spot < put_10k

    call_wds = _wds(
        _number(_value(exposures, "call_floor_10k_volume")),
        _number(_value(exposures, "call_floor_10k_w2_volume")),
    )
    put_wds = _wds(
        _number(_value(exposures, "put_ceiling_10k_volume")),
        _number(_value(exposures, "put_ceiling_10k_w2_volume")),
    )
    dominant_side = None
    dominant_wds = None
    if call_wds is not None or put_wds is not None:
        if put_wds is None or (call_wds is not None and call_wds >= put_wds):
            dominant_side, dominant_wds = "CALL", call_wds
        else:
            dominant_side, dominant_wds = "PUT", put_wds

    maxvol = _number(_value(exposures, "maxvol"))
    maxvol_migration = (
        round(maxvol - previous.maxvol_strike, 4)
        if previous and maxvol is not None and previous.maxvol_strike is not None
        else None
    )
    daily_path = classify_daily_path(da_gex_path)
    timestamp_text = _timestamp(timestamp, structure)
    try:
        context_date = datetime.fromisoformat(timestamp_text).date()
    except ValueError:
        try:
            context_date = date_type.fromisoformat(timestamp_text[:10])
        except ValueError:
            context_date = None
    opex = classify_opex_context(context_date) if context_date is not None else None
    realized_range = _realized_range(spot_history)
    quote_status = str(quote_quality_status or "unknown").lower()
    unusable_quote = quote_status in {"unusable", "invalid", "rejected", "stale"}
    gamma_changed = bool(
        previous
        and previous.gamma_regime in {"positive", "negative"}
        and gamma_regime in {"positive", "negative"}
        and previous.gamma_regime != gamma_regime
    )
    crossed_flip = bool(
        previous
        and previous.distance_to_gamma_flip is not None
        and distance_to_flip is not None
        and previous.distance_to_gamma_flip * distance_to_flip < 0
    )
    near_flip = distance_to_flip is not None and abs(distance_to_flip) <= NEAR_LEVEL_POINTS
    wing_breach = bool(
        current_spot is not None
        and (
            (call_10k is not None and current_spot <= call_10k)
            or (put_10k is not None and current_spot >= put_10k)
        )
    )
    range_expanding = bool(
        previous
        and realized_range is not None
        and previous.realized_range_so_far not in {None, 0}
        and realized_range >= previous.realized_range_so_far * EXPANDING_RANGE_MULTIPLE
    )
    corridor_width = (
        put_10k - call_10k
        if call_10k is not None and put_10k is not None and put_10k > call_10k
        else None
    )
    range_muted = bool(
        corridor is True
        and realized_range is not None
        and corridor_width is not None
        and realized_range <= corridor_width * MUTED_CORRIDOR_RANGE_FRACTION
    )

    if unusable_quote:
        label = RegimeLabel.NO_EDGE
        reasons.append("quote_quality_unusable")
    elif current_spot is None:
        label = RegimeLabel.UNKNOWN
        reasons.append("spot_unavailable")
    elif gamma_regime is None and gamma_flip is None and primary is None:
        label = RegimeLabel.UNKNOWN
        reasons.append("gamma_structure_too_sparse")
    elif gamma_changed or crossed_flip or near_flip:
        label = RegimeLabel.TRANSITION
        if gamma_changed:
            reasons.append("gamma_sign_changed")
        if crossed_flip:
            reasons.append("spot_crossed_gamma_flip")
        if near_flip:
            reasons.append("spot_near_gamma_flip")
    elif gamma_regime == "negative" and (wing_breach or range_expanding):
        label = RegimeLabel.ACCELERATION
        reasons.append("negative_gamma_with_structure_breach_or_expansion")
        if wing_breach:
            reasons.append("wing_structure_breached")
        if range_expanding:
            reasons.append("realized_range_expanding")
    elif range_muted:
        label = RegimeLabel.COMPRESSION
        reasons.append("muted_range_inside_active_corridor")
    elif gamma_regime == "positive" and corridor is True:
        label = RegimeLabel.ABSORPTION
        reasons.append("positive_gamma_inside_active_corridor")
    elif corridor is None:
        label = RegimeLabel.NO_EDGE
        reasons.append("key_wing_structure_missing")
    else:
        label = RegimeLabel.NO_EDGE
        reasons.append("no_conservative_regime_edge")

    available_count = sum(
        value is not None
        for value in (
            current_spot,
            gamma_regime,
            gamma_flip,
            primary,
            call_10k,
            put_10k,
            maxvol,
            dominant_wds,
        )
    )
    confidence = round(available_count / 8.0, 3)
    if unusable_quote:
        confidence = min(confidence, 0.25)
    quality = "HIGH" if confidence >= 0.75 else "MEDIUM" if confidence >= 0.50 else "LOW"
    greek_available = tuple(_value(exposures, "greek_api_available_fields") or ())
    greek_missing = tuple(_value(exposures, "greek_api_missing_fields") or ())
    deferred_fields = tuple(
        field for field in (
            "charm", "vanna", "theta_adjusted_charm", "vix", "iv_surface",
            "dom", "news", "per_strike_vex_skew",
        )
        if {
            "charm": "charm",
            "vanna": "vanna",
            "per_strike_vex_skew": "vex_skew",
        }.get(field, field) not in greek_available
    )
    if deferred_fields:
        reasons.append("deferred_inputs_unavailable")
    summary = (
        f"{symbol or 'Symbol'} is {label.value.replace('_', ' ').title()}: "
        f"gamma is {gamma_regime or 'unavailable'}, corridor is "
        f"{'active' if corridor is True else 'inactive' if corridor is False else 'unavailable'}, "
        f"and quote quality is {quote_status}."
    )

    return RegimeSnapshot(
        timestamp=timestamp_text,
        symbol=symbol,
        spot=current_spot,
        gamma_regime=gamma_regime,
        da_gex_signed=da_gex,
        gamma_flip=gamma_flip,
        distance_to_gamma_flip=distance_to_flip,
        primary_gamma_level=primary,
        secondary_gamma_level=secondary,
        spot_vs_primary=_relation(current_spot, primary),
        spot_vs_secondary=_relation(current_spot, secondary),
        corridor_valid=corridor,
        call_wing_2k=call_2k,
        call_wing_5k=call_5k,
        call_wing_10k=call_10k,
        put_wing_2k=put_2k,
        put_wing_5k=put_5k,
        put_wing_10k=put_10k,
        wds_value=dominant_wds if corridor is True else None,
        wds_tier=_wds_tier(dominant_wds) if corridor is True else None,
        dominant_wing_side=dominant_side if corridor is True else None,
        maxvol_strike=maxvol,
        maxvol_migration=maxvol_migration,
        total_gex_bn=_number(_value(exposures, "total_gex_bn")),
        total_vex_bn=_number(_value(exposures, "total_vex_bn")),
        quote_quality_status=quote_status,
        realized_range_so_far=realized_range,
        final_regime_label=label,
        confidence=confidence,
        quality_label=quality,
        reason_codes=tuple(dict.fromkeys(reasons)),
        plain_english_summary=summary,
        deferred_fields=deferred_fields,
        total_raw_gex_bn=_number(_value(exposures, "total_raw_gex_bn")),
        total_dex_bn=_number(_value(exposures, "total_dex_bn")),
        total_cex_bn=_number(_value(exposures, "total_cex_bn")),
        greek_api_available_fields=greek_available,
        greek_api_missing_fields=greek_missing,
        greek_api_source_endpoint=_value(exposures, "greek_api_source_endpoint"),
        greek_api_units=dict(_value(exposures, "greek_api_units") or {}),
        greek_api_unavailable_reasons=dict(
            _value(exposures, "greek_api_unavailable_reasons") or {}
        ),
        daily_regime_code=daily_path.code,
        daily_regime_label=daily_path.label,
        daily_regime_reason_codes=daily_path.reason_codes,
        da_gex_path_observations=daily_path.observation_count,
        da_gex_sign_changes=daily_path.sign_changes,
        da_gex_path_summary=daily_path.summary,
        context_regime_code=opex.code if opex else "R_UNKNOWN",
        context_regime_label=opex.label if opex else "Unknown OpEx Context",
        context_regime_reason_codes=opex.reason_codes if opex else ("timestamp_unavailable",),
        opex_context=opex.opex_context if opex else "unknown",
        days_to_opex=opex.days_to_opex if opex else None,
        expiration_context=opex.expiration_context if opex else "UNKNOWN",
    )
