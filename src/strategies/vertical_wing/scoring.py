"""Vertical Wing v1 — candidate scoring.

Each sub-score is normalized to ~[0, 1]; the final score is a weighted sum
using the weights from strategy params. Sub-scores are returned in
`Candidate.score_breakdown` so the UI/decision log can show *why*.

Quote-derived inputs (bid/ask quality, anchor volume) are read from
`Candidate.meta`, which `candidates.py` populates from the
`OptionChainSnapshot` at construction time.
"""

from __future__ import annotations

from typing import Any

from src.providers.quotes.types import OptionChainSnapshot
from src.providers.structure.types import StructureSnapshot
from src.strategies.base import Candidate


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _credit_size_score(credit: float) -> float:
    # 0.30 → 0.0, 1.00 → 1.0 (linear, clipped)
    return _clip((credit - 0.30) / 0.70)


def _credit_to_risk_score(rr: float) -> float:
    # 0.05 → 0.0, 0.50 → 1.0
    return _clip((rr - 0.05) / 0.45)


def _distance_score(distance_points: float) -> float:
    # 0 → 0, 30 → 1.0
    return _clip(abs(distance_points) / 30.0)


def _structure_strength_score(
    volume_at_anchor: float | None,
    *,
    anchor_source: str | None = None,
) -> tuple[float, str]:
    """Return (score, source_label).

    Policy (documented in plan.md §7.4):
      - volume present                       → (volume-1000)/4000 clipped to [0,1];
                                                source = "zs_volume_series"
      - volume None BUT anchor_source set    → neutral 0.5;
                                                source = "missing_anchor_volume_neutral"
        (Rationale: the structure provider gave us a level, which already
         implies some structure — we don't have the magnitude but we
         shouldn't punish a real ceiling/floor down to zero.)
      - no level AND no volume               → 0.0; source = "no_anchor"
    """
    if volume_at_anchor is not None:
        # 1000 → 0.2, 5000 → 1.0
        return _clip((volume_at_anchor - 1000) / 4000), "zs_volume_series"
    if anchor_source is not None:
        return 0.5, "missing_anchor_volume_neutral"
    return 0.0, "no_anchor"


def _maxvol_alignment_score(candidate: Candidate, structure: StructureSnapshot) -> float:
    mv = structure.exposures.maxvol
    if mv is None:
        return 0.5  # neutral
    if candidate.side == "CALL_CREDIT":
        # better if short strike >= maxvol (sell above the volume node)
        return 1.0 if candidate.short_strike >= mv else 0.0
    # PUT_CREDIT: better if short strike <= maxvol
    return 1.0 if candidate.short_strike <= mv else 0.0


def _gamma_regime_score(_: Candidate, structure: StructureSnapshot) -> float:
    regime = structure.exposures.gamma_regime
    if regime is None:
        return 0.5
    return 1.0 if regime == "positive" else 0.3


def _bid_ask_quality_score(candidate: Candidate) -> float:
    """Quote-derived. `candidates.py` precomputed this from leg bid/ask widths."""
    v = candidate.meta.get("bid_ask_quality")
    if v is None:
        return 0.5
    return _clip(float(v))


def _time_decay_headroom_score(_: StructureSnapshot) -> float:
    """Placeholder until intraday time-of-day is plumbed through.

    Returns a NEUTRAL 0.5 so this component neither favors nor penalizes
    any candidate. Documented as a placeholder in plan.md §7.4 — listed
    here so observability output makes its placeholder nature obvious.
    """
    return 0.5


def score_candidate(
    candidate: Candidate,
    structure: StructureSnapshot,
    chain: OptionChainSnapshot,
    params: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    weights = params.get("score_weights", {})
    vol_at_anchor = candidate.meta.get("anchor_volume")
    anchor_source = candidate.meta.get("anchor_source")
    ss_score, ss_source = _structure_strength_score(
        vol_at_anchor, anchor_source=anchor_source,
    )
    # Record the source label on the candidate so CSV/JSONL/UI can show it
    # without re-deriving. (Not a numeric score component, so not weighted.)
    candidate.meta["structure_strength_source"] = ss_source

    parts = {
        "credit_size":         _credit_size_score(candidate.credit),
        "credit_to_risk":      _credit_to_risk_score(candidate.reward_risk),
        "distance_from_spot":  _distance_score(candidate.distance_from_spot),
        "structure_strength":  ss_score,
        "maxvol_alignment":    _maxvol_alignment_score(candidate, structure),
        "gamma_regime":        _gamma_regime_score(candidate, structure),
        "bid_ask_quality":     _bid_ask_quality_score(candidate),
        "time_decay_headroom": _time_decay_headroom_score(structure),
    }

    total_weight = sum(weights.get(k, 0.0) for k in parts) or 1.0
    weighted = sum(parts[k] * weights.get(k, 0.0) for k in parts) / total_weight
    final = _clip(weighted)
    parts["final_score"] = final
    return final, parts
