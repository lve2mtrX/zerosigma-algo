"""Per-spread risk arithmetic — theoretical max loss + planned stop risk."""

from __future__ import annotations

import math

from src.risk.limits import (
    OPTION_MULTIPLIER,
    planned_loss_dollars,
    planned_loss_per_spread,
    theoretical_max_loss_dollars,
    theoretical_max_loss_per_spread,
)

# ──────────────────────────────────────────────────────────────────────
# Anchor case: 5-wide vertical, $0.80 credit ⇒ max_risk per spread $4.20
# ──────────────────────────────────────────────────────────────────────

CREDIT = 0.80
MAX_RISK = 4.20            # = spread_width(5.0) - credit(0.80)
CONTRACTS = 5


# ── theoretical max loss ────────────────────────────────────────────────

def test_theoretical_max_loss_per_spread_matches_spread_width_minus_credit():
    # 5.00 - 0.80 = 4.20 per spread
    assert math.isclose(theoretical_max_loss_per_spread(MAX_RISK), 4.20)


def test_theoretical_max_loss_dollars_for_aggressive_paper_size():
    # 4.20 × 100 × 5 = $2,100
    got = theoretical_max_loss_dollars(MAX_RISK, CONTRACTS)
    assert math.isclose(got, 2100.0)


def test_theoretical_clamped_to_zero_for_pathological_input():
    assert theoretical_max_loss_per_spread(-1.0) == 0.0


# ── planned stop risk per stop variant ─────────────────────────────────

def test_planned_stop_risk_sl100():
    # 2.0× debit ⇒ loss per spread = credit × 1.0 = 0.80
    per = planned_loss_per_spread(CREDIT, MAX_RISK, "SL_100_PERCENT_LOSS")
    assert math.isclose(per, 0.80)
    assert math.isclose(
        planned_loss_dollars(CREDIT, MAX_RISK, "SL_100_PERCENT_LOSS", CONTRACTS),
        per * OPTION_MULTIPLIER * CONTRACTS,
    )


def test_planned_stop_risk_sl150():
    # 2.5× debit ⇒ loss per spread = credit × 1.5 = 1.20
    per = planned_loss_per_spread(CREDIT, MAX_RISK, "SL_150_PERCENT_LOSS")
    assert math.isclose(per, 1.20)
    # The README's worked example: $600 for 5 contracts
    assert math.isclose(
        planned_loss_dollars(CREDIT, MAX_RISK, "SL_150_PERCENT_LOSS", CONTRACTS),
        600.0,
    )


def test_planned_stop_risk_sl200():
    # 3.0× debit ⇒ loss per spread = credit × 2.0 = 1.60
    per = planned_loss_per_spread(CREDIT, MAX_RISK, "SL_200_PERCENT_LOSS")
    assert math.isclose(per, 1.60)
    assert math.isclose(
        planned_loss_dollars(CREDIT, MAX_RISK, "SL_200_PERCENT_LOSS", CONTRACTS),
        800.0,
    )


# ── BASELINE_CASH_SETTLE fallback ──────────────────────────────────────

def test_planned_stop_risk_baseline_falls_back_to_theoretical():
    """Documented choice: no-stop variant treats planned risk = theoretical
    max loss (safer than waving the trade through with infinite implied risk)."""
    per = planned_loss_per_spread(CREDIT, MAX_RISK, "BASELINE_CASH_SETTLE")
    assert math.isclose(per, theoretical_max_loss_per_spread(MAX_RISK))
    assert math.isclose(
        planned_loss_dollars(CREDIT, MAX_RISK, "BASELINE_CASH_SETTLE", CONTRACTS),
        theoretical_max_loss_dollars(MAX_RISK, CONTRACTS),
    )


# ── safety: planned never exceeds theoretical, even with weird inputs ──

def test_planned_capped_at_theoretical_when_stop_would_exceed_width():
    # With a tiny max_risk (narrow spread), a 200%-loss stop multiple
    # would imply more loss than the spread can possibly realize.
    # planned_loss must clamp at the theoretical ceiling.
    narrow_credit = 0.40
    narrow_max_risk = 0.10  # 0.50-wide spread, 0.40 credit
    per = planned_loss_per_spread(narrow_credit, narrow_max_risk, "SL_200_PERCENT_LOSS")
    assert per <= theoretical_max_loss_per_spread(narrow_max_risk)
    assert math.isclose(per, 0.10)
