"""Selector-readiness audit — Phase 4.1.

PURE function consumed by:
  - `scripts/run_scanner.py` to populate the ranked_candidates.csv columns
  - `src/app/streamlit_main.py` per-candidate expander
  - tests that need to assert blocker semantics

It does NOT make a selection decision — that is Phase 5. All it does is
classify a Candidate into a flat dict of boolean readiness flags + a
list of human-readable blocker strings, so a future selector can consume
the same view without re-deriving it.

INTENTIONAL NON-GOALS:
  - No portfolio/session aggregation (max_open_positions, daily caps).
  - No execution.
  - No filtering — candidates that fail all four buckets still get a row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.strategies.base import Candidate
from src.utils.quote_quality import (
    DEFAULT_ACCEPTABLE_PCT,
    DEFAULT_GOOD_PCT,
    DEFAULT_POOR_PCT,
)
from src.utils.quote_quality import quality_bucket as _quality_bucket

# Buckets a candidate's quote quality lives in, after the validator runs.
# Ordered: invalid → poor → acceptable → good. unknown=no quote data at all
# (e.g. mock provider without validation, missing leg widths).
QuoteQualityBucket = str  # one of {good, acceptable, poor, invalid, wide, unknown}

# Phase 4.2 — bucket boundaries MIGRATED from absolute-$ bins to pct-of-mid
# (fraction of the worst leg's mid). The SHARED helper in src/utils/quote_quality
# owns the cutoffs (good<=3%, acceptable<=7%, poor<=15%, wide>15%) so the bucket
# and the bid_ask_quality SCORE always agree — fixing the live 4.1 case where a
# quote PASSED validation yet scored bid_ask_quality=0.00 with bucket='poor'.
# This module PREFERS the bucket/reason stamped onto Candidate.meta by the
# strategy; the helper here is the fallback for fixtures / mock candidates that
# carry only a pct (or nothing).


@dataclass(frozen=True)
class _SessionLike:
    """Minimal duck-type for `session` arg — we read starting_balance only."""
    starting_balance: float


def _shape_filter_reasons(c: Candidate) -> list[str]:
    """Return any non-risk rejection reasons from c.rejection_reasons.

    The risk filters (planned_loss / theoretical_loss) ALSO append a
    human-readable string to rejection_reasons; we filter those out here so
    `candidate_passes_trade_filters` only reflects shape/credit/distance/RR
    failures, NOT risk-cap failures (those land in `risk_filters`).
    """
    out: list[str] = []
    for r in c.rejection_reasons or ():
        rs = (r or "").lower()
        if "planned stop risk" in rs or "theoretical max loss" in rs:
            continue
        out.append(r)
    return out


def compute_readiness(
    c: Candidate,
    *,
    session: Any,
    threshold: float,
    min_score_edge: float,
    target_dte: int = 0,
    available_expiries: list[str] | None = None,
    today_et: date | None = None,
    expiry_selection_reason: str | None = None,
    strict_target_dte: bool = False,
    strict_target_dte_passed: bool = True,
) -> dict[str, Any]:
    """Classify `c` into a flat dict of selector-facing readiness fields.

    Returns a dict with EVERY key set (no missing keys downstream), suitable
    for CSV row.update() or st.json() display.

    Args:
      c:                       Candidate after generate→filter→score→select
      session:                 a SessionConfig (or anything with
                               `starting_balance` attr) — used for the
                               planned_stop_risk_pct ratio
      threshold:               score threshold (typically c.score_threshold)
      min_score_edge:          MIN_SCORE_EDGE env value (typically 0.02)
      target_dte:              the operator's target DTE (env/CLI/YAML)
      available_expiries:      what the broker chain advertises this tick
      today_et:                today's ET date — defaults to None which
                               causes candidate_dte to be None
      expiry_selection_reason: optional override; if not provided, derived
                               from (target_dte, c.expiry, available_expiries)
      strict_target_dte:       Phase 4.2 — when True, a target_dte that could
                               only be served by an expiry FALLBACK is treated
                               as unavailable (the scanner forces NO_TRADE).
                               Default False = today's lax fallback behavior.
      strict_target_dte_passed: False when strict mode is on AND the requested
                               target_dte was NOT available (fell back). When
                               this is False under strict_target_dte=True, a
                               'strict_target_dte_unavailable' blocker is added,
                               esr is overridden, and eligibility flips False.

    Output keys (ALL always present):
      - candidate_passes_score_threshold (bool)
      - candidate_passes_score_edge       (bool)
      - candidate_passes_trade_filters    (bool)
      - candidate_passes_risk_filters     (bool)
      - candidate_passes_quote_filters    (bool)
      - candidate_is_marginal             (bool)
      - selector_eligible_base            (bool — all 4 base buckets pass)
      - selector_blockers                 (list[str])
      - selector_readiness_note           (str)
      - risk_rejection_type               (str | None)
      - risk_rejection_reason             (str | None)
      - quote_quality_bucket              (str)
      - quote_quality_reason              (str)
      - spread_abs                        (float | None)
      - spread_pct                        (float | None)
      - planned_stop_risk_pct             (float | None)
      - target_dte                        (int)
      - selected_expiry                   (str)
      - candidate_dte                     (int | None)
      - expiry_selection_reason           (str)
      - strict_target_dte                 (bool)
      - strict_target_dte_passed          (bool)
    """
    score = float(c.score or 0.0)
    edge = score - float(threshold)
    passes_score = score >= float(threshold)
    passes_edge = edge >= float(min_score_edge)
    is_marginal = passes_score and not passes_edge

    # Risk: rely on the structured fields stamped by the cap filters.
    risk_rejections = c.meta.get("risk_rejections") or {}
    risk_failures = [k for k, v in risk_rejections.items() if v.get("passed") is False]
    risk_reasons = [
        v.get("reason") for v in risk_rejections.values()
        if v.get("passed") is False and v.get("reason")
    ]
    passes_risk = len(risk_failures) == 0
    risk_rejection_type = c.meta.get("risk_rejection_type") or (
        risk_failures[-1] if risk_failures else None
    )
    risk_rejection_reason = "; ".join(r for r in risk_reasons if r) or None

    # Quote: classify on worst-leg PCT-of-mid AND validator pass/fail.
    # Phase 4.2 — PREFER the bucket/reason the strategy stamped into meta
    # (computed from the SAME pct cutoffs as the bid_ask_quality score, so the
    # two agree). FALL BACK to the shared helper for fixtures / mock candidates
    # that carry only a pct (or nothing). readiness NEVER re-derives the score.
    short_leg = c.meta.get("short_leg") or {}
    long_leg = c.meta.get("long_leg") or {}
    short_passed = short_leg.get("validation_passed")
    long_passed = long_leg.get("validation_passed")
    worst_leg_abs = c.meta.get("worst_leg_bid_ask_abs")
    worst_leg_pct = c.meta.get("worst_leg_bid_ask_pct_of_mid")
    stamped_bucket = c.meta.get("quote_quality_bucket")
    stamped_reason = c.meta.get("quote_quality_reason")
    if isinstance(stamped_bucket, str) and isinstance(stamped_reason, str):
        bucket, bucket_reason = stamped_bucket, stamped_reason
    else:
        bucket, bucket_reason = _quality_bucket(
            worst_pct=(
                float(worst_leg_pct)
                if isinstance(worst_leg_pct, (int, float))
                else None
            ),
            short_passed=short_passed,
            long_passed=long_passed,
            good_pct=DEFAULT_GOOD_PCT,
            acceptable_pct=DEFAULT_ACCEPTABLE_PCT,
            poor_pct=DEFAULT_POOR_PCT,
        )
    # A candidate "passes quote filters" if NO leg explicitly failed.
    # 'unknown' counts as a pass (mock chain leaves validation None).
    passes_quote = bucket != "invalid"

    # Trade-shape filters: anything in rejection_reasons that isn't risk.
    shape_reasons = _shape_filter_reasons(c)
    passes_trade = not c.rejected or len(shape_reasons) == 0
    # If c.rejected is True ONLY because risk failed, trade_filters still pass.
    if c.rejected and len(shape_reasons) > 0:
        passes_trade = False

    # planned_stop_risk_pct — dollars over starting_balance
    psr_dollars = c.meta.get("planned_stop_risk_dollars")
    starting_balance = getattr(session, "starting_balance", None)
    if (
        isinstance(psr_dollars, (int, float))
        and isinstance(starting_balance, (int, float))
        and starting_balance > 0
    ):
        planned_stop_risk_pct = float(psr_dollars) / float(starting_balance)
    else:
        planned_stop_risk_pct = None

    # Selector_eligible_base — all four buckets pass AND not marginal.
    # Phase 5 will probably also gate on `passes_edge` here; today we keep
    # marginal as observability so today's CALL_CREDIT 0.0013-edge row is
    # still 'eligible_base=True' (it CAN trade) but `candidate_is_marginal=True`.
    selector_eligible_base = (
        passes_score and passes_trade and passes_risk and passes_quote
    )

    # Build a HUMAN-readable blockers list — one entry per failed bucket.
    blockers: list[str] = []
    if not passes_score:
        blockers.append(f"score_below_threshold(score={score:.4f}<thr={threshold:.2f})")
    if not passes_edge and passes_score:
        blockers.append(f"score_below_min_edge(edge={edge:+.4f}<min={min_score_edge:.4f})")
    if not passes_risk:
        for rt in risk_failures:
            blockers.append(f"risk_rejected:{rt}")
    if not passes_quote:
        blockers.append(f"quote_invalid:{bucket_reason}")
    if not passes_trade:
        # First shape-reason wins for the blocker; full list still in CSV
        first = shape_reasons[0] if shape_reasons else "trade_filter_rejected"
        blockers.append(f"trade_filter:{first}")

    # Candidate DTE relative to today (calendar days)
    candidate_dte: int | None = None
    if today_et is not None and c.expiry:
        try:
            cd = date.fromisoformat(c.expiry)
            candidate_dte = (cd - today_et).days
        except (TypeError, ValueError):
            candidate_dte = None

    # Expiry selection reason — when not supplied by caller, derive from
    # the relationship between target_dte and what was actually fetched.
    if expiry_selection_reason is None:
        if available_expiries is None:
            esr = "no_chain_discovery"
        elif c.expiry in (available_expiries or []):
            esr = "matches_target" if candidate_dte == target_dte else "explicit"
        else:
            esr = "fallback_only_available"
    else:
        esr = expiry_selection_reason

    # Phase 4.2 — strict target-DTE gate. When strict mode is on AND the
    # requested target_dte could only be served by a fallback expiry, the
    # candidate is NOT eligible: add the blocker, override esr (it wins over
    # the matches_target/fallback derivation above), and flip eligibility.
    # Applied AFTER esr derivation so 'strict_target_dte_unavailable' wins; it
    # never fires when strict_target_dte is False (default).
    if strict_target_dte and not strict_target_dte_passed:
        blockers.append("strict_target_dte_unavailable")
        esr = "strict_target_dte_unavailable"
        selector_eligible_base = False

    # Short readiness note — 1-line summary for the audit print.
    if selector_eligible_base and not is_marginal:
        note = "eligible"
    elif selector_eligible_base and is_marginal:
        note = "eligible_but_marginal"
    elif blockers:
        note = "blocked"
    else:
        note = "unknown"

    return {
        "candidate_passes_score_threshold":  bool(passes_score),
        "candidate_passes_score_edge":       bool(passes_edge),
        "candidate_passes_trade_filters":    bool(passes_trade),
        "candidate_passes_risk_filters":     bool(passes_risk),
        "candidate_passes_quote_filters":    bool(passes_quote),
        "candidate_is_marginal":             bool(is_marginal),
        "selector_eligible_base":            bool(selector_eligible_base),
        "selector_blockers":                 list(blockers),
        "selector_readiness_note":           note,
        "risk_rejection_type":               risk_rejection_type,
        "risk_rejection_reason":             risk_rejection_reason,
        "quote_quality_bucket":              bucket,
        "quote_quality_reason":              bucket_reason,
        "spread_abs":                        (
            float(worst_leg_abs) if isinstance(worst_leg_abs, (int, float)) else None
        ),
        "spread_pct":                        (
            float(worst_leg_pct) if isinstance(worst_leg_pct, (int, float)) else None
        ),
        "planned_stop_risk_pct":             (
            float(planned_stop_risk_pct) if planned_stop_risk_pct is not None else None
        ),
        "target_dte":                        int(target_dte),
        "selected_expiry":                   c.expiry,
        "candidate_dte":                     candidate_dte,
        "expiry_selection_reason":           esr,
        "strict_target_dte":                 bool(strict_target_dte),
        "strict_target_dte_passed":          bool(strict_target_dte_passed),
    }
