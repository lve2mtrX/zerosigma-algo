"""Phase 9J — WDS wired into the Live Cockpit (source-level) + no-execution guard."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def test_operator_read_uses_wing_dominance():
    assert "wing_dominance(" in _SRC
    # the wds dict is passed into the operator decision layer
    assert "wds=wds" in _SRC


def test_wing_stack_shows_dominant_wds_wing():
    # Phase 10A — corridor-aware Wing Stack (CW1/Spot/PW1 + Active/Inactive)
    assert "Wing corridor + dominant WDS" in _SRC
    assert "WSR (W2/W1)" in _SRC
    assert "immediate breach" in _SRC
    assert "CW1 (call floor 10K)" in _SRC and "PW1 (put ceiling 10K)" in _SRC
    assert '"✅ Active" if wd["corridor_valid"] else "⛔ Inactive"' in _SRC
    # the old tier-based "Primary wing" caption is gone from the Wing Stack
    assert "Primary wing: **" not in _SRC


_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


def test_no_execution_tokens_in_changed_sources():
    targets = [
        _REPO / "src" / "app" / "streamlit_main.py",
        _REPO / "src" / "app" / "cockpit_helpers.py",
        _REPO / "src" / "providers" / "structure" / "types.py",
        _REPO / "src" / "providers" / "structure" / "zerosigma_api.py",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{path.name} contains {tok!r}"
