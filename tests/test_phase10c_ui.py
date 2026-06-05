"""Phase 10C — full trader UX audit: Simple-Mode jargon purge, Runner→Test Status,
after-hours DTE preview, Strategy Builder clarity (Check Strategy Setup / Enabled /
Data Source), discoverable Backtests, and corridor explainer.

Two layers:
  * pure-helper tests on ``operator_mode`` + ``cockpit_helpers`` (deterministic,
    stdlib-only); and
  * source-level wiring tests on ``streamlit_main`` that confirm the trader copy
    is rendered and dev jargon is gated to Advanced — without importing Streamlit.

No order/execution surface may be introduced.
"""

from __future__ import annotations

from pathlib import Path

from src.app import cockpit_helpers as ch
from src.app import operator_mode as om

_REPO = Path(__file__).resolve().parents[1]
_SRC = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")

# The Simple-Mode candidate detail body (between the two helper defs) — used to
# prove no dev jargon leaks into Simple Mode.
_SIMPLE_FN = (_SRC.split("def _render_candidate_simple", 1)[1]
              .split("def _render_candidate_advanced", 1)[0])


# ── Task E — after-hours DTE preview (helper is unit-testable; never mutates) ──

class _ET:
    """Minimal stand-in for an ET datetime (only .hour is read)."""

    def __init__(self, hour: int) -> None:
        self.hour = hour


def test_resolve_preview_dte_rolls_0dte_after_close_only():
    # RTH (before 17:00) → profile DTE unchanged
    assert om.resolve_preview_dte(_ET(10), 0) == 0
    assert om.resolve_preview_dte(_ET(16), 0) == 0
    # after 17:00 ET, pre-midnight → 0DTE previews 1DTE
    assert om.resolve_preview_dte(_ET(17), 0) == 1
    assert om.resolve_preview_dte(_ET(23), 0) == 1
    # a 1DTE profile is never rolled
    assert om.resolve_preview_dte(_ET(18), 1) == 1
    # non-live-preview mode never rolls
    assert om.resolve_preview_dte(_ET(18), 0, mode="backtest") == 0


def test_after_hours_preview_active_flag():
    assert om.after_hours_preview_active(_ET(18), 0) is True
    assert om.after_hours_preview_active(_ET(10), 0) is False
    assert om.after_hours_preview_active(_ET(18), 1) is False


def test_after_hours_banner_says_preview_only_unchanged_profile():
    msg = om.after_hours_preview_banner("SPX", 0)
    assert "SPX" in msg
    assert "1DTE" in msg
    assert "unchanged" in msg.lower()
    assert "stale" in msg.lower()


def test_dte_label():
    assert om.dte_label(0) == "0DTE"
    assert om.dte_label(1) == "1DTE"
    assert om.dte_label(None) == "—"


# ── Task A/C — friendly candidate labels (no dev jargon) ──────────────────────

def test_anchor_label_friendly():
    assert om.anchor_label("put_ceiling_2k") == "Put Ceiling 2K"
    assert om.anchor_label("call_floor_5k") == "Call Floor 5K"
    assert om.anchor_label("put_ceiling_10k") == "Put Ceiling 10K"
    assert om.anchor_label(None) == "—"


def test_candidate_quote_status_label():
    assert om.candidate_quote_status_label(
        {"validation_passed": True}, {"validation_passed": True}) == "Available"
    assert om.candidate_quote_status_label(
        {"validation_passed": False}, {"validation_passed": True},
        top_blocker="stale") == "Stale"
    assert om.candidate_quote_status_label(
        {"validation_passed": False}, {"validation_passed": True},
        top_blocker="spread_abs") == "Validation Blocked"


def test_candidate_risk_and_blocker_labels():
    assert om.candidate_risk_status_label(None) == "OK"
    assert om.candidate_risk_status_label("theoretical_max_loss") == "Blocked: risk cap"
    assert om.candidate_blocker_label(eligible_base=True) == "—"
    assert om.candidate_blocker_label(
        quote_state="chain_returned_validation_failed", top_blocker="stale") == "stale quotes"
    assert om.candidate_blocker_label(risk_rejection_type="x") == "risk cap"
    assert om.candidate_blocker_label(rejected=True) == "filters"


# ── Task B — Runner → Test Status; humanized control wording ──────────────────

def test_test_status_label():
    assert om.test_status_label("stopped") == "Stopped"
    assert om.test_status_label("running") == "Running"


def test_humanize_runner_message_drops_runner_word():
    out = om.humanize_runner_message("a runner is already active (pid 5). Stop it first.")
    assert "runner" not in out.lower()
    assert "paper test" in out.lower()


# ── Task F — Strategy Builder: Check Setup + Enabled curation ──────────────────

def test_check_strategy_setup_button_label():
    assert om.BTN_VALIDATE == "Check Strategy Setup"
    assert om.button_labels()["validate"] == "Check Strategy Setup"


def test_enabled_curates_simple_list_with_safe_fallback():
    # all disabled → fall back to the full Main list (never empty)
    all_off = [{"profile_id": "a", "preset_kind": "dynamic", "enabled": False},
               {"profile_id": "b", "preset_kind": "dynamic", "enabled": False}]
    assert set(om.simple_mode_profile_ids(all_off)) == {"a", "b"}
    # one enabled → curate to just the enabled Main profile(s)
    one_on = [{"profile_id": "a", "preset_kind": "dynamic", "enabled": True},
              {"profile_id": "b", "preset_kind": "dynamic", "enabled": False}]
    assert om.simple_mode_profile_ids(one_on) == ["a"]


# ── Task G — backtest command + local results reader ──────────────────────────

def test_backtest_command_is_read_only_cli():
    cmd = om.backtest_command("spx", "all-main", 20, 0, "smoke")
    assert cmd == ("python -m scripts.backtest_run --symbol SPX --profile all-main "
                   "--latest-days 20 --dte 0 --run-label smoke "
                   "--starting-balance 10000 --contracts 1")
    assert "submit" not in cmd and "order" not in cmd


def test_backtest_command_custom_sizing():
    cmd = om.backtest_command("SPX", "all-main", 5, 0, "sizing_5lot", 2500, 5)
    assert "--starting-balance 2500" in cmd
    assert "--contracts 5" in cmd


def test_read_backtest_results_missing_dir_is_graceful(tmp_path):
    res = ch.read_backtest_results(tmp_path / "does_not_exist")
    assert res["available"] is False
    assert "No backtest results" in res["reason"]


def test_read_backtest_results_empty_dir_is_graceful(tmp_path):
    res = ch.read_backtest_results(tmp_path)
    assert res["available"] is False
    assert "trades.csv" in res["reason"]


def test_read_backtest_results_reads_trades(tmp_path):
    (tmp_path / "trades.csv").write_text(
        "date,profile_id,symbol,pnl_dollars,exit_reason,side,short_strike,long_strike,"
        "entry_credit_dollars,exit_debit_dollars,contracts,corridor_valid,wds_tier,"
        "score,selector_score\n"
        "2026-06-01,p1,SPX,45,TP,PUT_CREDIT,7570,7565,120,75,1,True,1,0.72,0.81\n"
        "2026-06-02,p1,SPX,-30,SL,CALL_CREDIT,7600,7605,120,150,1,False,2,0.61,0.44\n"
        "2026-06-03,p1,SPX,20,EOD,PUT_CREDIT,7570,7565,120,100,1,True,1,0.65,0.50\n",
        encoding="utf-8")
    (tmp_path / "candidates.csv").write_text(
        "selector_blockers,risk_rejection_type,quote_quality_reason\n"
        "score_below_threshold,,\n"
        ",planned_loss,\n",
        encoding="utf-8")
    (tmp_path / "no_trade_reasons.csv").write_text(
        "date,profile_id,reason,first_blocker,candidate_count,eligible_candidate_count\n"
        "2026-06-04,p1,no_selection,score_below_threshold,2,0\n",
        encoding="utf-8")
    (tmp_path / "run_config.json").write_text(
        '{"symbol": "SPX", "stamp": "x", "profiles": ["p1"], '
        '"counters": {"dates_evaluated": 4, "valid_entry_snapshots": 4, "candidates": 5}}',
        encoding="utf-8")
    res = ch.read_backtest_results(tmp_path)
    assert res["available"] is True
    m = res["metrics"]
    assert m["total_trades"] == 3
    assert m["total_pnl_dollars"] == 35.0
    assert (m["tp_count"], m["sl_count"], m["eod_count"]) == (1, 1, 1)
    assert res["run_config"].get("symbol") == "SPX"
    assert res["trade_rows"][0]["Side"] == "Put Credit"
    assert res["trade_rows"][0]["P&L"] == "$45.00"
    assert res["explainability"]["low_trade_count"] is True
    assert "selected trades" in res["explainability"]["summary"]
    assert res["explainability"]["top_reasons"][0]["reason"] == "score_below_threshold"


# ── source-level wiring (no Streamlit import) ─────────────────────────────────

# Task A — Simple-Mode candidate detail has NO developer jargon.
def test_simple_candidate_detail_has_no_dev_jargon():
    for tok in ("score_edge", "quote_quality_bucket", "bid_ask_quality", "clock skew",
                "Clock skew", "Skew (s)", "Phase 4.1", "Phase 4.2", "strict_target_dte",
                "selector_blockers", "Threshold", "Gap", "st.json"):
        assert tok not in _SIMPLE_FN, f"Simple candidate detail leaks dev term {tok!r}"
    # it DOES use the trader-facing labels
    for tok in ("Quote Status", "Risk Status", "Blocker", "Anchor"):
        assert tok in _SIMPLE_FN


def test_candidate_table_dev_columns_are_advanced_only():
    # the raw columns are added under `if not simple_mode:` (a row.update block)
    assert 'if not simple_mode:\n                row.update({' in _SRC
    # Simple columns are present unconditionally
    for col in ('"Quote Status"', '"Risk Status"', '"Blocker"', '"Anchor"', '"Anchor Vol"'):
        assert col in _SRC


# Task B — Test Status wired; force-stop + PID are Advanced-only.
def test_test_status_and_pid_force_stop_gated():
    assert 'metric("Test Status"' in _SRC
    assert 'metric("Runner"' not in _SRC
    assert "Clear stale test" in _SRC
    # force-stop checkbox lives under `if not simple_mode:` and uses the friendly label
    assert "om.BTN_FORCE_STOP" in _SRC
    assert om.BTN_FORCE_STOP == "⏹ Force stop local test process"
    # PID metric only in the Advanced (non-simple) branch
    assert 'metric("PID"' in _SRC


# Task D — corridor plain-English explainer.
def test_corridor_explainer_present():
    assert "10K call floor" in _SRC and "10K put ceiling" in _SRC
    assert "CW1 (10K call floor)" in _SRC and "is below spot AND the" in _SRC


# Task E — after-hours preview wired into Live Cockpit + Run Strategy.
def test_after_hours_preview_wired():
    assert "AFTER_HOURS_PREVIEW" in _SRC
    assert "om.resolve_preview_dte(" in _SRC
    assert "om.after_hours_preview_banner(" in _SRC
    # Phase 10C follow-up — the after-hours card labels the quote-chain DTE explicitly.
    assert "Profile DTE" in _SRC and "Quote chain" in _SRC


# Task F — Check Strategy Setup + data-source clarity.
def test_strategy_builder_clarity():
    assert "Check Strategy Setup" in _SRC
    assert "not run or trade" in _SRC          # the explainer (split across literals)
    assert "Profile default data source" in _SRC
    assert "Current run source:" in _SRC
    # mismatch warning when the profile default differs from the live app source
    assert "was created with" in _SRC and "current app source is" in _SRC
    # the bare/misleading "Data source" radio label is gone from the Simple builder
    assert 'radio("Data source"' not in _SRC


# Task G — discoverable Backtests tab (local-only; never live/broker).
def test_backtests_tab_discoverable_and_local_only():
    assert om.BACKTESTS_TAB == "Backtests"
    assert "📈 Backtests" in " ".join(om.tab_labels())
    assert "def render_backtests()" in _SRC
    assert "render_backtests()" in _SRC          # wired into a tab body
    assert "ch.read_backtest_results(" in _SRC
    assert "om.backtest_command(" in _SRC
    assert "LOCAL SNAPSHOTS · NO LIVE API · NO BROKER" in _SRC
    assert "No live API calls" in om.BACKTEST_NOTE and "No broker" in om.BACKTEST_NOTE
    # Phase 10C follow-up — the in-process Run button is the primary flow; the CLI
    # moved into a secondary "Advanced — CLI command" expander (no longer the focus).
    assert "▶ Run Backtest" in _SRC
    assert "Advanced — CLI command" in _SRC
    assert "run_backtest(" in _SRC
    for tok in ("Starting Balance", "Contracts / Lots", "Sizing preset"):
        assert tok in _SRC
    assert om.BACKTEST_SIZING_PRESETS == {
        "Small account": (2500.0, 1),
        "Standard paper": (10000.0, 1),
        "Aggressive paper": (10000.0, 5),
        "Large paper": (100000.0, 5),
    }
    for tok in ("Ending Balance", "Return %", "Max Drawdown $", "Max Drawdown %", "Contracts"):
        assert tok in _SRC
    for tok in ("Profit Factor", "Expectancy", "**Trades**", "**Why Trades Did Not Fire**",
                "**Breakdowns**", "bt_f_profile", "bt_f_side", "bt_f_exit",
                "bt_f_corridor", "bt_f_wds"):
        assert tok in _SRC
    assert "summary_by_side.csv" in (_REPO / "src" / "backtesting" / "reports.py").read_text(
        encoding="utf-8")


def test_simple_mode_friendly_label_cleanup_wired():
    assert om.friendly_enum_label("CALL_FLOOR") == "Call Floor"
    assert om.friendly_enum_label("PUT_CEILING") == "Put Ceiling"
    assert om.friendly_enum_label("balanced_structure_premium_valid") == \
        "Balanced Structure/Premium"
    assert om.friendly_enum_label("score_best_valid") == "Best Score"
    assert om.friendly_enum_label("best_credit_valid") == "Best Credit"
    assert om.friendly_enum_label("aggressive_paper_10k") == "Aggressive Paper 10K"
    assert om.friendly_enum_label("SL_150_PERCENT_LOSS") == "150% Stop"
    assert om.friendly_enum_label("SL_200_PERCENT_LOSS") == "200% Stop"
    assert om.friendly_text("Dominant wing is CALL_FLOOR 10K") == \
        "Dominant wing is Call Floor 10K"
    assert "_render_profile_info_card(om.profile_info_fields(sel_dict), simple=simple_mode)" in _SRC
    assert "Raw profile IDs and JSON are in Advanced" in _SRC


def test_settings_raw_json_hidden_in_simple_mode():
    assert "Paper lifecycle JSON is available in Advanced Mode." in _SRC
    assert "if simple_mode:" in _SRC and "st.json(PaperLifecycleConfig.from_env().to_dict()" in _SRC
    assert "format_func=(om.friendly_enum_label if simple_mode else str)" in _SRC


def test_empty_charts_are_guarded():
    assert "def _chart_ready(" in _SRC
    assert "if _chart_ready(_cum):" in _SRC
    assert "if _chart_ready(_dd_series):" in _SRC
    assert "if _chart_ready(_daily_pnls):" in _SRC
    assert "if _chart_ready(_sig):" in _SRC
    assert "if _chart_ready(_eq_vals):" in _SRC
    assert "if _chart_ready(_dd_vals):" in _SRC
    assert "if _chart_ready(_daily_vals):" in _SRC
    assert "More data will appear after runs." in _SRC


# Task I — friendly tab labels + no execution surface.
def test_all_tab_labels_trader_friendly():
    labels = om.tab_labels()
    assert len(labels) == 7
    for bad in ("Runner", "Forward Runner", "Logs / Review"):
        assert all(bad not in lbl for lbl in labels)


_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


def test_no_execution_surface_introduced():
    for name in ("streamlit_main.py", "operator_mode.py", "cockpit_helpers.py"):
        text = (_REPO / "src" / "app" / name).read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{name} contains {tok!r}"
