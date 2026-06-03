"""Phase 9F — operator UX cleanup, Zσ Strat Builder, Stats/Review, sandbox health,
Dashboard-style control polish.

NO network, NO credentials, NO broker execution. Pure-helper tests + import check.
"""

from __future__ import annotations

import ast
import importlib
import tempfile
from pathlib import Path

from src.app import cockpit_helpers as ch
from src.app import operator_mode as om
from src.app import ui_helpers as ui

REPO = Path(__file__).resolve().parents[1]


# ── header subtitle + tab labels ─────────────────────────────────────────────

def test_header_subtitle_no_forward_runner():
    assert "forward runner" not in om.HEADER_SUBTITLE.lower()
    assert "Zσ Strat Builder" in om.HEADER_SUBTITLE
    assert "Zσ Strat Tester" in om.HEADER_SUBTITLE
    assert om.HEADER_TITLE == "ZerσSigma Algo Cockpit"


def test_tab_labels_renamed():
    labels = om.tab_labels()
    joined = " ".join(labels)
    assert "Zσ Strat Builder" in joined          # renamed Strategy Builder
    assert "Stats / Review" in joined            # renamed Logs / Review
    assert "Forward Runner" not in joined
    assert "Logs / Review" not in joined         # no longer a visible main tab
    assert len(labels) == 6


# ── sandbox vs live symbol health ────────────────────────────────────────────

def test_sandbox_symbol_health_reports_sandbox_not_unavailable():
    v = om.symbol_health_view(symbol="SPX", sandbox=True,
                              market_data_available=False, exposures_available=False)
    assert v["market_data"] == "sandbox mock"
    assert v["exposures"] == "sandbox stub"
    assert v["eligible"] == "sandbox eligible"
    assert v["eligible_ok"] is True
    assert v["reason"] == ""                       # no alarming reason in sandbox
    assert "Sandbox uses SPX mock/stub" in v["note"]
    # the confusing "No ZerσSigma exposures and no Tasty market data" must NOT appear
    assert "No ZerσSigma exposures" not in v["reason"]


def test_live_symbol_health_reports_real_availability():
    v = om.symbol_health_view(symbol="SPY", sandbox=False,
                              market_data_available=True, exposures_available=False)
    assert v["market_data"] == "available"
    assert v["exposures"] == "unavailable"
    assert v["eligible"] == "no"
    assert v["eligible_ok"] is False
    assert "ZerσSigma exposures unavailable" in v["reason"]
    ok = om.symbol_health_view(symbol="SPX", sandbox=False,
                               market_data_available=True, exposures_available=True)
    assert ok["eligible"] == "yes" and ok["eligible_ok"] is True


def test_is_sandbox_detection():
    assert om.is_sandbox("stub", "mock") is True
    assert om.is_sandbox("zerosigma_api", "mock") is True   # mock market data → sandbox
    assert om.is_sandbox("zerosigma_api", "tastytrade") is False
    assert om.is_sandbox("stub", "tastytrade") is True


# ── preset descriptions + info fields ────────────────────────────────────────

def test_preset_descriptions_exist_for_committed_profiles():
    for pid in ("vertical_wing_score_best_1dte", "vertical_wing_best_credit_1dte",
                "vertical_wing_call_only_1dte", "vertical_wing_no_trade"):
        d = om.profile_description(pid)
        assert d and pid not in d  # a real friendly sentence, not the id
        assert d == om.PRESET_DESCRIPTIONS[pid]


def test_unknown_profile_gets_generic_description():
    d = om.profile_description("totally_unknown_x", {
        "symbol": "QQQ", "target_dte": 2, "allow_call_credit": True,
        "allow_put_credit": False, "daily_selector": "best_credit_valid",
        "strategy_type": "vertical_credit_spread"})
    assert "QQQ" in d and "call-credit only" in d and "best_credit_valid" in d


def test_profile_info_fields_shape():
    info = om.profile_info_fields({
        "profile_name": "Demo", "symbol": "SPX", "strategy_type": "vertical_credit_spread",
        "target_dte": 1, "allow_call_credit": True, "allow_put_credit": False,
        "daily_selector": "call_credit_only", "structure_provider": "zerosigma_api",
        "quote_provider": "tastytrade", "risk_profile": "aggressive_paper_10k",
        "enabled": True, "profile_id": "demo"})
    assert info["Side preference"] == "Calls only"
    assert info["Data source"] == "Live"
    assert info["Safety"] == "local paper / no broker execution"
    assert "Designed to test" in info


# ── button labels + active profile + runner busy ─────────────────────────────

def test_button_label_helpers():
    labels = om.button_labels()
    assert labels["start"] == "▶ Start local paper test"
    assert labels["clear_stale"] == "🧹 Clear stale runner"
    assert labels["record_manual"] == "Record manual paper trade"
    assert labels["apply_session"] == "Apply local session settings"
    # verb-first (after the emoji)
    for key in ("new", "edit", "clone", "load"):
        assert labels[key].split()[0][0].isupper()


def test_active_profile_display():
    assert om.active_profile_display("(none)") == "No active profile selected"
    assert om.active_profile_display(None) == "No active profile selected"
    assert om.active_profile_display("") == "No active profile selected"
    assert om.active_profile_display("vertical_wing_no_trade") == "vertical_wing_no_trade"


def test_runner_busy_message():
    m = om.runner_busy_message("vertical_wing_no_trade", "running")
    assert "already running for vertical_wing_no_trade" in m
    assert "Stop it before starting another" in m
    assert "No active profile selected" in om.runner_busy_message("(none)", "stopping")


def test_friendly_log_label_eod():
    assert om.friendly_log_label("eod_summary.json") == "EOD summary"


# ── strategy stats aggregation (graceful empties) ────────────────────────────

def test_stats_helpers_graceful_empty():
    d = tempfile.mkdtemp()
    assert ch.latest_run_stats(forward_root=d, portfolio_root=d)["has_data"] is False
    hist = ch.historical_stats(forward_root=d, portfolio_root=d)
    assert hist["has_data"] is False and hist["runs_found"] == 0 and hist["paper_trades"] == 0
    assert ch.common_no_trade_reasons(forward_root=d) == []
    assert ch.eod_export_file(d)["exists"] is False
    assert ch.latest_best_candidate(d) is None


# ── Dashboard-style control CSS ──────────────────────────────────────────────

def test_brand_css_has_dashboard_control_styles():
    css = ui.brand_css()
    assert "<style" in css
    # pill-style selectbox + the text-input-cursor fix
    assert 'data-baseweb="select"' in css
    assert "caret-color: transparent" in css
    assert "cursor: pointer" in css
    # primary green pill + disabled state
    assert 'button[kind="primary"]' in css
    assert "linear-gradient(135deg, #00e5a8" in css
    assert ":disabled" in css and "opacity: .42" in css
    # brand accent present
    assert ui.BRAND["accent"] in css


# ── streamlit imports cleanly + header-first layout ──────────────────────────

def test_streamlit_imports_cleanly_header_first():
    m = importlib.import_module("src.app.streamlit_main")
    assert m is not None
    src = (REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")
    ast.parse(src)
    # the branded header must be rendered ABOVE the controls expander
    hero_pos = src.find("om.HEADER_SUBTITLE")
    controls_pos = src.find('"⚙  Controls & data source"')
    assert hero_pos != -1 and controls_pos != -1 and hero_pos < controls_pos
    # no visible "Forward Runner" / "Logs / Review" tab literals
    assert '"▶ Forward Runner"' not in src
    assert "om.tab_labels()" in src


def test_no_execution_surface_in_helper_modules():
    forbidden = ("submit_order", "place_order", "preview_order", "create_order",
                 "order_preview", "execute_trade", "broker.")
    for rel in ("src/app/operator_mode.py", "src/app/cockpit_helpers.py",
                "src/app/ui_helpers.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        for tok in forbidden:
            assert tok not in src, f"{rel} must not reference {tok!r}"
