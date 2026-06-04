"""Phase 9G — operator UX: friendly preset labels, balanced style, info card,
dropdown ordering, friendly "Latest test run" label, builder TP/SL fields, and
the Zσ Strat Tester wording cleanup (Scan every / Stop after scans / Running /
Latest test run; PID + Max ticks hidden in Simple).

All assertions hit PURE helpers or scan source text — no Streamlit runtime, no
execution surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.app.operator_mode as om
import src.app.profile_builder as pb
from src.config.strategy_profiles import load_profile_file

_REPO = Path(__file__).resolve().parents[1]
_STREAMLIT_SRC = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")

NEW_PRESETS = (
    "morning_5k_dynamic_tp75", "morning_2k_dynamic_no_tp",
    "eod_5k_dynamic_sl150_no_tp", "eod_5k_dynamic_sl200_no_tp",
    "morning_5k_call_tp75_control", "morning_2k_call_no_tp_control",
    "eod_5k_call_sl150_no_tp_control", "eod_5k_call_tp50_control",
    "regime_put_credit_test", "observe_dynamic_5k",
)


# ── friendly descriptions for every new preset ───────────────────────────────

@pytest.mark.parametrize("pid", NEW_PRESETS)
def test_every_new_preset_has_friendly_description(pid: str):
    d = om.profile_description(pid)
    assert d and pid not in d                 # a sentence, not the raw id
    assert d == om.PRESET_DESCRIPTIONS[pid]


# ── balanced selector style round-trips ──────────────────────────────────────

def test_balanced_selector_style():
    assert om.selector_style_to_selector("Dynamic — balanced both sides") == \
        "balanced_structure_premium_valid"
    assert om.selector_to_style("balanced_structure_premium_valid") == \
        "Dynamic — balanced both sides"


# ── dropdown ordering: dynamic FIRST, then the rest ──────────────────────────

def test_order_profiles_dynamic_first():
    ids = ["vertical_wing_no_trade", "observe_dynamic_5k", "regime_put_credit_test",
           "morning_2k_dynamic_no_tp", "morning_5k_dynamic_tp75",
           "eod_5k_call_tp50_control"]
    ordered = om.order_profiles_for_dropdown(ids)
    # the two dynamic presets come first, in PRESET_ORDER
    assert ordered[0] == "morning_5k_dynamic_tp75"
    assert ordered[1] == "morning_2k_dynamic_no_tp"
    # legacy / unknown id sorts to the tail
    assert ordered[-1] == "vertical_wing_no_trade"


def test_dropdown_label_has_badge():
    lbl = om.profile_dropdown_label("morning_5k_dynamic_tp75",
                                    "Morning 5K Dynamic — TP75", "dynamic")
    assert "Dynamic" in lbl and "Morning 5K Dynamic — TP75" in lbl


# ── side policy + TP/SL + dynamic-exit display helpers ───────────────────────

def test_side_policy_display():
    assert om.side_policy_display({"side_policy": "dynamic both sides"}) == "dynamic both sides"
    assert om.side_policy_display({"allow_call_credit": True, "allow_put_credit": False}) == "call only"
    assert om.side_policy_display({"allow_call_credit": False, "allow_put_credit": True}) == "put only"
    assert om.side_policy_display({"daily_selector": "no_trade",
                                   "allow_call_credit": True, "allow_put_credit": True}) \
        == "observe only (no trade)"


def test_tp_sl_display():
    assert om.take_profit_display({"take_profit_pct": 0.75, "take_profit_mode": "credit_capture"}) \
        == "75% of credit (credit capture)"
    assert om.take_profit_display({"take_profit_pct": None}) == "None"
    assert om.stop_loss_display({"stop_loss_pct": 1.5, "stop_loss_mode": "fixed_credit_multiple"}) \
        == "150% of credit (fixed credit multiple)"
    assert om.stop_loss_display({}) == "—"


def test_dynamic_exit_status_is_honest():
    # even "enabled" must read as configured-but-not-active (wiring deferred)
    s_on = om.dynamic_exit_status({"dynamic_exit_enabled": True, "dynamic_exit_policy": "x"})
    assert "not active yet" in s_on
    s_off = om.dynamic_exit_status({"dynamic_exit_enabled": False})
    assert "Off" in s_off


# ── full info card (Builder + Tester) ────────────────────────────────────────

def test_info_card_has_full_field_set():
    f = load_profile_file("morning_5k_dynamic_tp75").profile.to_dict()
    card = om.profile_info_fields(f)
    for k in ("Profile", "Profile ID", "Symbol", "Strategy", "Entry window",
              "Target time", "Target DTE", "Threshold", "Side policy",
              "Selector mode", "Take profit (TP)", "Stop loss (SL)",
              "Dynamic exits", "Risk profile", "Data source", "Safety"):
        assert k in card
    assert card["Side policy"] == "dynamic both sides"
    assert card["Selector mode"] == "balanced_structure_premium_valid"
    assert card["Entry window"] == "10:55–11:05 ET"
    assert card["Take profit (TP)"].startswith("75% of credit")
    assert card["Stop loss (SL)"].startswith("150% of credit")
    assert card["Safety"] == "local paper / no broker execution"


# ── friendly "Latest test run" label + running ───────────────────────────────

def test_friendly_run_label_format():
    lbl = om.friendly_run_label(strategy_id="vertical_wing_v1",
                                started_at="2026-06-02T22:31:05+00:00")
    assert lbl == "Vertical Wing · Jun 2 · 10:31 PM"


def test_friendly_run_label_prefers_profile_name():
    lbl = om.friendly_run_label(profile_name="Morning 5K Dynamic — TP75",
                                started_at="2026-06-02T10:31:00")
    assert lbl.startswith("Morning 5K Dynamic — TP75 ·")


def test_friendly_run_label_fallbacks():
    assert om.friendly_run_label(run_id="") == "No test run yet"
    assert om.short_run_id("run_20260602_223105_abcdef123456").endswith("3456")


def test_running_display():
    assert om.running_display(True) == "Yes"
    assert om.running_display(False) == "No"


# ── profile_builder: TP/SL + dynamic-exit fields ─────────────────────────────

def test_builder_registers_exit_fields():
    names = {f["name"] for f in pb.PROFILE_FIELDS}
    for n in ("stop_loss_pct", "take_profit_pct", "stop_loss_mode",
              "take_profit_mode", "dynamic_exit_enabled", "dynamic_exit_policy",
              "preset_kind", "side_policy", "target_time", "threshold_label"):
        assert n in names


def test_builder_tp_sl_basic_modes_advanced():
    basic = {f["name"] for f in pb.basic_fields()}
    assert "stop_loss_pct" in basic and "take_profit_pct" in basic
    assert pb.is_advanced("stop_loss_mode") and pb.is_advanced("dynamic_exit_enabled")
    assert pb.advanced_group_fields("Advanced exit management")
    assert pb.STOP_LOSS_PRESETS and pb.TAKE_PROFIT_PRESETS


def test_builder_builds_dict_with_exit_fields():
    base = pb.new_template_dict("builder_demo")
    vals = {"profile_id": "builder_demo", "profile_name": "Builder Demo",
            "stop_loss_pct": 1.5, "take_profit_pct": 0.75,
            "take_profit_mode": "credit_capture", "preset_kind": "dynamic"}
    d = pb.build_profile_dict(vals, base=base)
    assert pb.validate_dict(d) == []
    assert d["stop_loss_pct"] == 1.5 and d["take_profit_pct"] == 0.75


# ── Zσ Strat Tester wording cleanup (source-level) ───────────────────────────

def test_tester_uses_scan_every_not_interval():
    assert "Scan every" in _STREAMLIT_SRC
    assert "Interval (s)" not in _STREAMLIT_SRC


def test_tester_renames_max_ticks_to_stop_after_scans():
    assert "Stop after scans" in _STREAMLIT_SRC
    assert "Max ticks" not in _STREAMLIT_SRC


def test_tester_latest_run_and_running_labels():
    assert "Latest test run" in _STREAMLIT_SRC
    assert "friendly_run_label" in _STREAMLIT_SRC
    assert '"Running"' in _STREAMLIT_SRC
    # the old raw "Active" / "Run id" metrics are gone
    assert '"Active"' not in _STREAMLIT_SRC


def test_tester_pid_and_full_run_id_in_advanced():
    # PID + full run id live behind the Advanced expander now
    assert "Full run id" in _STREAMLIT_SRC
    assert "Advanced details" in _STREAMLIT_SRC


# ── no execution surface in the changed modules / presets ────────────────────

_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


def test_no_execution_tokens_in_changed_sources():
    targets = [
        _REPO / "src" / "selector" / "daily_selector.py",
        _REPO / "src" / "config" / "strategy_profiles.py",
        _REPO / "src" / "app" / "operator_mode.py",
        _REPO / "src" / "app" / "profile_builder.py",
        _REPO / "src" / "app" / "streamlit_main.py",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{path.name} contains {tok!r}"
