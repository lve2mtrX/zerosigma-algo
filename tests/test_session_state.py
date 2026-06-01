"""SessionConfig must round-trip from profile, expose filter params, and diff."""

from __future__ import annotations

from src.app.session_state import EDITABLE_FIELDS, SessionConfig
from src.risk.limits import RiskProfile

_PROFILE = RiskProfile(
    name="test",
    raw={
        "label": "Test",
        "paper_only": True,
        "starting_balance": 10000,
        "contracts_per_trade": 5,
        "max_open_positions": 1,
        "max_daily_loss_percent": 0.10,
        "max_planned_trade_loss_percent": 0.10,
        "max_theoretical_trade_loss_percent": 0.30,
        "default_spread_width": 5,
        "default_stop_variant": "SL_150_PERCENT_LOSS",
        "profit_targets": [0.50, 0.75],
        "no_trade_score_threshold": 0.60,
        "minimum_credit_afternoon": 0.30,
        "max_bid_ask_width": 0.20,
        "min_distance_from_spot": 10,
        "minimum_reward_risk": 0.10,
    },
)


def test_from_profile_populates_all_editable_fields():
    s = SessionConfig.from_profile(_PROFILE)
    for f in EDITABLE_FIELDS:
        # presence — every editable field is settable on SessionConfig
        assert hasattr(s, f), f"SessionConfig missing field {f}"
    assert s.starting_balance == 10000
    assert s.contracts_per_trade == 5
    assert s.default_stop_variant == "SL_150_PERCENT_LOSS"


def test_to_filter_params_carries_planned_and_theoretical_caps():
    s = SessionConfig.from_profile(_PROFILE)
    p = s.to_filter_params()
    assert p["max_planned_trade_loss_percent"] == 0.10
    assert p["max_theoretical_trade_loss_percent"] == 0.30
    assert p["stop_variant"] == "SL_150_PERCENT_LOSS"
    assert p["account_balance"] == 10000


def test_diff_against_returns_only_changed_fields():
    base = SessionConfig.from_profile(_PROFILE)
    edited = base.clone()
    edited.contracts_per_trade = 1
    edited.no_trade_score_threshold = 0.55

    diff = edited.diff_against(base)
    assert set(diff.keys()) == {"contracts_per_trade", "no_trade_score_threshold"}
    assert diff["contracts_per_trade"] == (5, 1)
    assert diff["no_trade_score_threshold"] == (0.60, 0.55)


def test_clone_is_independent():
    a = SessionConfig.from_profile(_PROFILE)
    b = a.clone()
    b.profit_targets.append(0.90)
    assert 0.90 not in a.profit_targets
