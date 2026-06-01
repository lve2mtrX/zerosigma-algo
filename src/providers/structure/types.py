"""Typed snapshot returned by any StructureProvider.

Strategies consume `StructureSnapshot` and nothing else from the structure
layer. Adding a new structure source means populating this shape; strategies
don't need to know which provider produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ChainRow:
    """One strike, both sides."""
    strike: float
    # call side
    c_bid: float | None
    c_ask: float | None
    c_mid: float | None
    c_iv: float | None
    c_delta: float | None
    c_gamma: float | None
    c_oi: float | None
    c_volume: float | None
    # put side
    p_bid: float | None
    p_ask: float | None
    p_mid: float | None
    p_iv: float | None
    p_delta: float | None
    p_gamma: float | None
    p_oi: float | None
    p_volume: float | None
    # exposures (optional — populated if /exposure/series merged in)
    c_da_gex_1pct: float | None = None
    p_da_gex_1pct: float | None = None
    c_dex_1pct:    float | None = None
    p_dex_1pct:    float | None = None
    c_vex_1vol:    float | None = None
    p_vex_1vol:    float | None = None


@dataclass(frozen=True)
class ExposureContext:
    """Aggregated context from /api/v1/market/exposures.

    `put_ceiling_*` and `call_floor_*` are the Vertical-Wing levels: the
    strike at which put / call volume crosses the named volume threshold.
    Strategy modules can either consult these directly OR re-derive them
    from the chain (the stub provider populates both for convenience).
    """
    total_gex_bn:  float | None = None
    total_vex_bn:  float | None = None
    gamma_flip:    float | None = None
    call_wall:     float | None = None
    put_wall:      float | None = None
    maxvol:        float | None = None   # strike with max combined volume
    gamma_regime:  str   | None = None   # "positive" | "negative" | None
    da_gex_signed: float | None = None
    # Vertical-Wing levels (highest put-volume strike / lowest call-volume strike
    # at each named threshold). All optional — None means "no qualifying strike".
    put_ceiling_2k:  float | None = None
    put_ceiling_5k:  float | None = None
    call_floor_2k:   float | None = None
    call_floor_5k:   float | None = None
    # DDOI pin (open-interest concentration level) and DA-GEX regime hint.
    ddoi_pin: float | None = None


@dataclass(frozen=True)
class StructureSnapshot:
    """Read-only snapshot fed into strategies."""
    symbol: str
    spot: float
    quote_ts: datetime
    chain: list[ChainRow]
    exposures: ExposureContext = field(default_factory=ExposureContext)
    expiry: str | None = None          # YYYY-MM-DD
    dte: int | None = None
    source: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)  # raw payload, for debugging
