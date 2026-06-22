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
        if current.maxvol_migration not in {None, 0.0}:
            triggers.append("maxvol_migrated")
            reasons.append("maxvol_migrated")
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
        elif current.final_regime_label in {RegimeLabel.TRANSITION, RegimeLabel.NO_EDGE}:
            severity = RegimeSeverity.WARN
            action = RegimeAction.WATCH if affects_open_positions else RegimeAction.BLOCK_NEW_TRADES
        else:
            severity = RegimeSeverity.INFO
            action = RegimeAction.HOLD

        alert = (
            f"{current.symbol} regime changed from "
            f"{previous.final_regime_label.value.replace('_', ' ').title()} to "
            f"{current.final_regime_label.value.replace('_', ' ').title()} "
            f"because {trigger.replace('_', ' ')}. Suggested action: "
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
            },
            severity=severity,
            suggested_action=action,
            affects_open_positions=affects_open_positions,
            reason_codes=tuple(dict.fromkeys(reasons)),
            plain_english_alert=alert,
        )
