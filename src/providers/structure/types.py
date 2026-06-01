"""Typed snapshot returned by any StructureProvider.

After Phase 1.5, a StructureSnapshot carries STRUCTURE CONTEXT ONLY — no
per-strike bid/ask/mid. Quote data lives on the QuoteProvider's
`OptionChainSnapshot`. Strategies receive both objects.

Production source: `/api/v1/market/snapshot` + `/api/v1/exposure/series`
(see `docs/reference_notes.md`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ExposureContext:
    """Aggregated context from /api/v1/market/exposures.

    `put_ceiling_*` and `call_floor_*` are the Vertical-Wing levels: the
    strike at which put / call volume crosses the named volume threshold.
    These are STRUCTURE-derived levels — the StructureProvider knows the
    chain internally (in production, it's a separate ZS-side computation).
    Strategy modules consult these instead of re-deriving from quote data.
    """
    total_gex_bn:    float | None = None
    total_vex_bn:    float | None = None
    gamma_flip:      float | None = None
    call_wall:       float | None = None
    put_wall:        float | None = None
    maxvol:          float | None = None  # strike with max combined volume
    gamma_regime:    str   | None = None  # "positive" | "negative" | None
    da_gex_signed:   float | None = None
    # Vertical-Wing levels
    put_ceiling_2k:  float | None = None
    put_ceiling_5k:  float | None = None
    call_floor_2k:   float | None = None
    call_floor_5k:   float | None = None
    # DDOI pin (open-interest concentration level)
    ddoi_pin:        float | None = None


@dataclass(frozen=True)
class StructureSnapshot:
    """Read-only structure context — NOT a quote container.

    For bid/ask/mid/volume at a strike, see `OptionChainSnapshot` from the
    QuoteProvider. Strategies take BOTH.

    `spot` here is the spot value AT THE TIME this structure context was
    computed (e.g. when MaxVol was last recalculated). The current quote
    spot may differ; UI should display the more recent one.
    """
    symbol:    str
    spot:      float
    quote_ts:  datetime           # structure-side timestamp
    exposures: ExposureContext = field(default_factory=ExposureContext)
    expiry:    str | None = None  # "YYYY-MM-DD"
    dte:       int | None = None
    source:    str = "unknown"    # provider name, e.g. "stub" or "zerosigma_api"
    raw:       dict[str, Any] = field(default_factory=dict)
