"""Phase 4.1 — structured risk-rejection fields stamped by the filters.

NO network, NO Tasty creds. Direct Candidate construction + apply_filters
to assert the new meta keys land alongside the existing human-readable
rejection_reasons string.
"""

from __future__ import annotations

from src.risk.filters import apply_filters
from src.strategies.base import Candidate


def _make(credit: float = 0.80, max_risk: float = 4.20, distance: float = 20.0) -> Candidate:
    rr = credit / max_risk if max_risk else 0.0
    return Candidate(
        strategy_id="t", side="CALL_CREDIT", symbol="SPX", expiry="2026-06-01",
        short_strike=5810.0, long_strike=5815.0,
        credit=credit, max_risk=max_risk, reward_risk=rr,
        breakeven=5810.0 + credit, distance_from_spot=distance,
    )


# ── The live Tasty case: PUT_CREDIT 7575/7570 with $1400 planned > $1000 cap ──

def test_live_tasty_case_planned_loss_cap_stamped():
    """Replicate the live tick: 5-wide PUT_CREDIT with credit=2.20 (close to max
    width) under BASELINE_CASH_SETTLE + 1 contract on a $10K balance with
    planned cap $1000. planned_loss = (5.00 - 2.20) * 100 * 1 = $280? No:
    BASELINE_CASH_SETTLE → planned = theoretical = max_risk * 100 * contracts.
    With 5 contracts × $2.80 max_risk = $1400.
    """
    c = _make(credit=2.20, max_risk=2.80, distance=20.0)
    params = {
        "account_balance":              10_000,
        "contracts_per_trade":          5,
        "stop_variant":                 "BASELINE_CASH_SETTLE",
        "max_planned_trade_loss_dollars": 1000,
        # No min_credit, etc. (set high enough to clear)
        "min_credit":                   0.30,
        "min_distance_from_spot_points": 10,
        "minimum_reward_risk":          0.10,
    }
    apply_filters([c], params)
    # Human-readable list is preserved
    assert c.rejected is True
    assert any("planned stop risk" in r for r in c.rejection_reasons)
    # New structured fields
    assert c.meta["risk_rejection_type"] == "planned_loss_cap"
    assert c.meta["planned_stop_risk_dollars"] == 1400.0
    assert c.meta["planned_stop_risk_cap_dollars"] == 1000.0
    assert c.meta["planned_stop_risk_passed"] is False
    # Per-cap detail dict
    detail = c.meta["risk_rejections"]["planned_loss_cap"]
    assert detail["type"]         == "planned_loss_cap"
    assert detail["passed"]       is False
    assert detail["risk_dollars"] == 1400.0
    assert detail["cap_dollars"]  == 1000.0
    assert detail["stop_variant"] == "BASELINE_CASH_SETTLE"
    assert detail["contracts"]    == 5


def test_pass_path_also_stamps_passed_true():
    """Even when the cap passes, the dict carries explicit passed=True so
    downstream consumers don't treat key presence as failure."""
    c = _make(credit=0.80, max_risk=4.20)
    params = {
        "account_balance":              10_000,
        "contracts_per_trade":          1,
        "stop_variant":                 "SL_150_PERCENT_LOSS",
        "max_planned_trade_loss_dollars": 1000,
        "max_theoretical_trade_loss_dollars": 1000,
        "min_credit": 0.30,
        "min_distance_from_spot_points": 10,
        "minimum_reward_risk": 0.10,
    }
    apply_filters([c], params)
    assert c.rejected is False
    assert c.meta["risk_rejection_type"] is None
    assert c.meta["planned_stop_risk_passed"] is True
    assert c.meta["theoretical_loss_passed"] is True


def test_both_caps_failing_records_both_entries():
    """Same candidate trips planned AND theoretical → both keys present in
    risk_rejections dict; scalar reflects the LAST one stamped."""
    c = _make(credit=2.20, max_risk=2.80)
    params = {
        "account_balance":              10_000,
        "contracts_per_trade":          5,
        "stop_variant":                 "BASELINE_CASH_SETTLE",
        "max_planned_trade_loss_dollars":      1000,
        "max_theoretical_trade_loss_dollars":  1000,
        "min_credit": 0.30,
        "min_distance_from_spot_points": 10,
        "minimum_reward_risk": 0.10,
    }
    apply_filters([c], params)
    assert "planned_loss_cap" in c.meta["risk_rejections"]
    assert "theoretical_loss_cap" in c.meta["risk_rejections"]
    assert c.meta["risk_rejections"]["planned_loss_cap"]["passed"] is False
    assert c.meta["risk_rejections"]["theoretical_loss_cap"]["passed"] is False
    # Scalar = the last cap that failed (theoretical runs after planned)
    assert c.meta["risk_rejection_type"] == "theoretical_loss_cap"


def test_no_cap_configured_still_stamps_pass():
    """When neither cap is configured, the filters are no-ops but they still
    record the actual risk dollars (so the audit always sees them)."""
    c = _make(credit=0.80, max_risk=4.20)
    params = {
        "account_balance":              10_000,
        "contracts_per_trade":          5,
        "stop_variant":                 "SL_150_PERCENT_LOSS",
        # NO max_planned_*, NO max_theoretical_*
    }
    apply_filters([c], params)
    assert c.rejected is False
    # planned still stamped (cap None, passed True, risk_dollars computed)
    detail = c.meta["risk_rejections"]["planned_loss_cap"]
    assert detail["cap_dollars"] is None
    assert detail["passed"]      is True
    assert detail["risk_dollars"] > 0
