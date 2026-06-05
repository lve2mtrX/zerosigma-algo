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
                   "--latest-days 20 --dte 0 --run-label smoke")
    assert "submit" not in cmd and "order" not in cmd


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
        "pnl_dollars,exit_reason,side\n45,TP,PUT_CREDIT\n-30,SL,CALL_CREDIT\n20,EOD,PUT_CREDIT\n",
        encoding="utf-8")
    (tmp_path / "run_config.json").write_text('{"symbol": "SPX", "stamp": "x"}', encoding="utf-8")
    res = ch.read_backtest_results(tmp_path)
    assert res["available"] is True
    m = res["metrics"]
    assert m["total_trades"] == 3
    assert m["total_pnl_dollars"] == 35.0
    assert (m["tp_count"], m["sl_count"], m["eod_count"]) == (1, 1, 1)
    assert res["run_config"].get("symbol") == "SPX"


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
