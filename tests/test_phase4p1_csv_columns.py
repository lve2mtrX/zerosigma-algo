"""Phase 4.1 — CSV column ordering + presence tests.

NEW columns APPEND at the end of _DEFAULT_RANKED_FIELDS — existing column
indices stay byte-identical, no reorder, no rename, no drop.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

from src.app.session_state import SessionConfig
from src.providers.quotes.types import OptionChainSnapshot
from src.risk.limits import RiskProfile
from src.strategies.base import Candidate

rs = importlib.import_module("scripts.run_scanner")


PHASE_LE_4_TAIL = (
    "quote_provider", "quote_timestamp", "quote_age_seconds",
    "quote_chain_root", "quote_root_resolution_source",
    "short_validation_passed", "short_rejection_reason",
    "long_validation_passed",  "long_rejection_reason",
    "quote_validation_passed", "quote_rejection_reason",
)

PHASE_4P1_APPENDED = (
    "score_edge", "score_edge_passed", "marginal_score",
    "spread_bid", "spread_ask", "spread_mid", "spread_width_pct_of_mid",
    "worst_leg_bid_ask_abs", "worst_leg_bid_ask_pct_of_mid",
    "quote_quality_bucket", "quote_quality_reason",
    "risk_rejection_type",
    "planned_stop_risk_dollars", "planned_stop_risk_cap_dollars",
    "planned_stop_risk_pct", "planned_stop_risk_passed",
    "theoretical_loss_cap_dollars", "theoretical_loss_passed",
    "risk_rejection_reason",
    "candidate_passes_score_threshold", "candidate_passes_score_edge",
    "candidate_passes_trade_filters", "candidate_passes_risk_filters",
    "candidate_passes_quote_filters", "candidate_is_marginal",
    "selector_eligible_base", "selector_blockers", "selector_readiness_note",
    "target_dte", "selected_expiry", "candidate_dte", "expiry_selection_reason",
)


def test_default_ranked_fields_keeps_phase_le_4_tail_intact():
    """Every Phase ≤4 column appears in _DEFAULT_RANKED_FIELDS BEFORE the
    Phase 4.1 appended block."""
    fields = list(rs._DEFAULT_RANKED_FIELDS)
    # The Phase ≤4 tail group must end before the Phase 4.1 appended group.
    last_phase4_idx = max(fields.index(c) for c in PHASE_LE_4_TAIL)
    first_phase4p1_idx = min(fields.index(c) for c in PHASE_4P1_APPENDED)
    assert last_phase4_idx < first_phase4p1_idx, (
        "Phase 4.1 columns must APPEND after every Phase ≤4 column"
    )


def test_default_ranked_fields_contains_all_phase4p1_columns():
    fields = set(rs._DEFAULT_RANKED_FIELDS)
    for col in PHASE_4P1_APPENDED:
        assert col in fields, f"missing Phase 4.1 column {col!r}"


def test_candidate_row_emits_all_phase4p1_columns():
    """End-to-end: build a Candidate, call _candidate_row, assert the
    new columns are present in the returned dict."""
    c = Candidate(
        strategy_id="vw_v1", side="CALL_CREDIT",
        symbol="SPX", expiry="2026-06-01",
        short_strike=7610.0, long_strike=7615.0,
        credit=0.95, max_risk=4.05, reward_risk=0.235,
        breakeven=7610.95, distance_from_spot=10.0,
        meta={
            "short_leg": {"bid": 0.50, "ask": 0.60, "mid": 0.55,
                          "validation_passed": True},
            "long_leg":  {"bid": 0.10, "ask": 0.15, "mid": 0.125,
                          "validation_passed": True},
            "spread_bid": 0.35, "spread_ask": 0.50, "spread_mid": 0.425,
            "spread_width": 5.0, "spread_width_pct_of_mid": 0.235,
            "worst_leg_bid_ask_abs": 0.10, "worst_leg_bid_ask_pct_of_mid": 0.18,
            "anchor_source": "put_ceiling_2k", "anchor_volume": 2100.0,
            "anchor_volume_source": "zs_exposure_series",
            "bid_ask_quality": 0.5,
            "risk_rejections": {"planned_loss_cap": {
                "type": "planned_loss_cap", "passed": True,
                "risk_dollars": 405.0, "cap_dollars": 1000.0,
                "stop_variant": "BASELINE_CASH_SETTLE", "contracts": 1,
                "reason": None,
            }},
            "planned_stop_risk_dollars":     405.0,
            "planned_stop_risk_cap_dollars": 1000.0,
            "planned_stop_risk_passed":      True,
            "risk_rejection_type":           None,
        },
    )
    c.score = 0.65
    c.score_threshold = 0.60
    c.score_gap_to_threshold = -0.05
    c.score_edge = 0.05
    c.score_edge_passed = True
    c.marginal_score = False

    chain = OptionChainSnapshot(
        underlying="SPX", spot=7600.0, expiry="2026-06-01",
        quotes=[], quote_ts=datetime(2026, 6, 1, 14, 0, tzinfo=UTC),
        provider_name="mock",
    )
    session = SessionConfig.from_profile(RiskProfile(
        name="t",
        raw={"starting_balance": 10_000, "contracts_per_trade": 1,
             "default_stop_variant": "BASELINE_CASH_SETTLE"},
    ))
    ts = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    row = rs._candidate_row(
        "vw_v1", c, session, ts, "TRADE_CALL_CREDIT",
        chain=chain, target_dte=0, available_expiries=["2026-06-01"],
    )

    # All Phase 4.1 columns present
    for col in PHASE_4P1_APPENDED:
        assert col in row, f"row missing Phase 4.1 column {col!r}"
    # Spot-check key values
    assert row["score_edge"] == 0.05
    assert row["score_edge_passed"] is True
    assert row["spread_bid"]  == 0.35
    assert row["spread_mid"]  == 0.425
    assert row["worst_leg_bid_ask_abs"] == 0.10
    assert row["quote_quality_bucket"]  == "good"
    assert row["risk_rejection_type"]   is None
    assert row["planned_stop_risk_dollars"] == 405.0
    assert row["planned_stop_risk_cap_dollars"] == 1000.0
    # Selector_eligible_base = True (all passes)
    assert row["selector_eligible_base"] is True
    # Expiry plumbing
    assert row["target_dte"] == 0
    assert row["selected_expiry"] == "2026-06-01"


def test_existing_phase_le_4_column_indices_preserved():
    """Anchor a representative Phase ≤4 column at its current index — if
    Phase 4.1 silently reordered, this test would catch it."""
    fields = list(rs._DEFAULT_RANKED_FIELDS)
    # These are the public anchors operators / downstream readers rely on.
    assert fields[0] == "ts"
    assert fields[1] == "strategy_id"
    assert fields[2] == "decision"
    assert fields[3] == "side"
    # Phase 4 last block ends at quote_rejection_reason — find it and
    # confirm everything after is Phase 4.1.
    qrr = fields.index("quote_rejection_reason")
    for col in fields[qrr + 1:]:
        assert col in PHASE_4P1_APPENDED, (
            f"unexpected column {col!r} appended after quote_rejection_reason"
        )
