"""Phase 5 — pure daily_selector unit tests. NO network, NO creds.

Exercises select_daily_trade over hand-built candidate ROW dicts (the same
shape _candidate_row writes). The scanner-integration assertions (CSV columns,
decision log, --print-candidates) live in test_phase5_scanner_selector.py.
"""

from __future__ import annotations

from src.selector.daily_selector import (
    SELECTOR_MODES,
    SelectorConfig,
    components_to_str,
    select_daily_trade,
)


def _row(**over):
    """An ELIGIBLE CALL_CREDIT row by default; override any field."""
    base = {
        "side": "CALL_CREDIT",
        "score": 0.70,
        "credit": 1.00,
        "distance_from_spot": 20.0,
        "rejected": False,
        "selector_eligible_base": True,
        "candidate_passes_trade_filters": True,
        "candidate_passes_risk_filters": True,
        "candidate_passes_quote_filters": True,
        "candidate_passes_score_threshold": True,
        "candidate_passes_score_edge": True,
        "candidate_is_marginal": False,
        "quote_validation_passed": True,
        "quote_quality_bucket": "good",
        "planned_stop_risk_pct": 0.05,
    }
    base.update(over)
    return base


def _selected_idx(res):
    return res.selected_indices[0] if res.selected_indices else None


def _cfg(**over):
    return SelectorConfig(**over)


# ── mode selection ──────────────────────────────────────────────────────────

def test_score_best_valid_picks_highest_score():
    rows = [_row(score=0.62), _row(score=0.81), _row(score=0.70)]
    res = select_daily_trade(rows, _cfg(mode="score_best_valid"))
    assert _selected_idx(res) == 1
    assert res.per_row[1]["selected_trade"] is True
    assert sum(r["selected_trade"] for r in res.per_row) == 1


def test_best_credit_valid_picks_highest_credit_tiebreak_score():
    rows = [
        _row(credit=1.50, score=0.65),
        _row(credit=2.20, score=0.66),
        _row(credit=2.20, score=0.90),   # ties credit, wins on score
    ]
    res = select_daily_trade(rows, _cfg(mode="best_credit_valid"))
    assert _selected_idx(res) == 2
    assert res.per_row[2]["selector_tiebreaker"] == "credit>score>distance"


def test_closest_wing_valid_picks_nearest_distance():
    rows = [_row(distance_from_spot=40.0), _row(distance_from_spot=10.0), _row(distance_from_spot=25.0)]
    res = select_daily_trade(rows, _cfg(mode="closest_wing_valid"))
    assert _selected_idx(res) == 1


def test_farthest_wing_valid_picks_greatest_distance():
    rows = [_row(distance_from_spot=40.0), _row(distance_from_spot=10.0), _row(distance_from_spot=25.0)]
    res = select_daily_trade(rows, _cfg(mode="farthest_wing_valid"))
    assert _selected_idx(res) == 0


def test_call_credit_only_ignores_puts():
    rows = [_row(side="PUT_CREDIT", score=0.95), _row(side="CALL_CREDIT", score=0.71)]
    res = select_daily_trade(rows, _cfg(mode="call_credit_only"))
    assert _selected_idx(res) == 1
    assert rows[_selected_idx(res)]["side"] == "CALL_CREDIT"


def test_put_credit_only_ignores_calls():
    rows = [_row(side="PUT_CREDIT", score=0.71), _row(side="CALL_CREDIT", score=0.95)]
    res = select_daily_trade(rows, _cfg(mode="put_credit_only"))
    assert _selected_idx(res) == 0
    assert rows[_selected_idx(res)]["side"] == "PUT_CREDIT"


def test_call_credit_only_no_eligible_call_is_no_trade():
    rows = [_row(side="PUT_CREDIT", score=0.95)]
    res = select_daily_trade(rows, _cfg(mode="call_credit_only"))
    assert res.selected_trade is False
    assert res.selector_no_trade_reason == "no_eligible_call_credit_candidate"


# ── side filters ──────────────────────────────────────────────────────────

def test_disabled_side_blocks_candidate():
    rows = [_row(side="CALL_CREDIT", score=0.9), _row(side="PUT_CREDIT", score=0.7)]
    res = select_daily_trade(rows, _cfg(allow_call_credit=False))
    # call row is blocked by config, put row selected
    assert res.per_row[0]["side_allowed_by_config"] is False
    assert "side_disabled_by_config" in res.per_row[0]["selector_blockers"]
    assert res.per_row[0]["selected_trade"] is False
    assert _selected_idx(res) == 1


def test_both_sides_disabled_returns_no_trade():
    rows = [_row(side="CALL_CREDIT"), _row(side="PUT_CREDIT")]
    res = select_daily_trade(rows, _cfg(allow_call_credit=False, allow_put_credit=False))
    assert res.selected_trade is False
    assert res.selector_no_trade_reason == "no_sides_allowed"
    assert all(r["side_allowed_by_config"] is False for r in res.per_row)


# ── eligibility gates ───────────────────────────────────────────────────────

def test_rejected_never_selected():
    rows = [_row(rejected=True, score=0.99), _row(score=0.65)]
    res = select_daily_trade(rows, _cfg())
    assert _selected_idx(res) == 1
    assert "rejected" in res.per_row[0]["selector_blockers"]


def test_eligible_base_false_not_selected_by_default():
    rows = [_row(selector_eligible_base=False, score=0.99), _row(score=0.65)]
    res = select_daily_trade(rows, _cfg())
    assert _selected_idx(res) == 1
    assert "not_selector_eligible_base" in res.per_row[0]["selector_blockers"]


def test_require_quote_validation_excludes_invalid():
    rows = [_row(quote_validation_passed=False, score=0.99), _row(score=0.65)]
    res = select_daily_trade(rows, _cfg(require_quote_validation=True))
    assert _selected_idx(res) == 1
    assert "quote_validation_required" in res.per_row[0]["selector_blockers"]


def test_require_score_edge_excludes_marginal():
    rows = [_row(candidate_is_marginal=True, candidate_passes_score_edge=False, score=0.99),
            _row(score=0.65)]
    res = select_daily_trade(rows, _cfg(require_score_edge=True))
    assert _selected_idx(res) == 1
    assert "score_edge_required" in res.per_row[0]["selector_blockers"]


def test_min_score_filter_blocks_below_min():
    rows = [_row(score=0.61), _row(score=0.80)]
    res = select_daily_trade(rows, _cfg(min_selector_score=0.70))
    assert _selected_idx(res) == 1
    assert "selector_score_below_min" in res.per_row[0]["selector_blockers"]


def test_min_credit_filter_blocks_below_min():
    rows = [_row(credit=0.40, score=0.95), _row(credit=1.20, score=0.70)]
    res = select_daily_trade(rows, _cfg(min_selector_credit=1.00))
    assert _selected_idx(res) == 1
    assert "selector_credit_below_min" in res.per_row[0]["selector_blockers"]


def test_distance_filters_block_outside_range():
    rows = [
        _row(distance_from_spot=5.0, score=0.95),     # below min
        _row(distance_from_spot=200.0, score=0.94),   # above max
        _row(distance_from_spot=30.0, score=0.70),    # in range
    ]
    res = select_daily_trade(rows, _cfg(
        min_selector_distance_from_spot=10.0, max_selector_distance_from_spot=100.0,
    ))
    assert _selected_idx(res) == 2
    assert "selector_distance_below_min" in res.per_row[0]["selector_blockers"]
    assert "selector_distance_above_max" in res.per_row[1]["selector_blockers"]


# ── lowest_breach_risk_valid ────────────────────────────────────────────────

def test_lowest_breach_risk_emits_transparent_components():
    rows = [
        _row(distance_from_spot=15.0, credit=1.0, planned_stop_risk_pct=0.12),
        _row(distance_from_spot=40.0, credit=0.8, planned_stop_risk_pct=0.04),
    ]
    res = select_daily_trade(rows, _cfg(mode="lowest_breach_risk_valid"))
    # row 1 is farther + lower risk → safer → selected
    assert _selected_idx(res) == 1
    comps = res.per_row[1]["selector_score_components"]
    assert set(comps) >= {"distance_component", "risk_component", "credit_component", "total", "partial"}
    assert comps["partial"] is False
    # CSV serialization round-trips to a non-empty string
    assert components_to_str(comps).startswith("{")


def test_lowest_breach_risk_missing_psr_marks_partial_not_crash():
    rows = [_row(mode_irrelevant=True, planned_stop_risk_pct=None, distance_from_spot=30.0)]
    res = select_daily_trade(rows, _cfg(mode="lowest_breach_risk_valid"))
    assert _selected_idx(res) == 0   # still selectable
    assert res.per_row[0]["selector_score_components"]["partial"] is True


# ── regime_aligned_valid ────────────────────────────────────────────────────

def test_regime_aligned_insufficient_when_missing():
    rows = [_row(score=0.9)]
    res = select_daily_trade(rows, _cfg(mode="regime_aligned_valid"), gamma_regime=None)
    assert res.selected_trade is False
    assert res.selector_no_trade_reason == "insufficient_regime_data"


def test_regime_aligned_positive_selects_best():
    rows = [_row(score=0.71), _row(score=0.88)]
    res = select_daily_trade(rows, _cfg(mode="regime_aligned_valid"), gamma_regime="positive")
    assert _selected_idx(res) == 1


def test_regime_aligned_negative_blocked():
    rows = [_row(score=0.9)]
    res = select_daily_trade(rows, _cfg(mode="regime_aligned_valid"), gamma_regime="negative")
    assert res.selected_trade is False
    assert res.selector_no_trade_reason == "regime_negative_blocked"


# ── no_trade + invariants ────────────────────────────────────────────────────

def test_no_trade_mode_selects_none():
    rows = [_row(score=0.99), _row(score=0.88)]
    res = select_daily_trade(rows, _cfg(mode="no_trade"))
    assert res.selected_trade is False
    assert res.selector_no_trade_reason == "no_trade_mode"
    assert all(r["selected_trade"] is False for r in res.per_row)


def test_preserves_all_candidates_and_at_most_one_selected():
    rows = [_row(score=s) for s in (0.62, 0.81, 0.70, 0.55)]
    res = select_daily_trade(rows, _cfg(mode="score_best_valid"))
    assert len(res.per_row) == len(rows)          # all preserved
    assert sum(r["selected_trade"] for r in res.per_row) == 1   # at most one


def test_no_eligible_candidate_is_clean_no_trade():
    rows = [_row(rejected=True), _row(selector_eligible_base=False)]
    res = select_daily_trade(rows, _cfg())
    assert res.selected_trade is False
    assert res.selector_no_trade_reason == "no_eligible_candidate"
    # candidates preserved, none hidden
    assert len(res.per_row) == 2


def test_all_modes_are_known():
    assert set(SELECTOR_MODES) == {
        "score_best_valid", "best_credit_valid", "closest_wing_valid",
        "farthest_wing_valid", "call_credit_only", "put_credit_only",
        "lowest_breach_risk_valid", "regime_aligned_valid",
        "balanced_structure_premium_valid",   # Phase 9G — dynamic both-side selection
        "no_trade",
    }


def test_module_has_no_vertical_wing_import():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "selector" / "daily_selector.py"
    text = src.read_text(encoding="utf-8")
    for line in text.splitlines():
        if ("import" in line or "from " in line) and "vertical_wing" in line:
            raise AssertionError(f"daily_selector imports vertical_wing: {line!r}")
