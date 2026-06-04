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
    # ── 10K wing tier (Phase 9H) ──
    # Same derivation as 2K/5K (strike where put/call volume crosses 10000),
    # just a stricter threshold. Requires the per-strike volume series; the
    # single-level public `wings.*` cannot synthesize a 10K tier, so under
    # public/wings-only data these stay None (like the 5K tier).
    put_ceiling_10k: float | None = None
    call_floor_10k:  float | None = None

    # ── Anchor volumes (Phase 2.8 + 9H) ──
    # Actual volume at each VW anchor strike, as reported by the structure
    # source. SEPARATE from whatever the QuoteProvider says at the same
    # strike — the structure owns "which volume qualified this level."
    # None means "no qualifying strike" or "level present but volume not
    # reported" (e.g. structure derived the level from a non-volume source).
    put_ceiling_2k_volume: float | None = None
    put_ceiling_5k_volume: float | None = None
    put_ceiling_10k_volume: float | None = None
    call_floor_2k_volume:  float | None = None
    call_floor_5k_volume:  float | None = None
    call_floor_10k_volume: float | None = None
    maxvol_volume:         float | None = None   # combined volume at maxvol strike

    # ── 10K Wing-Dominance inputs (Phase 9J) ──
    # The ADJACENT strike used to judge whether the 10K wing (W1) is clean/
    # dominant. For the CALL floor, W2 is the strike one step LOWER than the
    # floor; for the PUT ceiling, W2 is the strike one step HIGHER than the
    # ceiling. Volumes are side-specific (CALL volume for the call wing, PUT
    # volume for the put wing). WSR = W2_volume / W1_volume; WDS = 1 - WSR.
    # None when there is no qualifying 10K wing OR no adjacent strike in the
    # series (→ true WDS unavailable; never invented). See
    # `cockpit_helpers.wing_dominance`.
    call_floor_10k_w2_strike:  float | None = None
    call_floor_10k_w2_volume:  float | None = None
    put_ceiling_10k_w2_strike: float | None = None
    put_ceiling_10k_w2_volume: float | None = None

    # ── Gamma clusters (Phase 9H) ──
    # The most/next-most relevant gamma levels influencing spot. Mapped from
    # the ZS payload `gamma.cluster_primary` / `gamma.cluster_secondary` when
    # present. When absent, the UI derives a display-only primary/secondary
    # from the available gamma structure (walls / flip) — see
    # `cockpit_helpers.primary_secondary_gamma`.
    gamma_primary:   float | None = None
    gamma_secondary: float | None = None

    # DDOI pin (open-interest concentration / dealer-positioning gravity level).
    # NOT in the public ZS payload today → typically None. Phase 9H removed it
    # from the prime cockpit cards; it is only shown under Advanced Structure /
    # raw diagnostics when a value is actually present.
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
