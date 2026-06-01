"""StructureProvider must produce STRUCTURE-only data (no chain quotes)."""

from __future__ import annotations

from src.providers._mock_data import SPOT
from src.providers.structure.stub import StubStructureProvider
from src.providers.structure.types import StructureSnapshot


def test_stub_is_deterministic():
    a = StubStructureProvider().get_snapshot("SPX")
    b = StubStructureProvider().get_snapshot("SPX")
    assert a.spot == b.spot == SPOT
    assert a.exposures.put_ceiling_2k == b.exposures.put_ceiling_2k
    assert a.exposures.call_floor_2k == b.exposures.call_floor_2k


def test_stub_carries_vertical_wing_anchors():
    snap = StubStructureProvider().get_snapshot("SPX")
    assert snap.exposures.put_ceiling_2k == 5815.0
    assert snap.exposures.put_ceiling_5k == 5810.0
    assert snap.exposures.call_floor_2k  == 5785.0
    assert snap.exposures.call_floor_5k  == 5790.0
    assert snap.exposures.maxvol is not None
    assert snap.exposures.gamma_regime == "positive"


def test_structure_snapshot_does_not_carry_chain_quotes():
    """Regression: post Phase 1.5, StructureSnapshot has NO 'chain' / 'quotes' field."""
    snap = StubStructureProvider().get_snapshot("SPX")
    assert isinstance(snap, StructureSnapshot)
    assert not hasattr(snap, "chain"), \
        "StructureSnapshot must not carry chain quotes; use OptionChainSnapshot instead"
    assert not hasattr(snap, "quotes")
