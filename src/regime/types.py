"""Serializable regime-domain types with no provider or UI dependencies."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class RegimeLabel(StrEnum):
    ABSORPTION = "ABSORPTION"
    ACCELERATION = "ACCELERATION"
    TRANSITION = "TRANSITION"
    COMPRESSION = "COMPRESSION"
    NO_EDGE = "NO_EDGE"
    UNKNOWN = "UNKNOWN"


class RegimeSeverity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class RegimeAction(StrEnum):
    HOLD = "HOLD"
    WATCH = "WATCH"
    EXIT = "EXIT"
    BLOCK_NEW_TRADES = "BLOCK_NEW_TRADES"


DEFERRED_REGIME_FIELDS = (
    "charm",
    "vanna",
    "theta_adjusted_charm",
    "vix",
    "iv_surface",
    "dom",
    "news",
    "per_strike_vex_skew",
)


@dataclass(frozen=True)
class RegimeSnapshot:
    timestamp: str
    symbol: str
    spot: float | None
    gamma_regime: str | None
    da_gex_signed: float | None
    gamma_flip: float | None
    distance_to_gamma_flip: float | None
    primary_gamma_level: float | None
    secondary_gamma_level: float | None
    spot_vs_primary: str | None
    spot_vs_secondary: str | None
    corridor_valid: bool | None
    call_wing_2k: float | None
    call_wing_5k: float | None
    call_wing_10k: float | None
    put_wing_2k: float | None
    put_wing_5k: float | None
    put_wing_10k: float | None
    wds_value: float | None
    wds_tier: int | None
    dominant_wing_side: str | None
    maxvol_strike: float | None
    maxvol_migration: float | None
    total_gex_bn: float | None
    total_vex_bn: float | None
    quote_quality_status: str | None
    realized_range_so_far: float | None
    final_regime_label: RegimeLabel
    confidence: float
    quality_label: str
    reason_codes: tuple[str, ...] = ()
    plain_english_summary: str = ""
    deferred_fields: tuple[str, ...] = field(default=DEFERRED_REGIME_FIELDS)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["final_regime_label"] = self.final_regime_label.value
        row["reason_codes"] = list(self.reason_codes)
        row["deferred_fields"] = list(self.deferred_fields)
        return row

    def to_flat_dict(self) -> dict[str, Any]:
        return {
            "regime_snapshot_json": json.dumps(self.to_dict(), sort_keys=True),
            "regime_label": self.final_regime_label.value,
            "regime_confidence": self.confidence,
            "regime_quality_label": self.quality_label,
            "regime_reason_codes": "; ".join(self.reason_codes),
            "regime_summary": self.plain_english_summary,
            "spot": self.spot,
            "gamma_regime": self.gamma_regime,
            "da_gex_signed": self.da_gex_signed,
            "gamma_flip": self.gamma_flip,
            "distance_to_gamma_flip": self.distance_to_gamma_flip,
            "primary_gamma": self.primary_gamma_level,
            "secondary_gamma": self.secondary_gamma_level,
            "corridor_valid": self.corridor_valid,
            "call_wing_10k": self.call_wing_10k,
            "put_wing_10k": self.put_wing_10k,
            "active_wds": self.wds_value,
            "wds_tier": self.wds_tier,
            "dominant_wing_side": self.dominant_wing_side,
            "maxvol": self.maxvol_strike,
            "maxvol_migration": self.maxvol_migration,
            "total_gex_bn": self.total_gex_bn,
            "total_vex_bn": self.total_vex_bn,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> RegimeSnapshot:
        values = dict(row)
        values["final_regime_label"] = RegimeLabel(
            values.get("final_regime_label", RegimeLabel.UNKNOWN.value)
        )
        values["reason_codes"] = tuple(values.get("reason_codes") or ())
        values["deferred_fields"] = tuple(
            values.get("deferred_fields") or DEFERRED_REGIME_FIELDS
        )
        return cls(**values)


@dataclass(frozen=True)
class RegimeChangeEvent:
    timestamp: str
    symbol: str
    old_regime: RegimeLabel
    new_regime: RegimeLabel
    trigger: str
    levels_involved: dict[str, float | None]
    severity: RegimeSeverity
    suggested_action: RegimeAction
    affects_open_positions: bool
    reason_codes: tuple[str, ...]
    plain_english_alert: str

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["old_regime"] = self.old_regime.value
        row["new_regime"] = self.new_regime.value
        row["severity"] = self.severity.value
        row["suggested_action"] = self.suggested_action.value
        row["reason_codes"] = list(self.reason_codes)
        return row
