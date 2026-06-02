"""Phase 4.1 — compute_readiness tests (pure function).

NO network, NO Tasty creds. Builds Candidates directly with the meta
fields the upstream filters / candidate builders would have stamped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.selector.readiness import compute_readiness
from src.strategies.base import Candidate


@dataclass
class _S:
    """Minimal session stand-in."""
    starting_balance: float = 10000.0


def _candidate(
    *,
    score: float = 0.65,
    threshold: float = 0.60,
    rejected: bool = False,
    rejection_reasons: list[str] | None = None,
    meta: dict | None = None,
    expiry: str = "2026-06-01",
) -> Candidate:
    c = Candidate(
        strategy_id="vw_v1",
        side="CALL_CREDIT",
        symbol="SPX",
        expiry=expiry,
        short_strike=7610.0,
        long_strike=7615.0,
        credit=0.95,
        max_risk=4.05,
        reward_risk=0.95 / 4.05,
        breakeven=7610.95,
        distance_from_spot=10.0,
        meta=meta or {},
    )
    c.score = score
    c.score_threshold = threshold
    c.score_gap_to_threshold = threshold - score
    c.rejected = rejected
    c.rejection_reasons = rejection_reasons or []
    c.score_edge = score - threshold
    c.score_edge_passed = c.score_edge >= 0.02
    c.marginal_score = (score >= threshold) and (c.score_edge < 0.02)
    return c


# ── score edge ──────────────────────────────────────────────────────────

class TestScoreEdge:
    def test_eligible_above_min_edge(self):
        # score 0.65, threshold 0.60 → edge 0.05 ≥ 0.02
        c = _candidate(score=0.65, threshold=0.60, meta={
            "worst_leg_bid_ask_abs": 0.05,
            "risk_rejections": {"planned_loss_cap": {"passed": True}},
        })
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["candidate_passes_score_threshold"] is True
        assert r["candidate_passes_score_edge"]      is True
        assert r["candidate_is_marginal"]            is False
        assert r["selector_eligible_base"]           is True
        assert r["selector_readiness_note"]          == "eligible"

    def test_marginal_just_above_threshold(self):
        # score 0.6013, threshold 0.60 → edge 0.0013 < 0.02 (the live Tasty case)
        c = _candidate(score=0.6013, threshold=0.60, meta={
            "worst_leg_bid_ask_abs": 0.05,
            "risk_rejections": {"planned_loss_cap": {"passed": True}},
        })
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["candidate_passes_score_threshold"] is True
        assert r["candidate_passes_score_edge"]      is False
        assert r["candidate_is_marginal"]            is True
        assert r["selector_readiness_note"]          == "eligible_but_marginal"
        assert any("score_below_min_edge" in b for b in r["selector_blockers"])

    def test_below_threshold_blocked(self):
        c = _candidate(score=0.50, threshold=0.60, meta={
            "worst_leg_bid_ask_abs": 0.05,
            "risk_rejections": {"planned_loss_cap": {"passed": True}},
        })
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["candidate_passes_score_threshold"] is False
        assert r["selector_eligible_base"]           is False
        assert any("score_below_threshold" in b for b in r["selector_blockers"])


# ── quote quality bucket ─────────────────────────────────────────────────

class TestQuoteQualityBucket:
    def test_good_tight_quote(self):
        c = _candidate(meta={"worst_leg_bid_ask_abs": 0.05,
                             "risk_rejections": {"planned_loss_cap": {"passed": True}}})
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["quote_quality_bucket"] == "good"

    def test_acceptable_at_boundary(self):
        c = _candidate(meta={"worst_leg_bid_ask_abs": 0.20,
                             "risk_rejections": {"planned_loss_cap": {"passed": True}}})
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["quote_quality_bucket"] == "acceptable"

    def test_poor_above_acceptable(self):
        c = _candidate(meta={"worst_leg_bid_ask_abs": 0.40,
                             "risk_rejections": {"planned_loss_cap": {"passed": True}}})
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["quote_quality_bucket"] == "poor"

    def test_wide_above_poor(self):
        c = _candidate(meta={"worst_leg_bid_ask_abs": 1.00,
                             "risk_rejections": {"planned_loss_cap": {"passed": True}}})
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["quote_quality_bucket"] == "wide"

    def test_validation_failed_overrides_to_invalid(self):
        # Even with tight bid/ask, an explicit validator failure on either leg
        # forces bucket=invalid.
        c = _candidate(meta={
            "worst_leg_bid_ask_abs": 0.05,
            "short_leg": {"validation_passed": False},
            "long_leg": {"validation_passed": True},
            "risk_rejections": {"planned_loss_cap": {"passed": True}},
        })
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["quote_quality_bucket"] == "invalid"
        assert r["candidate_passes_quote_filters"] is False
        assert any("quote_invalid" in b for b in r["selector_blockers"])

    def test_unknown_when_no_data(self):
        c = _candidate(meta={
            "risk_rejections": {"planned_loss_cap": {"passed": True}},
        })
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["quote_quality_bucket"] == "unknown"
        # unknown should still allow eligibility (mock chain leaves validation None)
        assert r["candidate_passes_quote_filters"] is True


# ── risk rejection plumbing ──────────────────────────────────────────────

class TestRiskRejection:
    def test_planned_loss_cap_failed(self):
        c = _candidate(
            rejected=True,
            rejection_reasons=["planned stop risk $1400 > cap $1000 (BASELINE_CASH_SETTLE, 1 contracts)"],
            meta={
                "worst_leg_bid_ask_abs": 0.05,
                "planned_stop_risk_dollars":     1400.0,
                "planned_stop_risk_cap_dollars": 1000.0,
                "planned_stop_risk_passed":      False,
                "risk_rejection_type":           "planned_loss_cap",
                "risk_rejections": {
                    "planned_loss_cap": {
                        "type": "planned_loss_cap", "passed": False,
                        "risk_dollars": 1400.0, "cap_dollars": 1000.0,
                        "stop_variant": "BASELINE_CASH_SETTLE", "contracts": 1,
                        "reason": "planned stop risk $1400 > cap $1000 (BASELINE_CASH_SETTLE, 1 contracts)",
                    },
                },
            },
        )
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["candidate_passes_risk_filters"]    is False
        assert r["risk_rejection_type"]              == "planned_loss_cap"
        assert "planned stop risk" in (r["risk_rejection_reason"] or "")
        assert any("risk_rejected:planned_loss_cap" in b for b in r["selector_blockers"])
        assert r["selector_eligible_base"]           is False
        # planned_stop_risk_pct = 1400 / 10000 = 0.14
        assert r["planned_stop_risk_pct"] == 0.14

    def test_theoretical_loss_cap_failed(self):
        c = _candidate(
            rejected=True,
            rejection_reasons=["theoretical max loss $2100 > cap $1500 (1 contracts)"],
            meta={
                "worst_leg_bid_ask_abs": 0.05,
                "theoretical_loss_dollars":     2100.0,
                "theoretical_loss_cap_dollars": 1500.0,
                "theoretical_loss_passed":      False,
                "risk_rejection_type":          "theoretical_loss_cap",
                "risk_rejections": {
                    "theoretical_loss_cap": {
                        "type": "theoretical_loss_cap", "passed": False,
                        "risk_dollars": 2100.0, "cap_dollars": 1500.0,
                        "contracts": 1, "stop_variant": None,
                        "reason": "theoretical max loss $2100 > cap $1500 (1 contracts)",
                    },
                },
            },
        )
        r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.02)
        assert r["risk_rejection_type"] == "theoretical_loss_cap"
        assert r["candidate_passes_risk_filters"] is False


# ── expiry plumbing ──────────────────────────────────────────────────────

class TestExpiryFields:
    def test_candidate_dte_positive(self):
        c = _candidate(expiry="2026-06-03",
                       meta={"worst_leg_bid_ask_abs": 0.05,
                             "risk_rejections": {"planned_loss_cap": {"passed": True}}})
        r = compute_readiness(
            c, session=_S(), threshold=0.60, min_score_edge=0.02,
            target_dte=2, available_expiries=["2026-06-03"],
            today_et=date(2026, 6, 1),
        )
        assert r["target_dte"] == 2
        assert r["selected_expiry"] == "2026-06-03"
        assert r["candidate_dte"] == 2

    def test_expiry_selection_reason_explicit_from_caller(self):
        c = _candidate(expiry="2026-06-03",
                       meta={"worst_leg_bid_ask_abs": 0.05,
                             "risk_rejections": {"planned_loss_cap": {"passed": True}}})
        r = compute_readiness(
            c, session=_S(), threshold=0.60, min_score_edge=0.02,
            target_dte=2,
            today_et=date(2026, 6, 1),
            expiry_selection_reason="after_hours_roll",
        )
        assert r["expiry_selection_reason"] == "after_hours_roll"


# ── min_score_edge env override (the scanner-side knob) ──
def test_min_score_edge_env_override():
    """The MIN_SCORE_EDGE env value flows in as the function arg from the
    scanner; here we just confirm a different value changes the verdict."""
    c = _candidate(score=0.6013, threshold=0.60,
                   meta={"worst_leg_bid_ask_abs": 0.05,
                         "risk_rejections": {"planned_loss_cap": {"passed": True}}})
    # With min_score_edge=0.001 the 0.0013 candidate passes
    r = compute_readiness(c, session=_S(), threshold=0.60, min_score_edge=0.001)
    assert r["candidate_passes_score_edge"] is True
    assert r["candidate_is_marginal"]       is False
