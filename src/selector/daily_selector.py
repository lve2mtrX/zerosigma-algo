"""Configurable daily trade selector — Phase 5.

Chooses AT MOST ONE (configurable: `max_trades_per_day`) candidate from the
candidates a strategy already generated, scored, filtered, and stamped with
selector-readiness metadata (see `src/selector/readiness.py`).

THIS IS SELECTION ONLY. It does NOT execute, submit, preview, or place orders;
it does NOT change candidate generation, quote fetching, risk filters, or
strategy scoring. It marks `selected_trade=True` on at most `max_trades_per_day`
rows and explains every decision.

PURE module — no I/O, no network, no provider dependencies, and (by design) it
does NOT depend on the `vertical_wing` strategy package: it operates on generic
candidate ROW dicts (the same dicts `scripts/run_scanner.py` writes to the CSV,
already carrying the Phase 4.1 readiness fields) plus a `SelectorConfig`. This
keeps the selector reusable across strategies and keeps the no-vw-leak test green.

Input row dict keys consumed (all produced upstream by _candidate_row /
compute_readiness):
    side, score, credit, distance_from_spot, rejected,
    selector_eligible_base, candidate_passes_trade_filters,
    candidate_passes_risk_filters, candidate_passes_quote_filters,
    candidate_passes_score_threshold, candidate_passes_score_edge,
    candidate_is_marginal, quote_validation_passed, quote_quality_bucket,
    planned_stop_risk_pct
`gamma_regime` (structure-level) is passed separately as a context arg.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ── selector modes ────────────────────────────────────────────────────────
SELECTOR_MODES: tuple[str, ...] = (
    "score_best_valid",
    "best_credit_valid",
    "closest_wing_valid",
    "farthest_wing_valid",
    "call_credit_only",
    "put_credit_only",
    "lowest_breach_risk_valid",
    "regime_aligned_valid",
    "balanced_structure_premium_valid",   # Phase 9G — dynamic both-side selection
    "no_trade",
)

DEFAULT_SELECTOR_MODE = "score_best_valid"

# Side literals used by the strategy rows.
_CALL = "CALL_CREDIT"
_PUT = "PUT_CREDIT"


@dataclass(frozen=True)
class SelectorConfig:
    """Resolved selector configuration (CLI > env > YAML > default upstream).

    `run_scanner` resolves the raw knobs and constructs this; the selector
    itself never reads env/CLI so it stays pure + trivially testable.
    """
    mode: str = DEFAULT_SELECTOR_MODE
    max_trades_per_day: int = 1
    allow_call_credit: bool = True
    allow_put_credit: bool = True
    require_selector_eligible_base: bool = True
    require_quote_validation: bool = True
    require_score_edge: bool = False
    no_trade_on_selector_conflict: bool = True
    min_selector_score: float | None = None
    min_selector_credit: float | None = None
    min_selector_distance_from_spot: float | None = None
    max_selector_distance_from_spot: float | None = None
    lowest_breach_risk_distance_weight: float = 1.0
    lowest_breach_risk_credit_weight: float = 0.25
    lowest_breach_risk_risk_weight: float = 1.0
    # Phase 9G — balanced_structure_premium_valid weights (premium vs distance vs
    # structure safety; never "highest premium wins"). Configurable; conservative
    # defaults below.
    balanced_structure_weight: float = 1.0
    balanced_premium_weight: float = 0.75
    balanced_distance_weight: float = 0.75
    balanced_maxvol_weight: float = 0.75
    balanced_quote_weight: float = 0.50
    balanced_score_weight: float = 0.75
    balanced_risk_penalty_weight: float = 0.50

    def summary(self) -> str:
        """Compact, secrets-free one-line summary for the CSV / audit print."""
        parts = [
            f"mode={self.mode}",
            f"max/day={self.max_trades_per_day}",
            f"call={self.allow_call_credit}",
            f"put={self.allow_put_credit}",
            f"req_base={self.require_selector_eligible_base}",
            f"req_quote={self.require_quote_validation}",
            f"req_edge={self.require_score_edge}",
        ]
        if self.min_selector_score is not None:
            parts.append(f"min_score={self.min_selector_score}")
        if self.min_selector_credit is not None:
            parts.append(f"min_credit={self.min_selector_credit}")
        if self.min_selector_distance_from_spot is not None:
            parts.append(f"min_dist={self.min_selector_distance_from_spot}")
        if self.max_selector_distance_from_spot is not None:
            parts.append(f"max_dist={self.max_selector_distance_from_spot}")
        if self.mode == "balanced_structure_premium_valid":
            parts.append(
                f"weights[struct={self.balanced_structure_weight},"
                f"prem={self.balanced_premium_weight},"
                f"dist={self.balanced_distance_weight},"
                f"maxvol={self.balanced_maxvol_weight},"
                f"quote={self.balanced_quote_weight},"
                f"score={self.balanced_score_weight},"
                f"risk={self.balanced_risk_penalty_weight}]"
            )
        return " ".join(parts)


# ── per-row + scan-level result containers ─────────────────────────────────

def _blank_row_meta(side_allowed: bool) -> dict[str, Any]:
    return {
        "selected_trade": False,
        "selector_rank": None,
        "selector_reason": "",
        "selector_score": None,
        "selector_score_components": None,
        "selector_tiebreaker": None,
        "side_allowed_by_config": side_allowed,
        "selector_blockers": [],
    }


@dataclass
class SelectorResult:
    """Outcome of one selector pass over a tick's candidate rows."""
    daily_selector_mode: str
    max_trades_per_day: int
    selector_config_summary: str
    selected_indices: list[int] = field(default_factory=list)
    selector_rejection_reason: str | None = None
    selector_no_trade_reason: str | None = None
    selector_conflict_detected: bool = False
    selector_explanation: str | None = None   # Phase 9G — why this side/trade won
    per_row: list[dict[str, Any]] = field(default_factory=list)

    @property
    def selected_trade(self) -> bool:
        return bool(self.selected_indices)


# ── numeric helpers ─────────────────────────────────────────────────────────

def _num(row: dict, key: str) -> float | None:
    v = row.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _truthy(row: dict, key: str, *, default: bool = False) -> bool:
    v = row.get(key)
    return bool(v) if v is not None else default


# ── eligibility gate (shared by all *_valid + side-only modes) ──────────────

def _evaluate_eligibility(
    row: dict, cfg: SelectorConfig,
) -> tuple[bool, bool, list[str]]:
    """Return (eligible, side_allowed_by_config, blockers).

    Adds SELECTOR-layer blockers only; readiness blockers already live on the
    row's own `selector_blockers` (we do not mutate that — the scanner merges
    these selector blockers separately).
    """
    blockers: list[str] = []

    # Side filter first (so side_allowed_by_config is always meaningful).
    side = row.get("side")
    if side == _CALL:
        side_allowed = cfg.allow_call_credit
    elif side == _PUT:
        side_allowed = cfg.allow_put_credit
    else:
        side_allowed = True  # unknown side — don't block on side alone
    if not side_allowed:
        blockers.append("side_disabled_by_config")

    # Never select rejected candidates.
    if _truthy(row, "rejected"):
        blockers.append("rejected")

    # Base readiness gate (default required).
    if cfg.require_selector_eligible_base and not _truthy(
        row, "selector_eligible_base", default=False,
    ):
        blockers.append("not_selector_eligible_base")

    # Respect the four pass_* buckets explicitly.
    for key, blk in (
        ("candidate_passes_trade_filters", "trade_filters_failed"),
        ("candidate_passes_risk_filters", "risk_filters_failed"),
        ("candidate_passes_quote_filters", "quote_filters_failed"),
        ("candidate_passes_score_threshold", "score_threshold_failed"),
    ):
        # Treat a missing flag as a pass (mock fixtures may omit it) ONLY when
        # selector_eligible_base isn't being relied upon; otherwise the base
        # gate already covers it. Be conservative: missing → pass.
        if row.get(key) is False:
            blockers.append(blk)

    # REQUIRE_QUOTE_VALIDATION: exclude explicitly-invalid quotes. None (mock /
    # unvalidated) is allowed through — only an explicit False / 'invalid' blocks.
    if cfg.require_quote_validation:
        qv = row.get("quote_validation_passed")
        if qv is False or row.get("quote_quality_bucket") == "invalid":
            blockers.append("quote_validation_required")

    # REQUIRE_SCORE_EDGE: exclude marginal / no-edge candidates.
    if cfg.require_score_edge:
        if row.get("candidate_passes_score_edge") is False or _truthy(
            row, "candidate_is_marginal",
        ):
            blockers.append("score_edge_required")

    # MIN/MAX score / credit / distance filters.
    score = _num(row, "score")
    credit = _num(row, "credit")
    dist = _num(row, "distance_from_spot")
    dist_abs = abs(dist) if dist is not None else None
    if cfg.min_selector_score is not None and (score is None or score < cfg.min_selector_score):
        blockers.append("selector_score_below_min")
    if cfg.min_selector_credit is not None and (credit is None or credit < cfg.min_selector_credit):
        blockers.append("selector_credit_below_min")
    if cfg.min_selector_distance_from_spot is not None and (
        dist_abs is None or dist_abs < cfg.min_selector_distance_from_spot
    ):
        blockers.append("selector_distance_below_min")
    if cfg.max_selector_distance_from_spot is not None and (
        dist_abs is not None and dist_abs > cfg.max_selector_distance_from_spot
    ):
        blockers.append("selector_distance_above_max")

    eligible = len(blockers) == 0
    return eligible, side_allowed, blockers


# ── per-mode ranking key (higher tuple sorts first) ─────────────────────────

def _ranking(row: dict, mode: str, cfg: SelectorConfig) -> tuple[tuple[float, ...], str | None]:
    """Return (sort_key, tiebreaker_label). Rows sort DESCENDING by sort_key;
    the first element is the mode's primary criterion, the rest are tiebreakers.
    """
    score = _num(row, "score") or 0.0
    credit = _num(row, "credit") or 0.0
    dist = _num(row, "distance_from_spot")
    dist_abs = abs(dist) if dist is not None else 0.0

    if mode == "best_credit_valid":
        # credit, then score, then farthest distance
        return (credit, score, dist_abs), "credit>score>distance"
    if mode == "closest_wing_valid":
        # shortest distance (negate so smaller distance sorts first), then score, then credit
        return (-dist_abs, score, credit), "distance(closest)>score>credit"
    if mode == "farthest_wing_valid":
        return (dist_abs, score, credit), "distance(farthest)>score>credit"
    # score_best_valid / call_credit_only / put_credit_only / regime_aligned_valid
    # all rank by score, then credit, then farthest distance.
    return (score, credit, dist_abs), "score>credit>distance"


def _breach_risk_components(row: dict, cfg: SelectorConfig) -> dict[str, Any]:
    """Transparent composite for lowest_breach_risk_valid. Higher = safer.

      distance_component = distance_weight * |distance_from_spot|   (farther = safer)
      credit_component   = credit_weight   * credit                  (acceptable credit)
      risk_component      = -risk_weight   * (planned_stop_risk_pct * 100)  (lower risk = safer)

    `planned_stop_risk_pct` missing → risk_component=0.0 + partial=True (no crash).
    """
    dist = _num(row, "distance_from_spot")
    dist_abs = abs(dist) if dist is not None else 0.0
    credit = _num(row, "credit") or 0.0
    psr = _num(row, "planned_stop_risk_pct")

    distance_component = cfg.lowest_breach_risk_distance_weight * dist_abs
    credit_component = cfg.lowest_breach_risk_credit_weight * credit
    partial = psr is None
    risk_component = 0.0 if partial else -cfg.lowest_breach_risk_risk_weight * (psr * 100.0)

    total = distance_component + credit_component + risk_component
    return {
        "distance_component": round(distance_component, 6),
        "credit_component": round(credit_component, 6),
        "risk_component": round(risk_component, 6),
        "total": round(total, 6),
        "partial": partial,
        "weights": {
            "distance": cfg.lowest_breach_risk_distance_weight,
            "credit": cfg.lowest_breach_risk_credit_weight,
            "risk": cfg.lowest_breach_risk_risk_weight,
        },
    }


# ── balanced_structure_premium_valid (Phase 9G) ─────────────────────────────

_QUOTE_BUCKET_SCORE = {
    "good": 1.0, "acceptable": 0.7, "poor": 0.4, "wide": 0.2,
    "invalid": 0.0, "unknown": 0.5,
}


def _quote_raw(row: dict) -> float | None:
    """Quote-quality 0..1 — prefer bid_ask_quality, else map the bucket."""
    baq = _num(row, "bid_ask_quality")
    if baq is not None:
        return baq
    bucket = row.get("quote_quality_bucket")
    if isinstance(bucket, str):
        return _QUOTE_BUCKET_SCORE.get(bucket.lower())
    return None


def _balanced_raw(row: dict) -> dict[str, float | None]:
    """Raw (un-normalized) component inputs. Missing fields → None (neutral)."""
    dist = _num(row, "distance_from_spot")
    structure = _num(row, "anchor_volume")
    if structure is None:
        structure = _num(row, "structure_strength")
    maxvol = _num(row, "maxvol_alignment")
    if maxvol is None:
        maxvol = _num(row, "score_maxvol_alignment")
    return {
        "premium": _num(row, "credit"),
        "distance": abs(dist) if dist is not None else None,
        "structure": structure,
        "maxvol": maxvol,
        "quote": _quote_raw(row),
        "score": _num(row, "score"),
        "risk": _num(row, "planned_stop_risk_pct"),
    }


def _norm(values: list[float | None]) -> list[float]:
    """Min-max normalize to [0,1] across the candidate set. None / all-equal →
    neutral 0.5 (so a missing or flat component never dominates)."""
    present = [v for v in values if v is not None]
    if not present:
        return [0.5] * len(values)
    lo, hi = min(present), max(present)
    if hi <= lo:
        return [0.5] * len(values)
    return [((v - lo) / (hi - lo)) if v is not None else 0.5 for v in values]


def _balanced_components(rows: list[dict], cfg: SelectorConfig) -> list[dict[str, Any]]:
    """Transparent balanced score per row — normalized WITHIN the eligible set so
    the better side wins on the premium/distance/structure tradeoff (NEVER
    highest-premium-only, NEVER farthest-distance-only)."""
    raws = [_balanced_raw(r) for r in rows]
    keys = ("premium", "distance", "structure", "maxvol", "quote", "score", "risk")
    norms = {k: _norm([raw[k] for raw in raws]) for k in keys}
    weights = {
        "structure": cfg.balanced_structure_weight,
        "premium": cfg.balanced_premium_weight,
        "distance": cfg.balanced_distance_weight,
        "maxvol": cfg.balanced_maxvol_weight,
        "quote": cfg.balanced_quote_weight,
        "score": cfg.balanced_score_weight,
        "risk": cfg.balanced_risk_penalty_weight,
    }
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(raws):
        premium, distance = norms["premium"][i], norms["distance"][i]
        structure, maxvol = norms["structure"][i], norms["maxvol"][i]
        quote, score, risk = norms["quote"][i], norms["score"][i], norms["risk"][i]
        total = (
            weights["structure"] * structure + weights["premium"] * premium
            + weights["distance"] * distance + weights["maxvol"] * maxvol
            + weights["quote"] * quote + weights["score"] * score
            - weights["risk"] * risk
        )
        out.append({
            "premium_score": round(premium, 4),
            "distance_safety_score": round(distance, 4),
            "structure_score": round(structure, 4),
            "maxvol_gamma_alignment_score": round(maxvol, 4),
            "quote_quality_score": round(quote, 4),
            "existing_candidate_score": round(score, 4),
            "planned_risk_penalty": round(risk, 4),
            "total": round(total, 4),
            "partial": any(raw[k] is None for k in keys),
            "weights": dict(weights),
        })
    return out


def _balanced_explanation(win_row: dict, win_comps: dict, other_row: dict | None,
                          other_comps: dict | None) -> str:
    """Human 'why this side won' for the balanced selector."""
    wside = win_row.get("side") or "trade"
    if other_row is None or other_comps is None:
        return (f"Selected {wside}: only eligible side this tick "
                f"(balanced score {win_comps['total']}).")
    oside = other_row.get("side") or "the other side"
    reasons = []
    if win_comps["structure_score"] > other_comps["structure_score"]:
        reasons.append("stronger structure")
    reasons.append("better/comparable credit"
                   if win_comps["premium_score"] >= other_comps["premium_score"]
                   else "acceptable credit")
    if win_comps["distance_safety_score"] >= other_comps["distance_safety_score"]:
        reasons.append("safer distance from spot")
    if win_comps["planned_risk_penalty"] <= other_comps["planned_risk_penalty"]:
        reasons.append("lower planned risk")
    return (f"Selected {wside} because it had {', '.join(reasons)} than the {oside} "
            f"alternative (balanced score {win_comps['total']} vs {other_comps['total']}).")


# ── main entry point ────────────────────────────────────────────────────────

def select_daily_trade(
    rows: list[dict[str, Any]],
    cfg: SelectorConfig,
    *,
    gamma_regime: Any | None = None,
) -> SelectorResult:
    """Select at most `cfg.max_trades_per_day` candidates from `rows`.

    Preserves every input row (returns one per_row entry per input row, in
    order). Marks `selected_trade=True` on the chosen rows only. Never selects
    a rejected or ineligible candidate. Returns a NO_TRADE result (empty
    `selected_indices` + a reason) when nothing qualifies.
    """
    mode = cfg.mode if cfg.mode in SELECTOR_MODES else DEFAULT_SELECTOR_MODE
    result = SelectorResult(
        daily_selector_mode=mode,
        max_trades_per_day=int(cfg.max_trades_per_day),
        selector_config_summary=cfg.summary(),
        per_row=[_blank_row_meta(True) for _ in rows],
    )

    def _no_trade(reason: str) -> SelectorResult:
        result.selector_rejection_reason = reason
        result.selector_no_trade_reason = reason
        for i in range(len(rows)):
            if not result.per_row[i]["selector_reason"]:
                result.per_row[i]["selector_reason"] = f"no_trade:{reason}"
        return result

    # no_trade mode — always select nothing.
    if mode == "no_trade":
        for i in range(len(rows)):
            result.per_row[i]["selector_reason"] = "no_trade_mode"
        return _no_trade("no_trade_mode")

    # Both sides disabled — global short-circuit.
    if not cfg.allow_call_credit and not cfg.allow_put_credit:
        for i in range(len(rows)):
            meta = result.per_row[i]
            meta["side_allowed_by_config"] = False
            meta["selector_blockers"] = ["side_disabled_by_config"]
            meta["selector_reason"] = "no_sides_allowed"
        return _no_trade("no_sides_allowed")

    # regime_aligned_valid — conservative gamma gate BEFORE eligibility.
    if mode == "regime_aligned_valid":
        regime = str(gamma_regime).strip().lower() if gamma_regime is not None else ""
        if regime in ("positive", "neutral"):
            regime_note = f"regime_supports_both_sides(gamma_regime={regime})"
        elif regime == "negative":
            for i in range(len(rows)):
                result.per_row[i]["selector_reason"] = "regime_negative_blocked"
            return _no_trade("regime_negative_blocked")
        else:
            for i in range(len(rows)):
                result.per_row[i]["selector_reason"] = "insufficient_regime_data"
            return _no_trade("insufficient_regime_data")
    else:
        regime_note = None

    # Side restriction for call/put-only modes.
    side_only: str | None = None
    if mode == "call_credit_only":
        side_only = _CALL
    elif mode == "put_credit_only":
        side_only = _PUT

    # 1) Evaluate eligibility for every row; collect eligible candidate indices.
    eligible: list[int] = []
    for i, row in enumerate(rows):
        is_elig, side_allowed, blockers = _evaluate_eligibility(row, cfg)
        meta = result.per_row[i]
        meta["side_allowed_by_config"] = side_allowed
        meta["selector_blockers"] = list(blockers)

        if side_only is not None and row.get("side") != side_only:
            meta["selector_reason"] = f"excluded:wrong_side_for_{mode}"
            continue
        if not is_elig:
            meta["selector_reason"] = "ineligible:" + ",".join(blockers)
            continue

        # eligible — stamp the mode score now (so even non-winners show it).
        if mode == "lowest_breach_risk_valid":
            comps = _breach_risk_components(row, cfg)
            meta["selector_score"] = comps["total"]
            meta["selector_score_components"] = comps
        elif mode == "balanced_structure_premium_valid":
            pass  # deferred — balanced score is normalized across the eligible set below
        else:
            key, tb = _ranking(row, mode, cfg)
            meta["selector_score"] = round(key[0], 6)
            meta["selector_tiebreaker"] = tb
        meta["selector_reason"] = "eligible"
        eligible.append(i)

    if not eligible:
        reason = (
            f"no_eligible_{side_only.lower()}_candidate" if side_only
            else "no_eligible_candidate"
        )
        return _no_trade(reason)

    # 1b) Balanced selector — normalize components across the eligible SET, then
    # stamp each eligible row's transparent score (deferred from the loop above).
    if mode == "balanced_structure_premium_valid":
        elig_rows = [rows[i] for i in eligible]
        comps_list = _balanced_components(elig_rows, cfg)
        for j, i in enumerate(eligible):
            meta = result.per_row[i]
            meta["selector_score"] = comps_list[j]["total"]
            meta["selector_score_components"] = comps_list[j]
            meta["selector_tiebreaker"] = "balanced_total>score>distance"

    # 2) Rank eligible candidates by the mode.
    if mode == "lowest_breach_risk_valid":
        def sort_key(i: int) -> tuple[float, ...]:
            comps = result.per_row[i]["selector_score_components"]
            # tiebreak: total, then score, then |distance|
            return (
                comps["total"],
                _num(rows[i], "score") or 0.0,
                abs(_num(rows[i], "distance_from_spot") or 0.0),
            )
        for i in eligible:
            result.per_row[i]["selector_tiebreaker"] = "breach_total>score>distance"
    elif mode == "balanced_structure_premium_valid":
        def sort_key(i: int) -> tuple[float, ...]:
            comps = result.per_row[i]["selector_score_components"]
            # tiebreak: balanced total, then existing score, then |distance|
            return (
                comps["total"],
                _num(rows[i], "score") or 0.0,
                abs(_num(rows[i], "distance_from_spot") or 0.0),
            )
    else:
        def sort_key(i: int) -> tuple[float, ...]:
            return _ranking(rows[i], mode, cfg)[0]

    ranked = sorted(eligible, key=sort_key, reverse=True)

    # selector_rank (1-based) for every eligible row.
    for rank, i in enumerate(ranked, start=1):
        result.per_row[i]["selector_rank"] = rank

    # 3) Conflict detection: an exact tie on the FULL sort key at the selection
    # boundary that cannot be broken by any tiebreaker.
    n = max(1, int(cfg.max_trades_per_day))
    if len(ranked) > n and sort_key(ranked[n - 1]) == sort_key(ranked[n]):
        result.selector_conflict_detected = True
        if cfg.no_trade_on_selector_conflict:
            for i in ranked:
                result.per_row[i]["selector_reason"] = "eligible_but_selector_conflict"
            return _no_trade("selector_conflict")

    # 4) Mark the top-n as selected.
    winners = ranked[:n]
    for i in winners:
        meta = result.per_row[i]
        meta["selected_trade"] = True
        base = f"selected:{mode}"
        if regime_note:
            base += f";{regime_note}"
        if meta.get("selector_tiebreaker"):
            base += f";tiebreak={meta['selector_tiebreaker']}"
        meta["selector_reason"] = base

    # 5) Balanced selector — explain WHY the winning side beat the other side
    # (winner vs the best eligible runner-up on the OPPOSITE side).
    if mode == "balanced_structure_premium_valid" and winners:
        wi = winners[0]
        wrow, wcomps = rows[wi], result.per_row[wi]["selector_score_components"]
        wside = wrow.get("side")
        other_i = next(
            (i for i in ranked if i != wi and rows[i].get("side") != wside), None
        )
        ocomps = (
            result.per_row[other_i]["selector_score_components"]
            if other_i is not None else None
        )
        orow = rows[other_i] if other_i is not None else None
        expl = _balanced_explanation(wrow, wcomps, orow, ocomps)
        result.selector_explanation = expl
        result.per_row[wi]["selector_reason"] += f"; {expl}"

    result.selected_indices = list(winners)
    return result


# ── serialization helper for CSV (score_components is a dict) ───────────────

def components_to_str(components: Any) -> str:
    """JSON-encode selector_score_components for a CSV cell (empty string when None)."""
    if components is None:
        return ""
    try:
        return json.dumps(components, default=float, sort_keys=True)
    except (TypeError, ValueError):
        return str(components)
