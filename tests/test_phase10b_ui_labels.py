"""Phase 10B UI hotfix — trader-first cockpit labels + Run Strategy workflow +
stale-quote clarity.

Two layers:
  * pure-helper tests on ``operator_mode`` (raw enum → short trader copy), which
    are deterministic and stdlib-only; and
  * source-level wiring tests on ``streamlit_main`` that confirm the new copy is
    actually rendered (Best Eligible vs Preview, Run-a-Strategy panel, read-only
    header, raw IDs gated to Advanced) without importing Streamlit.

Guard rails: the long ``cockpit_quote_status['label']`` is *not* touched here
(it is pinned by test_phase10b_cockpit_quote_status.py); these are NEW short
labels. No execution surface is introduced.
"""

from __future__ import annotations

from pathlib import Path

from src.app import operator_mode as om

_REPO = Path(__file__).resolve().parents[1]
_SRC = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


# ── 1. friendly status-card labels (raw IDs → short copy) ────────────────────

def test_decision_label_humanizes_raw_enums():
    assert om.decision_label("TRADE_CALL_CREDIT") == "Call Credit"
    assert om.decision_label("CALL_CREDIT") == "Call Credit"
    assert om.decision_label("TRADE_PUT_CREDIT") == "Put Credit"
    assert om.decision_label("PUT_CREDIT") == "Put Credit"
    assert om.decision_label("NO_TRADE") == "No Trade"
    assert om.decision_label(None) == "—"
    assert om.decision_label("") == "—"
    # side_label is an alias
    assert om.side_label("PUT_CREDIT") == "Put Credit"


def test_provider_short_humanizes_provider_ids():
    assert om.provider_short("zerosigma_api") == "Zσ API"
    assert om.provider_short("tastytrade") == "Tasty"
    assert om.provider_short("mock") == "Mock"
    assert om.provider_short("null") == "Manual"
    assert om.provider_short(None) == "—"


def test_runner_state_label_title_cases():
    assert om.runner_state_label("stopped") == "Stopped"
    assert om.runner_state_label("running") == "Running"
    assert om.runner_state_label("stopping") == "Stopping"
    assert om.runner_state_label(None) == "Stopped"


# ── 1b. quote state → short label, stale-aware ───────────────────────────────

def test_quote_state_label_stale_vs_validation_split():
    # validation-failed + stale top blocker → "Stale" (the after-hours case)
    assert om.quote_state_label("chain_returned_validation_failed", "stale") == "Stale"
    # validation-failed + a spread/width blocker → "Validation Blocked"
    assert om.quote_state_label("chain_returned_validation_failed", "spread_abs") == "Wide"
    # the long pinned label is unchanged; this is the SHORT card label only
    assert om.quote_state_label("chain_returned_usable") == "Available"
    assert om.quote_state_label("chain_returned_stale") == "Stale"
    assert om.quote_state_label("quote_request_skipped") == "No Strikes"
    assert om.quote_state_label("chain_returned_missing_required_strikes") == "Missing Strikes"
    assert om.quote_state_label("chain_unavailable") == "No Chain"
    assert om.quote_state_label("mock") == "Sandbox"


def test_quote_state_label_never_returns_raw_enum():
    # Simple Mode must never show a clipped raw enum like
    # "chain_returned_validation_failed" in a status card.
    for state in (
        "chain_returned_validation_failed", "chain_returned_usable",
        "chain_returned_stale", "quote_request_skipped",
        "chain_returned_missing_required_strikes", "chain_resolved_quotes_unavailable",
        "chain_unavailable", "not_configured", "auth_failed", "root_unresolved",
        "expiration_unavailable", "mock", "unknown_error",
    ):
        label = om.quote_state_label(state, "stale")
        assert "_" not in label, f"{state} leaked a raw enum into the card: {label!r}"
        assert label  # non-empty


# ── 2. quote-status banners (stale clarity) ──────────────────────────────────

def test_quote_state_banner_stale_message():
    msg = om.quote_state_banner("chain_returned_validation_failed", "SPX", "stale")
    assert "stale" in msg.lower()
    assert "preview" in msg.lower()
    assert "RTH" in msg
    # usable / sandbox → no banner (nothing to warn about)
    assert om.quote_state_banner("chain_returned_usable", "SPX") is None
    assert om.quote_state_banner("mock", "SPX") is None
    ah = om.quote_state_banner("chain_returned_stale", "SPX", after_hours=True)
    assert ah == "After-hours: chain returned, quotes stale. Preview only."


def test_quote_state_banner_validation_nonstale_names_reason():
    msg = om.quote_state_banner("chain_returned_validation_failed", "SPX", "spread_abs")
    assert "validation" in msg.lower()
    assert "spread_abs" in msg


def test_stale_quote_mode_banner_mentions_symbol_and_preview():
    msg = om.stale_quote_mode_banner("SPX")
    assert "SPX" in msg
    assert "stale" in msg.lower()
    assert "preview" in msg.lower()


# ── 3 + 8. friendly candidate labels + status pills ──────────────────────────

def test_candidate_label_formats_side_and_strikes():
    assert om.candidate_label("PUT_CREDIT", 7550, 7545) == "Put Credit 7550/7545"
    assert om.candidate_label("CALL_CREDIT", 7600.0, 7605.0) == "Call Credit 7600/7605"
    # non-numeric strikes degrade gracefully (never a traceback in the UI)
    assert om.candidate_label("PUT_CREDIT", None, None) == "Put Credit —/—"


def test_candidate_status_label_pills():
    # eligible base case
    assert om.candidate_status_label(eligible_base=True) == "Eligible"
    # stale quotes win over generic "rejected"
    assert om.candidate_status_label(
        quote_state="chain_returned_validation_failed", top_blocker="stale",
    ) == "Blocked: stale quotes"
    # non-stale validation failure
    assert om.candidate_status_label(
        quote_state="chain_returned_validation_failed", top_blocker="spread_abs",
    ) == "Blocked: quote validation"
    assert om.candidate_status_label(
        quote_state="chain_returned_missing_required_strikes",
    ) == "Blocked: missing strikes"
    # risk cap
    assert om.candidate_status_label(risk_rejection_type="theoretical_max_loss") == "Blocked: risk cap"
    # plain filter rejection
    assert om.candidate_status_label(rejected=True) == "Blocked: filters"
    # observe-only preset
    assert om.candidate_status_label(preset_kind="observe") == "Observe only"


# ── 6. read-only header status cells (7 short cards) ─────────────────────────

def test_header_status_cells_shape_and_labels():
    cells = om.header_status_cells(
        strategy="Vertical Wing", structure="Zσ API", quotes="Stale",
        runner="Stopped", last_signal="No Trade", paper_pnl="$0.00",
    )
    assert len(cells) == 7
    labels = [c[0] for c in cells]
    # Phase 10C — "Runner" is never user-facing; the header cell reads "Test Status".
    assert labels == ["Strategy", "Structure", "Quotes", "Test Status",
                      "Last Signal", "Paper P&L", "Safety"]
    assert "Runner" not in labels
    # Safety defaults to the no-broker assurance
    assert cells[-1] == ("Safety", "No Broker")


# ── 5 + 7. Run Strategy workflow (tab + buttons) ─────────────────────────────

def test_run_strategy_tab_label():
    labels = om.tab_labels()
    joined = " ".join(labels)
    assert "Run Strategy" in joined
    assert "Zσ Strat Tester" not in joined  # tab itself relabeled
    assert "Forward Runner" not in joined


def test_run_strategy_button_labels():
    b = om.button_labels()
    assert b["preview"] == "👁 Preview Strategy"
    assert b["start"] == "▶ Start Paper Test"
    assert b["stop"] == "■ Stop Test"
    assert b["review"] == "📄 Review Latest"


# ── source-level wiring (no Streamlit import) ────────────────────────────────

# 4. "Best Eligible Setup" only when quotes are actually usable; otherwise the
#    preview is explicitly labeled Blocked / Stale Quotes.
def test_best_eligible_gated_on_quote_availability():
    assert 'QUOTE_STATUS["available"]' in _SRC
    assert "Best Candidate Preview — Stale Quotes" in _SRC
    assert "Best Candidate Preview — Blocked" in _SRC
    # the friendly setup string is built from the candidate_label helper
    assert "om.candidate_label(best.get(" in _SRC


# 2 + 9. stale-quote banner + after-hours mode wired into symbol health/market
def test_quote_banners_wired_in_views():
    assert "om.quote_state_banner(QUOTE_STATUS" in _SRC
    assert "om.stale_quote_mode_banner(SYMBOL)" in _SRC
    assert "After-hours / stale quote mode" in _SRC


# 5. prominent "Run a Strategy" 5-step panel in the Run Strategy tab
def test_run_a_strategy_panel_present():
    assert "🧪 Run Strategy — local paper test" in _SRC
    assert "#### ▶ Run a Strategy" in _SRC
    for step in ("1. Choose strategy", "2. Confirm data source",
                 "3. Preview Strategy", "4. Start Paper Test",
                 "5. Stop Test / Review Latest"):
        assert step in _SRC
    assert "Paper test only. No broker execution. No order preview." in _SRC


# 6. header reads clearly read-only + points to the Run Strategy tab
def test_header_is_readonly_with_cta():
    assert "Status Summary — read-only" in _SRC
    assert "To run a strategy, open the **🧪 Run Strategy** tab" in _SRC
    assert "om.header_status_cells(" in _SRC


# 8. candidate rows/cards use the friendly helpers
def test_candidate_views_use_friendly_helpers():
    assert "om.candidate_label(c.side, c.short_strike, c.long_strike)" in _SRC
    assert "om.candidate_status_label(" in _SRC


# 10. raw score breakdown JSON only in Advanced Mode (Simple shows a caption)
def test_raw_breakdown_gated_to_advanced():
    # Phase 10C — Simple Mode points to Advanced; raw st.json lives in the
    # Advanced-only candidate detail helper.
    assert "Full score breakdown, thresholds, and quote diagnostics are in Advanced Mode." in _SRC
    assert "st.json(c.score_breakdown" in _SRC
    assert "_render_candidate_advanced" in _SRC and "_render_candidate_simple" in _SRC


# 12. no broker execution / order-preview surface introduced by the hotfix
_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


def test_no_execution_surface_introduced():
    for name in ("streamlit_main.py", "operator_mode.py", "cockpit_helpers.py"):
        text = (_REPO / "src" / "app" / name).read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{name} contains {tok!r}"
