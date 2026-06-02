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
