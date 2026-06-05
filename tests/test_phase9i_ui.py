"""Phase 9I — trader-first cockpit cleanup (source-level): data-source resolution,
quote diagnostics, Advanced/DDOI + terminal + manual-desk hidden in Simple Mode,
Main-only profile dropdown, stats charts, EOD refresh. Plus a no-execution guard.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


# ── 1. data-source resolution ────────────────────────────────────────────────

def test_data_source_resolution_wired():
    assert "Data source for this run" in _SRC
    assert "resolve_run_source(" in _SRC
    assert "App data source" in _SRC
    assert 'metric("Exposure source"' in _SRC and 'metric("Market data source"' in _SRC
    assert 'metric("Status"' in _SRC


# ── 2. quote diagnostics ─────────────────────────────────────────────────────

def test_quote_diagnostics_wired():
    # Phase 10B superseded the Phase 9I quote_chain_status banner with the richer
    # cockpit_quote_status reconciliation (distinct states, validation-aware).
    assert "cockpit_quote_status(" in _SRC
    # precise quote-state + validation details surfaced in the read-only expander
    assert "Quote status & diagnostics" in _SRC


# ── 3. Advanced structure + DDOI hidden in Simple Mode ───────────────────────

def test_advanced_structure_and_ddoi_gated():
    # the Advanced-structure expander (incl. DDOI) renders only when NOT simple_mode
    assert ('if not simple_mode:\n        with st.expander("Advanced structure / raw diagnostics"'
            in _SRC)
    # DDOI lives ONLY in the advanced expander (adv[...]) — never a prime top[...] card
    assert 'adv[3].metric("DDOI pin"' in _SRC
    for _i in range(6):
        assert f'top[{_i}].metric("DDOI' not in _SRC


# ── 4. Main-only profile dropdown + show-all toggle ──────────────────────────

def test_profile_dropdown_main_only():
    assert "simple_mode_profile_ids(" in _SRC
    assert "Show all saved profiles" in _SRC   # Phase 10C — clearer label, incl. custom
    # the old always-on category radio is gone
    assert 'st.radio(\n            "Profile group"' not in _SRC


# ── 5. terminal commands gated to Advanced Mode ──────────────────────────────

def test_terminal_commands_gated():
    # Tester terminal block is Advanced-only
    assert ('if not simple_mode:\n        with st.expander("Advanced details / terminal commands"'
            in _SRC)
    # Portfolio offers Simple-Mode buttons instead of a raw command block
    assert "🔄 Refresh portfolio" in _SRC
    assert "🧾 Reconcile local paper ledger" in _SRC
    # the python command block still exists (under Advanced else-branch)
    assert "run_portfolio_forward" in _SRC


# ── 6. Manual Paper Desk hidden in Simple Mode ───────────────────────────────

def test_manual_desk_hidden_in_simple():
    assert "if not simple_mode:\n        st.divider()\n        render_manual_desk()" in _SRC
    assert "Manual paper entry is available in Advanced Mode" in _SRC


# ── 7. stats charts + drawdown ───────────────────────────────────────────────

def test_stats_charts_wired():
    assert "Performance charts" in _SRC
    assert "equity_curve_from_closed_trades(" in _SRC
    assert "max_drawdown(" in _SRC
    assert "st.line_chart(" in _SRC and "st.area_chart(" in _SRC
    assert "Max drawdown" in _SRC


# ── 8. EOD generate/refresh + staleness ──────────────────────────────────────

def test_eod_refresh_wired():
    assert "Generate / Refresh EOD summary" in _SRC
    assert "eod_summary_status(" in _SRC
    assert "Last generated:" in _SRC
    assert "_eod_autogen_done" in _SRC          # safe one-shot auto-gen guard


# ── 9. latest-run clarity (friendly label first) ─────────────────────────────

def test_latest_run_friendly_label():
    assert 'metric("Latest run"' in _SRC
    assert "friendly_run_label(" in _SRC
    assert "Full run id:" in _SRC               # raw id only in Advanced


# ── no execution surface in the new/changed modules ──────────────────────────

_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


def test_no_execution_tokens_in_changed_sources():
    targets = [
        _REPO / "src" / "app" / "streamlit_main.py",
        _REPO / "src" / "app" / "cockpit_helpers.py",
        _REPO / "src" / "app" / "operator_mode.py",
        _REPO / "src" / "app" / "control_ui.py",
        _REPO / "scripts" / "discover_backtest_sources.py",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{path.name} contains {tok!r}"
