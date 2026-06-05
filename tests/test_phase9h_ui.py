"""Phase 9H — Live Cockpit wiring (source-level): operator decision layer, prime
Primary/Secondary Gamma cards, Wing Stack, DDOI removed from prime (Advanced
only), profile grouping + latest-run mismatch. Plus a no-execution guard over
all new/changed modules.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


# ── operator decision layer ──────────────────────────────────────────────────

def test_operator_decision_panel_present_and_above_market():
    assert "def render_operator_decision()" in _SRC
    assert "Operator read" in _SRC
    live = _SRC.index("with tab_live:")
    assert _SRC.index("render_operator_decision()", live) < _SRC.index("render_market()", live)


# ── prime cards: Primary/Secondary gamma replace DDOI ────────────────────────

def test_prime_cards_have_primary_secondary_gamma():
    assert 'metric("Primary gamma"' in _SRC
    assert 'metric("Secondary gamma"' in _SRC


def test_ddoi_removed_from_prime_cards():
    # the old prime DDOI metric line is gone …
    assert 'levels[4].metric("DDOI pin"' not in _SRC
    assert 'metric("DDOI pin", ch.fmt_strike(ex.ddoi_pin))' not in _SRC
    # … and DDOI now lives only under the Advanced Structure expander
    assert "Advanced structure / raw diagnostics" in _SRC
    assert 'adv[3].metric("DDOI pin"' in _SRC


# ── Wing Stack ───────────────────────────────────────────────────────────────

def test_wing_stack_section_present():
    assert "Wing Stack" in _SRC
    assert "wing_stack(" in _SRC
    # Phase 9J/10A — the tier-based "Primary wing" caption was replaced by the
    # corridor-aware "Wing corridor + dominant WDS" block; nearest wing remains.
    assert "Nearest" in _SRC and "Wing corridor + dominant WDS" in _SRC
    # 10K unavailability is explained, not silently blank
    assert "10K wings" in _SRC and "require upstream exposure" in _SRC


# ── mismatch + grouping ──────────────────────────────────────────────────────

def test_latest_run_mismatch_wired():
    assert "Latest completed test" in _SRC
    assert "run_profile_mismatch(" in _SRC
    assert "Selected profile:" in _SRC


def test_profile_grouping_wired():
    # Phase 9I/10C — the category radio was replaced by a Main-only dropdown + a
    # "Show all saved profiles" checkbox (Tester + Builder; incl. custom profiles).
    assert "simple_mode_profile_ids(" in _SRC
    assert "Show all saved profiles" in _SRC


# ── no execution surface anywhere in the new/changed modules ─────────────────

_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


def test_no_execution_tokens_in_changed_sources():
    targets = [
        _REPO / "src" / "app" / "streamlit_main.py",
        _REPO / "src" / "app" / "cockpit_helpers.py",
        _REPO / "src" / "app" / "operator_mode.py",
        _REPO / "src" / "providers" / "structure" / "types.py",
        _REPO / "src" / "providers" / "structure" / "zerosigma_api.py",
        _REPO / "src" / "providers" / "structure" / "stub.py",
        _REPO / "src" / "replay" / "__init__.py",
        _REPO / "src" / "replay" / "snapshot_loader.py",
        _REPO / "scripts" / "discover_replay_data.py",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{path.name} contains {tok!r}"
