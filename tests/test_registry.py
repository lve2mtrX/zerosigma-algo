"""Smoke test — strategy registry loads vertical_wing_v1 and it can produce
at least one candidate from the stub snapshot."""

from __future__ import annotations

from pathlib import Path

from src.providers.structure.stub import StubStructureProvider
from src.strategies.registry import load_strategies
from src.utils.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_registry_loads_vertical_wing():
    cfg = load_config(REPO_ROOT)
    strategies = load_strategies(cfg)
    assert "vertical_wing_v1" in strategies


def test_vertical_wing_produces_candidates_against_stub_snapshot():
    cfg = load_config(REPO_ROOT)
    strategies = load_strategies(cfg)
    strat = strategies["vertical_wing_v1"]
    snap = StubStructureProvider().get_snapshot("SPX")
    cands = strat.generate_candidates(snap, strat.default_parameters)
    # Stub snapshot is built so both PUT_CEILING_CALL_CREDIT and CALL_FLOOR_PUT_CREDIT
    # have qualifying volume.
    assert any(c.side == "CALL_CREDIT" for c in cands)
    assert any(c.side == "PUT_CREDIT" for c in cands)
