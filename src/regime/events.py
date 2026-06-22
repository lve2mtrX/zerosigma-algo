"""Reason-coded regime-change detection with deterministic debounce."""

from __future__ import annotations

from datetime import datetime

from src.regime.types import (
    RegimeAction,
    RegimeChangeEvent,
    RegimeLabel,
    RegimeSeverity,
    RegimeSnapshot,
)

MATERIAL_MAXVOL_MIGRATION_POINTS = 5.0


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class RegimeEventDebouncer:
    """Emit the first meaningful change and suppress repeated signatures."""

    def __init__(self, cooldown_seconds: int = 300) -> None:
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self._last_emitted: dict[str, datetime | None] = {}

    def evaluate(
        self,
        previous: RegimeSnapshot | None,
        current: RegimeSnapshot,
        *,
        affects_open_positions: bool = False,
    ) -> RegimeChangeEvent | None:
        if previous is None or previous.symbol != current.symbol:
            return None

        triggers: list[str] = []
        reasons: list[str] = []
        if previous.final_regime_label != current.final_regime_label:
            triggers.append("regime_label_changed")
            reasons.append("regime_label_changed")
        if previous.gamma_regime != current.gamma_regime:
            triggers.append("gamma_sign_changed")
            reasons.append("gamma_sign_changed")
        if (
            previous.distance_to_gamma_flip is not None
            and current.distance_to_gamma_flip is not None
            and previous.distance_to_gamma_flip * current.distance_to_gamma_flip < 0
        ):
            triggers.append("gamma_flip_crossed")
            reasons.append("spot_crossed_gamma_flip")
        if previous.corridor_valid is True and current.corridor_valid is False:
            triggers.append("corridor_broken")
            reasons.append("active_corridor_broken")
        if "wing_structure_breached" in current.reason_codes:
            triggers.append("wing_breached")
            reasons.append("wing_structure_breached")
        if (
            current.maxvol_migration is not None
            and abs(current.maxvol_migration) >= MATERIAL_MAXVOL_MIGRATION_POINTS
        ):
            triggers.append("maxvol_migrated")
            reasons.append("maxvol_migrated_materially")
        if previous.daily_regime_code != current.daily_regime_code:
            triggers.append("daily_da_gex_regime_changed")
            reasons.append("daily_da_gex_regime_changed")
            if current.daily_regime_code == "R3_WHIPSAW":
                reasons.append("da_gex_path_flipped_or_whipsawed")
        if previous.context_regime_code != current.context_regime_code:
            triggers.append("opex_context_regime_changed")
            reasons.append("opex_context_regime_changed")
        previous_available = set(previous.greek_api_available_fields)
        current_available = set(current.greek_api_available_fields)
        newly_missing = sorted(previous_available - current_available)
        newly_available = sorted(current_available - previous_available)
        if newly_missing:
            triggers.append("greek_data_degraded")
            reasons.append("greek_api_field_disappeared")
        if newly_available:
            triggers.append("greek_data_recovered")
            reasons.append("greek_api_field_appeared")
        if not triggers:
            return None

        trigger = "+".join(dict.fromkeys(triggers))
        signature = (
            f"{current.symbol}|{previous.final_regime_label.value}|"
            f"{current.final_regime_label.value}|{trigger}"
        )
        now = _parse_timestamp(current.timestamp)
        last = self._last_emitted.get(signature)
        if last is not None and now is not None:
            if (now - last).total_seconds() < self.cooldown_seconds:
                return None
        elif signature in self._last_emitted:
            return None
        self._last_emitted[signature] = now

        if current.final_regime_label == RegimeLabel.ACCELERATION:
            severity = RegimeSeverity.CRITICAL
            action = RegimeAction.EXIT if affects_open_positions else RegimeAction.BLOCK_NEW_TRADES
        elif (
            current.final_regime_label in {RegimeLabel.TRANSITION, RegimeLabel.NO_EDGE}
            or "greek_data_degraded" in triggers
            or current.daily_regime_code == "R3_WHIPSAW"
        ):
            severity = RegimeSeverity.WARN
            action = RegimeAction.WATCH if affects_open_positions else RegimeAction.BLOCK_NEW_TRADES
        else:
            severity = RegimeSeverity.INFO
            action = RegimeAction.HOLD

        changes: list[str] = []
        if previous.final_regime_label != current.final_regime_label:
            changes.append(
                f"core regime {previous.final_regime_label.value} to "
                f"{current.final_regime_label.value}"
            )
        if previous.daily_regime_code != current.daily_regime_code:
            changes.append(
                f"daily path {previous.daily_regime_code} to {current.daily_regime_code}"
            )
        if previous.context_regime_code != current.context_regime_code:
            changes.append(
                f"OpEx context {previous.context_regime_code} to {current.context_regime_code}"
            )
        if newly_missing:
            changes.append(f"Greek fields disappeared: {', '.join(newly_missing)}")
        if newly_available:
            changes.append(f"Greek fields appeared: {', '.join(newly_available)}")
        if "maxvol_migrated" in triggers:
            changes.append(f"MaxVol moved {current.maxvol_migration:+.2f} points")
        change_text = "; ".join(changes) or trigger.replace("_", " ")
        alert = (
            f"{current.symbol} structure alert: {change_text}. Suggested action: "
            f"{action.value.replace('_', ' ').title()}."
        )
        return RegimeChangeEvent(
            timestamp=current.timestamp,
            symbol=current.symbol,
            old_regime=previous.final_regime_label,
            new_regime=current.final_regime_label,
            trigger=trigger,
            levels_involved={
                "spot": current.spot,
                "gamma_flip": current.gamma_flip,
                "call_wing_10k": current.call_wing_10k,
                "put_wing_10k": current.put_wing_10k,
                "maxvol": current.maxvol_strike,
                "maxvol_migration": current.maxvol_migration,
                "old_daily_regime": previous.daily_regime_code,
                "daily_regime": current.daily_regime_code,
                "old_context_regime": previous.context_regime_code,
                "context_regime": current.context_regime_code,
                "newly_missing_greek_fields": newly_missing,
                "newly_available_greek_fields": newly_available,
            },
            severity=severity,
            suggested_action=action,
            affects_open_positions=affects_open_positions,
            reason_codes=tuple(dict.fromkeys(reasons)),
            plain_english_alert=alert,
        )
