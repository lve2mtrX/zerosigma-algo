"""Strategy registry smoke + vertical_wing_v1 candidate generation.

After the Phase 1.5 provider split, strategies receive BOTH a
`StructureSnapshot` (no chain) and an `OptionChainSnapshot` (no structure)."""

from __future__ import annotations

from pathlib import Path

from src.providers.quotes.mock_provider import MockQuoteProvider
from src.providers.structure.stub import StubStructureProvider
from src.strategies.registry import load_strategies
from src.utils.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_registry_loads_vertical_wing():
    cfg = load_config(REPO_ROOT)
    strategies = load_strategies(cfg)
    assert "vertical_wing_v1" in strategies


def test_vertical_wing_produces_both_sides_from_stub_plus_mock():
    cfg = load_config(REPO_ROOT)
    strategies = load_strategies(cfg)
    strat = strategies["vertical_wing_v1"]
    structure = StubStructureProvider().get_snapshot("SPX")
    chain     = MockQuoteProvider().get_option_chain("SPX", expiry=structure.expiry)
    assert chain is not None
    cands = strat.generate_candidates(structure, chain, strat.default_parameters)
    sides = {c.side for c in cands}
    assert "CALL_CREDIT" in sides
    assert "PUT_CREDIT" in sides
    # quote-derived metadata flows into candidates
    assert all("short_leg" in c.meta for c in cands)
    assert all(c.meta["short_leg"].get("bid") is not None for c in cands)
