"""Phase 9G — backtest-derived preset stack + TP/SL + dynamic-exit schema.

Covers: the 10 new presets validate, carry the right side policy / selector /
TP / SL / metadata, contain NO secrets or execution keys, and the schema
additions are backward-compatible (existing profiles still validate; bad types
rejected). LOCAL CONFIG ONLY — nothing here executes or previews an order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.strategy_profiles import (
    StrategyProfile,
    list_profiles,
    load_profile_file,
    template_profile_dict,
    validate_profile_dict,
)

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"

DYNAMIC_PRESETS = (
    "morning_5k_dynamic_tp75", "morning_2k_dynamic_no_tp",
    "eod_5k_dynamic_sl150_no_tp", "eod_5k_dynamic_sl200_no_tp",
)
CONTROL_PRESETS = (
    "morning_5k_call_tp75_control", "morning_2k_call_no_tp_control",
    "eod_5k_call_sl150_no_tp_control", "eod_5k_call_tp50_control",
)
NEW_PRESETS = DYNAMIC_PRESETS + CONTROL_PRESETS + (
    "regime_put_credit_test", "observe_dynamic_5k",
)
LEGACY_PRESETS = (
    "vertical_wing_score_best_1dte", "vertical_wing_best_credit_1dte",
    "vertical_wing_call_only_1dte", "vertical_wing_no_trade",
)


# ── every preset (new + legacy) loads and validates ──────────────────────────

def test_all_profiles_valid():
    results = list_profiles()
    bad = [(Path(r.path).name, r.errors) for r in results if not r.ok]
    assert not bad, f"invalid profiles: {bad}"
    # all 10 new + 4 legacy are present
    ids = {r.profile.profile_id for r in results if r.ok}
    for pid in NEW_PRESETS + LEGACY_PRESETS:
        assert pid in ids, f"missing preset {pid}"


@pytest.mark.parametrize("pid", NEW_PRESETS)
def test_new_preset_loads(pid: str):
    res = load_profile_file(pid)
    assert res.ok, res.errors
    assert res.profile is not None


# ── side policy per preset class ─────────────────────────────────────────────

@pytest.mark.parametrize("pid", DYNAMIC_PRESETS)
def test_dynamic_presets_are_both_sided_balanced(pid: str):
    p = load_profile_file(pid).profile
    assert p.allow_call_credit is True
    assert p.allow_put_credit is True
    assert p.daily_selector == "balanced_structure_premium_valid"
    assert p.preset_kind == "dynamic"


@pytest.mark.parametrize("pid", CONTROL_PRESETS)
def test_control_presets_are_call_only(pid: str):
    p = load_profile_file(pid).profile
    assert p.allow_call_credit is True
    assert p.allow_put_credit is False           # puts disabled
    assert p.daily_selector == "call_credit_only"
    assert p.preset_kind == "control"


def test_put_regime_preset_is_put_only():
    p = load_profile_file("regime_put_credit_test").profile
    assert p.allow_call_credit is False          # calls disabled
    assert p.allow_put_credit is True
    assert p.daily_selector == "put_credit_only"


def test_observe_preset_never_trades():
    p = load_profile_file("observe_dynamic_5k").profile
    assert p.daily_selector == "no_trade"
    assert p.allow_call_credit is True and p.allow_put_credit is True
    assert p.preset_kind == "observe"


# ── TP / SL metadata on the presets ──────────────────────────────────────────

def test_tp_sl_values_on_key_presets():
    m = load_profile_file("morning_5k_dynamic_tp75").profile
    assert m.stop_loss_pct == 1.50 and m.take_profit_pct == 0.75
    assert m.take_profit_mode == "credit_capture"

    no_tp = load_profile_file("morning_2k_dynamic_no_tp").profile
    assert no_tp.stop_loss_pct == 1.50 and no_tp.take_profit_pct is None
    assert no_tp.take_profit_mode == "none"

    sl200 = load_profile_file("eod_5k_dynamic_sl200_no_tp").profile
    assert sl200.stop_loss_pct == 2.00 and sl200.take_profit_pct is None

    tp50 = load_profile_file("eod_5k_call_tp50_control").profile
    assert tp50.stop_loss_pct == 2.00 and tp50.take_profit_pct == 0.50


def test_presets_carry_entry_window_and_target_time():
    m = load_profile_file("morning_5k_dynamic_tp75").profile
    assert m.entry_window_start == "10:55" and m.entry_window_end == "11:05"
    assert m.target_time == "11:00" and m.threshold_label == "5k"
    e = load_profile_file("eod_5k_dynamic_sl150_no_tp").profile
    assert e.target_time == "15:15"


def test_dynamic_exit_defaults_off_everywhere():
    for pid in NEW_PRESETS:
        p = load_profile_file(pid).profile
        assert p.dynamic_exit_enabled is False      # configured-but-not-active default


# ── safety: no secrets, no execution keys in any preset file ─────────────────

_FORBIDDEN_KEYS = (
    "execution_mode", "tasty_refresh_token", "tasty_client_secret",
    "password", "client_secret", "refresh_token",
)
_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


@pytest.mark.parametrize("pid", NEW_PRESETS)
def test_preset_has_no_secrets_or_execution(pid: str):
    raw = load_profile_file(pid).raw or {}
    for k in _FORBIDDEN_KEYS:
        assert k not in raw, f"{pid} contains forbidden key {k}"
    # validation actively rejects those keys if ever added
    assert validate_profile_dict({**raw, "execution_mode": "live"})


def test_preset_files_have_no_execution_tokens():
    for pid in NEW_PRESETS:
        text = (PROFILES_DIR / f"{pid}.yaml").read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{pid}.yaml contains {tok!r}"


# ── schema additions are backward-compatible ─────────────────────────────────

def test_template_has_new_fields_and_validates():
    t = template_profile_dict("demo_9g")
    assert validate_profile_dict(t) == []
    for k in ("preset_kind", "side_policy", "threshold_label", "target_time",
              "stop_loss_pct", "stop_loss_mode", "take_profit_pct",
              "take_profit_mode", "dynamic_exit_enabled", "dynamic_exit_policy"):
        assert k in t


def test_new_fields_round_trip():
    t = template_profile_dict("demo_rt")
    t.update(preset_kind="dynamic", stop_loss_pct=1.5, take_profit_pct=0.75,
             dynamic_exit_enabled=True, target_time="15:15", threshold_label="5k")
    p = StrategyProfile.from_dict(t)
    assert p.preset_kind == "dynamic" and p.stop_loss_pct == 1.5
    assert p.take_profit_pct == 0.75 and p.dynamic_exit_enabled is True
    assert p.summary_row()["preset_kind"] == "dynamic"


def test_bad_types_rejected():
    t = template_profile_dict("demo_bad")
    t.update(stop_loss_pct="nope", dynamic_exit_enabled="yes", preset_kind=5,
             take_profit_pct="x")
    errs = validate_profile_dict(t)
    assert any("stop_loss_pct" in e for e in errs)
    assert any("dynamic_exit_enabled" in e for e in errs)
    assert any("preset_kind" in e for e in errs)
    assert any("take_profit_pct" in e for e in errs)


def test_legacy_profiles_still_valid():
    for pid in LEGACY_PRESETS:
        res = load_profile_file(pid)
        assert res.ok, res.errors
        # legacy profiles simply omit the new fields → defaults applied
        assert res.profile.preset_kind is None
        assert res.profile.dynamic_exit_enabled is False
