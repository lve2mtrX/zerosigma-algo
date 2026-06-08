"""Phase 10C follow-up — after-hours 1DTE quote labeling, stale-quote decision
gating (Start Paper Test disabled / no fake live "Decision"), the usable Backtests
UI runner + data discovery, and saved/custom profile visibility.

Pure-helper tests (deterministic, stdlib-only) + source-level wiring tests on
streamlit_main (no Streamlit import). No order/execution surface may appear.
"""

from __future__ import annotations

from pathlib import Path

from src.app import cockpit_helpers as ch
from src.app import operator_mode as om

_REPO = Path(__file__).resolve().parents[1]
_SRC = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


class _ET:
    def __init__(self, hour: int) -> None:
        self.hour = hour


# ── Task A — explicit 1DTE after-hours quote labeling ─────────────────────────

def test_after_hours_quote_detail():
    assert om.after_hours_quote_detail(True, 1) == "1DTE quote chain · after-hours preview"
    assert om.after_hours_quote_detail(False) is None
    assert om.after_hours_quote_detail(True, 0) == "0DTE quote chain · after-hours preview"


def test_after_hours_banner_explicit_dte_lines():
    msg = om.after_hours_preview_banner("SPX", 0)
    assert "Quote chain: 1DTE after-hours preview" in msg
    assert "Profile DTE: 0DTE" in msg
    assert "Strategy DTE unchanged" in msg
    assert "SPX" in msg and "stale" in msg.lower()


def test_after_hours_labeling_wired_in_source():
    assert "om.after_hours_quote_detail(" in _SRC          # quotes-card sub-label
    assert "Quote chain" in _SRC                           # explicit chain DTE label
    assert "Strategy DTE unchanged" in _SRC                # never mutates the profile


# ── Task B — stale-quote decision gating ──────────────────────────────────────

def test_decision_headline_live_when_available():
    dh = om.decision_headline(available=True)
    assert dh["live"] is True
    assert dh["title"] == "Decision"
    assert "cleared selector, quote, and risk gates" in dh["note"]


def test_decision_headline_stale_is_preview_only():
    dh = om.decision_headline(available=False, quote_state="chain_returned_validation_failed",
                              top_blocker="stale")
    assert dh["live"] is False
    assert dh["title"] == "No Live Decision — Quotes Stale"
    assert "preview-only" in dh["note"].lower()
    assert "stale" in dh["note"].lower()
    # must NOT claim the side cleared the gates
    assert "cleared selector" not in dh["note"]


def test_decision_headline_blocked_and_unavailable():
    blocked = om.decision_headline(available=False, quote_state="chain_returned_validation_failed",
                                   top_blocker="spread_abs")
    assert blocked["title"] == "No Live Decision — Quotes Blocked"
    assert "spread_abs" in blocked["note"]
    unavail = om.decision_headline(available=False, quote_state="chain_unavailable")
    assert unavail["title"] == "No Live Decision — Quotes Unavailable"


def test_start_test_stale_reason_constant():
    r = om.START_TEST_STALE_REASON
    assert "Cannot start live paper test" in r
    assert "stale" in r.lower() and "RTH" in r and "Sandbox" in r


def test_stale_decision_gating_wired_in_source():
    # module-level stale flags + decision headline gate
    assert "QUOTE_STALE" in _SRC and "LIVE_QUOTES_STALE" in _SRC
    assert "om.decision_headline(" in _SRC
    assert '_dh["live"]' in _SRC
    assert "Preview Candidate" in _SRC
    # Start Paper Test uses the shared all-blocker readiness gate; Preview is not.
    assert "om.paper_test_readiness(" in _SRC
    assert '_can_start = bool(_readiness["can_start"])' in _SRC
    assert "disabled=not runner_profiles or not _can_start" in _SRC
    assert "Preview only — cannot start live paper test until quotes are fresh/usable." in _SRC


# ── Task C/D — backtest data discovery + UI runner ────────────────────────────

def test_backtest_data_range_graceful_on_unknown_symbol():
    rng = ch.backtest_data_range("ZZZ_NOPE", "0DTE")
    assert rng["available"] is False
    assert rng["file_count"] == 0
    assert rng["symbol"] == "ZZZ_NOPE" and rng["dte"] == "0DTE"


def test_backtest_range_caption_formatting():
    avail = {"symbol": "SPX", "dte": "0DTE", "available": True, "file_count": 146,
             "min_date": "2025-10-31", "max_date": "2026-06-04"}
    assert ch.backtest_range_caption(avail) == "SPX 0DTE: 146 files · 2025-10-31 → 2026-06-04"
    none = {"symbol": "SPX", "dte": "1DTE", "available": False}
    assert ch.backtest_range_caption(none) == "SPX 1DTE: no local data"


def test_backtest_data_availability_shape():
    av = ch.backtest_data_availability("SPX")
    assert set(av.keys()) == {"0DTE", "1DTE"}
    assert "available" in av["0DTE"] and "file_count" in av["1DTE"]


def test_backtest_default_label():
    assert om.backtest_default_label("SPX", "all-main", "Latest N days") == "spx_all_main_latest"
    assert om.backtest_default_label("spx", "all", "All data") == "spx_all_all"
    assert om.backtest_default_label("SPY", "morning_5k_dynamic_tp75", "Date range").startswith("spy_")


def test_backtest_ui_runner_wired():
    # discovery + range surfaced
    assert "ch.backtest_data_availability(" in _SRC
    assert "ch.backtest_range_caption(" in _SRC
    # date mode controls (Latest N / Date range / All data) + calendar inputs
    assert '"Latest N days", "Date range", "All data"' in _SRC
    assert "date_input(" in _SRC
    assert "Available data for" in _SRC                    # All-data range line
    # in-process Run button + Refresh
    assert "▶ Run Backtest" in _SRC
    assert "Refresh Latest Results" in _SRC
    assert "run_backtest(" in _SRC and "resolve_profiles(" in _SRC
    assert "st.spinner(" in _SRC
    # CLI is secondary (Advanced expander), not the page focus
    assert "Advanced — CLI command" in _SRC
    assert "Run this in a terminal" not in _SRC            # old dev-mode caption gone


# ── Task E — saved / custom profile visibility ────────────────────────────────

def test_custom_profile_category():
    assert om.profile_category(None) == "Custom"
    assert om.profile_category("") == "Custom"
    assert om.profile_category("dynamic") == "Main Strategies"
    assert "Custom" in om.PROFILE_CATEGORIES
    assert "Legacy / Archived" not in om.PROFILE_CATEGORIES


def test_custom_profile_reachable_via_show_all():
    sums = [
        {"profile_id": "my_custom", "preset_kind": None, "enabled": False},
        {"profile_id": "morning_5k_dynamic_tp75", "preset_kind": "dynamic", "enabled": False},
    ]
    # default (Main only) hides the custom profile…
    assert "my_custom" not in om.simple_mode_profile_ids(sums)
    # …but "show all" surfaces it
    assert "my_custom" in om.simple_mode_profile_ids(sums, show_all=True)
    # and it lands in the Custom category
    assert om.profiles_in_category(sums, "Custom") == ["my_custom"]


def test_show_all_saved_profiles_and_invalid_surfacing_wired():
    assert _SRC.count("Show all saved profiles") >= 2     # builder + runner (+ backtests)
    assert "invalid profile(s) hidden" in _SRC            # backtests
    assert "validation errors and are" in _SRC            # run strategy


# ── Task F — no developer jargon / no execution surface introduced ────────────

def test_no_terminal_or_runner_jargon_leak():
    assert "Run this in a terminal" not in _SRC
    assert 'metric("Runner"' not in _SRC


_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


def test_no_execution_surface_introduced():
    for name in ("streamlit_main.py", "operator_mode.py", "cockpit_helpers.py"):
        text = (_REPO / "src" / "app" / name).read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{name} contains {tok!r}"
