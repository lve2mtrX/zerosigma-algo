"""Shared quote-quality scoring + bucketing — Phase 4.2.

PURE, stdlib-only module. NO project imports, NO I/O, NO network. It holds
the single source of truth for BOTH:

  - the ``bid_ask_quality`` sub-score (0.0 .. 1.0), and
  - the ``quote_quality_bucket`` label (good/acceptable/poor/wide/invalid/unknown),

so the two ALWAYS agree. This fixes the live Phase 4.1 contradiction where a
quote PASSED broker validation yet ``bid_ask_quality`` scored 0.00 with
bucket='poor' — because the old scorer used a blunt ABSOLUTE ~$0.20 cap while
the bucket used different absolute bins.

Phase 4.2 migrates BOTH to RELATIVE pct-of-mid cutoffs:

    good       <= 3%
    acceptable <= 7%
    poor       <= 15%
    wide        > 15%

This module is intentionally placed under ``src/utils`` (a neutral shared
location alongside ``expiry.py`` / ``config.py`` / ``time.py``) so it can be
imported by BOTH the strategy candidate builder (which stamps the results
into ``Candidate.meta``) AND the selector-readiness audit (which reads meta,
falling back to this helper for fixtures lacking a stamped bucket). Neither
importer needs to reach into the other.

Score interpolation (locked endpoints):
    worst_pct is None or < 0           -> 0.0
    worst_pct <= good_pct              -> 1.0
    good_pct  < worst_pct <= accept    -> linear 0.8 .. 0.6
    accept    < worst_pct <= poor      -> linear 0.5 .. 0.2
    worst_pct > poor_pct               -> 0.0

The legacy 'absolute' mode (1 - worst_abs/max_abs_cap) is preserved as an
opt-in knob and is used automatically when a relative score is requested but
the worst leg has no usable mid (worst_pct is None), so a missing mid never
silently zeroes an otherwise-valid quote.
"""

from __future__ import annotations

# Default RELATIVE cutoffs (fraction of mid). The SAME cutoffs drive both the
# score interpolation and the bucket label so they cannot disagree.
DEFAULT_GOOD_PCT = 0.03
DEFAULT_ACCEPTABLE_PCT = 0.07
DEFAULT_POOR_PCT = 0.15

# Default ABSOLUTE cap (dollars), used only when mode='absolute' OR when a
# relative score is requested but worst_pct is None. NOTE: this default (1.00)
# is deliberately NOT the legacy 0.20 literal — relative is the new default and
# the absolute cap is a separate knob. An operator chasing legacy parity must
# set BID_ASK_MAX_ABS_CAP=0.20 explicitly.
DEFAULT_MAX_ABS_CAP = 1.00


def _interp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Linear interpolation of x over (x0, x1] onto [y0, y1].

    Guards a degenerate zero-width band by returning y1 (the band's far end).
    """
    span = x1 - x0
    if span <= 0:
        return y1
    frac = (x - x0) / span
    return y0 + (y1 - y0) * frac


def relative_score(
    worst_pct: float | None,
    *,
    good_pct: float = DEFAULT_GOOD_PCT,
    acceptable_pct: float = DEFAULT_ACCEPTABLE_PCT,
    poor_pct: float = DEFAULT_POOR_PCT,
) -> float:
    """Relative-width bid/ask quality score in [0.0, 1.0].

    ``worst_pct`` is the WIDER leg's bid-ask spread as a fraction of its mid
    (e.g. 0.0645 == 6.45%). See module docstring for the locked endpoints.
    """
    if worst_pct is None or worst_pct < 0:
        return 0.0
    if worst_pct <= good_pct:
        return 1.0
    if worst_pct <= acceptable_pct:
        # Linear 0.8 .. 0.6 across (good_pct, acceptable_pct]
        return _interp(worst_pct, good_pct, acceptable_pct, 0.8, 0.6)
    if worst_pct <= poor_pct:
        # Linear 0.5 .. 0.2 across (acceptable_pct, poor_pct]
        return _interp(worst_pct, acceptable_pct, poor_pct, 0.5, 0.2)
    return 0.0


def absolute_score(
    worst_abs: float | None,
    *,
    max_abs_cap: float = DEFAULT_MAX_ABS_CAP,
) -> float:
    """Legacy absolute-cap score: 1.0 at $0 spread, 0.0 at >= cap, linear between.

    Reproduces the old ``_bid_ask_quality_score`` behavior (including its
    cap<=0 -> 0.5 branch). Set ``max_abs_cap`` to 0.20 to reproduce the
    Phase 4.1 default exactly.
    """
    if worst_abs is None:
        return 0.0
    if max_abs_cap <= 0:
        return 0.5
    return max(0.0, min(1.0, 1.0 - (worst_abs / max_abs_cap)))


def bid_ask_quality(
    *,
    worst_abs: float | None,
    worst_pct: float | None,
    mode: str = "relative",
    good_pct: float = DEFAULT_GOOD_PCT,
    acceptable_pct: float = DEFAULT_ACCEPTABLE_PCT,
    poor_pct: float = DEFAULT_POOR_PCT,
    max_abs_cap: float = DEFAULT_MAX_ABS_CAP,
) -> tuple[float, str, str]:
    """Compute (score, mode_used, reason) for the worst leg's bid/ask width.

    - ``worst_abs is None`` (crossed/missing-leg) -> (0.0, 'invalid',
      'crossed_or_missing_leg') — preserves today's hard-0.0 floor for quotes
      the strategy can't price.
    - ``mode == 'absolute'`` OR ``worst_pct is None`` -> absolute path so a
      missing mid never silently zeroes a valid quote.
    - otherwise -> relative path keyed on pct-of-mid.

    ``reason`` is a short snake_case CSV-safe string.
    """
    if worst_abs is None:
        return 0.0, "invalid", "crossed_or_missing_leg"
    if mode == "absolute" or worst_pct is None:
        score = absolute_score(worst_abs, max_abs_cap=max_abs_cap)
        return score, "absolute", f"abs_cap<={max_abs_cap:.2f}"
    score = relative_score(
        worst_pct,
        good_pct=good_pct,
        acceptable_pct=acceptable_pct,
        poor_pct=poor_pct,
    )
    if worst_pct < 0:
        reason = "negative_pct"
    elif worst_pct <= good_pct:
        reason = f"pct<=good_{good_pct:.2%}"
    elif worst_pct <= acceptable_pct:
        reason = f"pct<=acceptable_{acceptable_pct:.2%}"
    elif worst_pct <= poor_pct:
        reason = f"pct<=poor_{poor_pct:.2%}"
    else:
        reason = f"pct>poor_{poor_pct:.2%}"
    return score, "relative", reason


def quality_bucket(
    *,
    worst_pct: float | None,
    short_passed: bool | None,
    long_passed: bool | None,
    good_pct: float = DEFAULT_GOOD_PCT,
    acceptable_pct: float = DEFAULT_ACCEPTABLE_PCT,
    poor_pct: float = DEFAULT_POOR_PCT,
) -> tuple[str, str]:
    """Return (bucket, reason) keyed on pct-of-mid.

    Ordering (mirrors the selector-readiness precedence):
      - any leg explicitly failed validation -> 'invalid' (validator wins)
      - worst_pct is None                     -> 'unknown' (no usable leg width)
      - worst_pct <= good_pct                 -> 'good'
      - worst_pct <= acceptable_pct           -> 'acceptable'
      - worst_pct <= poor_pct                 -> 'poor'
      - worst_pct >  poor_pct                 -> 'wide'

    Phase 4.2: this keys on worst_leg_bid_ask_PCT_OF_MID, not the old absolute
    bins. ``reason`` is a short snake_case CSV-safe string.
    """
    if short_passed is False or long_passed is False:
        return "invalid", "validation_failed"
    if worst_pct is None:
        return "unknown", "no_leg_width"
    if worst_pct <= good_pct:
        return "good", f"pct<=good_{good_pct:.2%}"
    if worst_pct <= acceptable_pct:
        return "acceptable", f"pct<=acceptable_{acceptable_pct:.2%}"
    if worst_pct <= poor_pct:
        return "poor", f"pct<=poor_{poor_pct:.2%}"
    return "wide", f"pct>poor_{poor_pct:.2%}"
