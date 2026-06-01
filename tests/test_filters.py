"""Hard-filter smoke tests under the new planned/theoretical risk model."""

from __future__ import annotations

from src.risk.filters import apply_filters
from src.strategies.base import Candidate


def _make(credit: float, distance: float = 20.0, max_risk: float = 4.20) -> Candidate:
    """Build a candidate; defaults represent a 5-wide vertical with $0.80 credit.

    max_risk default is 4.20 so spread_width = max_risk + credit = $5.
    """
    rr = credit / max_risk if max_risk else 0.0
    return Candidate(
        strategy_id="t",
        side="CALL_CREDIT",
        symbol="SPX",
        expiry="2026-05-31",
        short_strike=5810.0,
        long_strike=5815.0,
        credit=credit,
        max_risk=max_risk,
        reward_risk=rr,
        breakeven=5811.0,
        distance_from_spot=distance,
    )


# ── candidate-shape filters ─────────────────────────────────────────────

def test_non_positive_credit_is_rejected():
    c = _make(credit=0.0)
    apply_filters([c], {"min_credit": 0, "account_balance": 10000, "contracts_per_trade": 1})
    assert c.rejected
    assert any("positive" in r for r in c.rejection_reasons)


def test_below_min_credit_is_rejected():
    c = _make(credit=0.20)
    apply_filters([c], {"min_credit": 0.50})
    assert c.rejected
    assert any("below floor" in r for r in c.rejection_reasons)


# ── new risk-cap filters: aggressive_paper_10k worked example ───────────

# 5-contract × 5-wide × $0.80 credit × SL_150 (= 2.5× debit)
#   theoretical max loss  = (5.00 - 0.80) × 100 × 5 = $2,100
#   planned stop risk     = ((0.80 × 2.5) - 0.80) × 100 × 5 = $600
# aggressive caps: planned 10% = $1,000; theoretical 30% = $3,000 → BOTH pass.

AGGRESSIVE_PAPER_10K_PARAMS = {
    "min_credit": 0.30,
    "min_distance_from_spot_points": 10,
    "minimum_reward_risk": 0.10,
    "account_balance": 10000,
    "contracts_per_trade": 5,
    "stop_variant": "SL_150_PERCENT_LOSS",
    "max_planned_trade_loss_percent": 0.10,
    "max_theoretical_trade_loss_percent": 0.30,
}

CONSERVATIVE_PAPER_10K_PARAMS = {
    "min_credit": 0.30,
    "min_distance_from_spot_points": 10,
    "minimum_reward_risk": 0.10,
    "account_balance": 10000,
    "contracts_per_trade": 1,
    "stop_variant": "SL_100_PERCENT_LOSS",
    "max_planned_trade_loss_percent": 0.03,
    "max_theoretical_trade_loss_percent": 0.07,
}


def test_aggressive_5x5_paper_passes_planned_gate():
    """5 contracts × $5 width × $0.80 credit × SL_150 should clear both caps."""
    c = _make(credit=0.80, max_risk=4.20)
    apply_filters([c], AGGRESSIVE_PAPER_10K_PARAMS)
    assert not c.rejected, c.rejection_reasons


def test_aggressive_5x5_paper_still_reports_theoretical_independently():
    """If only theoretical cap is lower than the trade's theoretical risk,
    the trade is rejected with a theoretical-loss reason — even though
    planned risk is fine. Confirms theoretical is a separate gate."""
    c = _make(credit=0.80, max_risk=4.20)
    params = {**AGGRESSIVE_PAPER_10K_PARAMS, "max_theoretical_trade_loss_percent": 0.05}
    apply_filters([c], params)
    assert c.rejected
    assert any("theoretical max loss" in r for r in c.rejection_reasons)
    assert not any("planned stop risk" in r for r in c.rejection_reasons)


def test_conservative_rejects_5_contract_trade_on_planned_gate():
    """Same trade sized for conservative profile (1 contract) would pass;
    forcing 5 contracts trips the tighter planned cap first."""
    c = _make(credit=0.80, max_risk=4.20)
    params = {**CONSERVATIVE_PAPER_10K_PARAMS, "contracts_per_trade": 5}
    apply_filters([c], params)
    assert c.rejected
    assert any("planned stop risk" in r for r in c.rejection_reasons)


def test_conservative_accepts_same_trade_at_1_contract():
    """The conservative profile's intended sizing fits its caps."""
    c = _make(credit=0.80, max_risk=4.20)
    apply_filters([c], CONSERVATIVE_PAPER_10K_PARAMS)
    assert not c.rejected, c.rejection_reasons


def test_baseline_no_stop_falls_back_to_theoretical_for_planned_gate():
    """With BASELINE_CASH_SETTLE the planned-risk gate uses theoretical max
    loss (safer fallback). Same trade at 5 lots = $2,100 planned, which
    exceeds the $1,000 planned cap → rejected."""
    c = _make(credit=0.80, max_risk=4.20)
    params = {**AGGRESSIVE_PAPER_10K_PARAMS, "stop_variant": "BASELINE_CASH_SETTLE"}
    apply_filters([c], params)
    assert c.rejected
    assert any("planned stop risk" in r for r in c.rejection_reasons)


def test_no_caps_configured_is_a_pass_through():
    """When neither pct nor dollar cap is set, the risk filters are no-ops."""
    c = _make(credit=0.80, max_risk=4.20)
    params = {
        "account_balance": 10000,
        "contracts_per_trade": 5,
        "stop_variant": "SL_150_PERCENT_LOSS",
        # no max_planned_*, no max_theoretical_*
    }
    apply_filters([c], params)
    assert not c.rejected, c.rejection_reasons
