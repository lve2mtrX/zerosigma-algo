"""Phase 4.1 — score_edge / score_edge_passed / marginal_score plumbing.

Tests both:
  - the VW strategy stamps the fields onto every Candidate during select()
  - MIN_SCORE_EDGE env override changes the verdict
  - decision branches are NOT altered (observability only)
"""

from __future__ import annotations

from src.strategies.base import Candidate, StrategyDecision
from src.strategies.vertical_wing.strategy import VerticalWingV1


def _make(side: str, score: float, *, rejected: bool = False) -> Candidate:
    c = Candidate(
        strategy_id="vw_v1",
        side=side,                          # type: ignore[arg-type]
        symbol="SPX", expiry="2026-06-01",
        short_strike=7610.0 if side == "CALL_CREDIT" else 7575.0,
        long_strike=7615.0  if side == "CALL_CREDIT" else 7570.0,
        credit=0.95, max_risk=4.05, reward_risk=0.235,
        breakeven=7610.95, distance_from_spot=10.0,
    )
    c.score = score
    c.rejected = rejected
    return c


def test_marginal_candidate_at_0013_edge_flagged_marginal():
    """The live Tasty case: score=0.6013, threshold=0.60 → edge=0.0013 <
    MIN_SCORE_EDGE=0.02 → marginal_score=True, score_edge_passed=False."""
    strat = VerticalWingV1()
    c = _make("CALL_CREDIT", 0.6013)
    decision = strat.select([c], {"no_trade_score_threshold": 0.60})
    assert isinstance(decision, StrategyDecision)
    assert c.score_edge is not None
    assert abs(c.score_edge - 0.0013) < 1e-9
    assert c.score_edge_passed is False
    assert c.marginal_score is True
    # Decision branch IS NOT altered — the candidate STILL gets selected
    # (it's above threshold, not rejected, and the only one).
    assert decision.decision == "TRADE_CALL_CREDIT"


def test_well_above_threshold_not_marginal():
    """score=0.8259 - the PUT_CREDIT live case before risk-cap rejection."""
    strat = VerticalWingV1()
    c = _make("PUT_CREDIT", 0.8259)
    decision = strat.select([c], {"no_trade_score_threshold": 0.60})
    assert abs(c.score_edge - 0.2259) < 1e-9
    assert c.score_edge_passed is True
    assert c.marginal_score    is False
    assert decision.decision == "TRADE_PUT_CREDIT"


def test_below_threshold_no_marginal_flag():
    strat = VerticalWingV1()
    c = _make("CALL_CREDIT", 0.50)
    strat.select([c], {"no_trade_score_threshold": 0.60})
    assert c.score_edge is not None
    assert c.score_edge < 0
    assert c.score_edge_passed is False
    assert c.marginal_score    is False     # not even above threshold


def test_min_score_edge_env_relaxes_verdict(monkeypatch):
    """MIN_SCORE_EDGE=0.001 turns the 0.0013-edge case into score_edge_passed=True."""
    monkeypatch.setenv("MIN_SCORE_EDGE", "0.001")
    strat = VerticalWingV1()
    c = _make("CALL_CREDIT", 0.6013)
    strat.select([c], {"no_trade_score_threshold": 0.60})
    assert c.score_edge_passed is True
    assert c.marginal_score    is False


def test_garbage_min_score_edge_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MIN_SCORE_EDGE", "not-a-number")
    strat = VerticalWingV1()
    c = _make("CALL_CREDIT", 0.6013)
    strat.select([c], {"no_trade_score_threshold": 0.60})
    # Default 0.02 applies → still marginal
    assert c.marginal_score is True


def test_decision_not_altered_for_marginal_candidate():
    """Phase 4.1 spec: the TRADE/NO_TRADE decision must NOT change based on
    score_edge_passed. A marginal candidate that's the only survivor still
    yields a TRADE decision."""
    strat = VerticalWingV1()
    c = _make("CALL_CREDIT", 0.6013)
    decision = strat.select([c], {"no_trade_score_threshold": 0.60})
    assert decision.decision in ("TRADE_CALL_CREDIT", "TRADE_PUT_CREDIT")
    assert c.rejection_type == "selected"
