"""Phase 9G — balanced_structure_premium_valid daily selector.

The dynamic both-side selector must: consider only eligible candidates; pick the
better side on a transparent COMBINED score (never highest-premium-only, never
farthest-distance-only); work for CALL or PUT; respect side + risk filters; be
deterministic; and emit selector_score_components + a human explanation.

PURE selection — nothing here executes or previews an order.
"""

from __future__ import annotations

from src.config.strategy_profiles import ALLOWED_SELECTORS
from src.selector.daily_selector import (
    SELECTOR_MODES,
    SelectorConfig,
    components_to_str,
    select_daily_trade,
)

MODE = "balanced_structure_premium_valid"

_COMPONENT_KEYS = (
    "premium_score", "distance_safety_score", "structure_score",
    "maxvol_gamma_alignment_score", "quote_quality_score",
    "existing_candidate_score", "planned_risk_penalty", "total",
)


def _row(side, score, credit, dist, vol, **kw):
    d = dict(
        side=side, score=score, credit=credit, distance_from_spot=dist,
        rejected=False, selector_eligible_base=True,
        candidate_passes_trade_filters=True, candidate_passes_risk_filters=True,
        candidate_passes_quote_filters=True, candidate_passes_score_threshold=True,
        candidate_passes_score_edge=True, quote_validation_passed=True,
        quote_quality_bucket="good", planned_stop_risk_pct=0.2, anchor_volume=vol,
    )
    d.update(kw)
    return d


def _cfg(**kw):
    return SelectorConfig(mode=MODE, max_trades_per_day=1, **kw)


# A CALL with stronger structure + safer distance, vs a PUT with slightly more credit.
_CALL_STRONGER = [
    _row("CALL_CREDIT", 0.80, 1.00, 30, 9000),
    _row("PUT_CREDIT", 0.70, 1.10, 12, 3000),
]
# A PUT with clearly stronger structure + distance, vs a thin CALL.
_PUT_STRONGER = [
    _row("CALL_CREDIT", 0.60, 1.20, 10, 2000),
    _row("PUT_CREDIT", 0.85, 1.00, 35, 9500),
]


def test_mode_registered():
    assert MODE in SELECTOR_MODES
    assert MODE in ALLOWED_SELECTORS          # auto-allowed for profiles


def test_not_highest_premium_only():
    # PUT has the higher credit, but CALL wins on the combined score.
    res = select_daily_trade(_CALL_STRONGER, _cfg(), gamma_regime="positive")
    win = _CALL_STRONGER[res.selected_indices[0]]
    assert win["side"] == "CALL_CREDIT"


def test_can_select_put_side():
    res = select_daily_trade(_PUT_STRONGER, _cfg(), gamma_regime="positive")
    win = _PUT_STRONGER[res.selected_indices[0]]
    assert win["side"] == "PUT_CREDIT"


def test_emits_transparent_components():
    res = select_daily_trade(_CALL_STRONGER, _cfg(), gamma_regime="positive")
    for meta in res.per_row:
        comps = meta["selector_score_components"]
        assert comps is not None
        for k in _COMPONENT_KEYS:
            assert k in comps
        assert "weights" in comps
        # every normalized component is bounded [0, 1]
        for k in _COMPONENT_KEYS:
            if k == "total":
                continue
            assert 0.0 <= comps[k] <= 1.0
    # serializable for CSV
    s = components_to_str(res.per_row[0]["selector_score_components"])
    assert "total" in s and s.startswith("{")


def test_explanation_names_winning_side():
    res = select_daily_trade(_CALL_STRONGER, _cfg(), gamma_regime="positive")
    assert res.selector_explanation
    assert "CALL_CREDIT" in res.selector_explanation
    # explanation also stamped on the winner row reason
    win = res.per_row[res.selected_indices[0]]
    assert "Selected CALL_CREDIT" in win["selector_reason"]


def test_respects_side_filter():
    # calls disabled → only PUT eligible even though CALL scored best.
    res = select_daily_trade(_CALL_STRONGER, _cfg(allow_call_credit=False),
                             gamma_regime="positive")
    assert res.selected_indices
    assert _CALL_STRONGER[res.selected_indices[0]]["side"] == "PUT_CREDIT"


def test_respects_eligibility():
    rows = [
        _row("CALL_CREDIT", 0.9, 1.0, 30, 9000, rejected=True),       # rejected
        _row("PUT_CREDIT", 0.5, 0.8, 20, 4000, quote_validation_passed=False),  # quote fail
        _row("CALL_CREDIT", 0.7, 1.0, 25, 7000),                      # the only eligible one
    ]
    res = select_daily_trade(rows, _cfg(), gamma_regime="positive")
    assert res.selected_indices == [2]
    assert res.per_row[0]["selected_trade"] is False
    assert res.per_row[1]["selected_trade"] is False


def test_deterministic():
    a = select_daily_trade(_CALL_STRONGER, _cfg(), gamma_regime="positive")
    b = select_daily_trade(_CALL_STRONGER, _cfg(), gamma_regime="positive")
    assert a.selected_indices == b.selected_indices
    assert [m["selector_score"] for m in a.per_row] == [m["selector_score"] for m in b.per_row]


def test_no_eligible_returns_no_trade():
    rows = [_row("CALL_CREDIT", 0.9, 1.0, 30, 9000, rejected=True)]
    res = select_daily_trade(rows, _cfg(), gamma_regime="positive")
    assert res.selected_indices == []
    assert res.selector_no_trade_reason


def test_both_sides_disabled_no_trade():
    res = select_daily_trade(_CALL_STRONGER,
                             _cfg(allow_call_credit=False, allow_put_credit=False),
                             gamma_regime="positive")
    assert res.selected_indices == []


def test_weights_in_summary():
    s = _cfg().summary()
    assert "balanced_structure_premium_valid" in s
    assert "weights[" in s and "struct=" in s
