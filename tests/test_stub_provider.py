"""Stub StructureProvider must be deterministic + carry the expected levels."""

from __future__ import annotations

from src.providers.structure.stub import SPOT, StubStructureProvider


def test_stub_is_deterministic():
    a = StubStructureProvider().get_snapshot("SPX")
    b = StubStructureProvider().get_snapshot("SPX")
    assert a.spot == b.spot == SPOT
    assert len(a.chain) == len(b.chain)
    assert [r.strike for r in a.chain] == [r.strike for r in b.chain]
    assert [r.c_volume for r in a.chain] == [r.c_volume for r in b.chain]
    assert [r.p_volume for r in a.chain] == [r.p_volume for r in b.chain]


def test_stub_chain_has_vertical_wing_anchors():
    snap = StubStructureProvider().get_snapshot("SPX")
    # PUT_CEILING(2K) is the HIGHEST strike with put_vol >= 2000
    assert snap.exposures.put_ceiling_2k == 5815.0
    # PUT_CEILING(5K) is the HIGHEST strike with put_vol >= 5000
    assert snap.exposures.put_ceiling_5k == 5810.0
    # CALL_FLOOR(2K) is the LOWEST strike with call_vol >= 2000
    assert snap.exposures.call_floor_2k == 5785.0
    # CALL_FLOOR(5K) is the LOWEST strike with call_vol >= 5000
    assert snap.exposures.call_floor_5k == 5790.0
    # plus maxvol + gamma context
    assert snap.exposures.maxvol is not None
    assert snap.exposures.gamma_regime == "positive"


def test_stub_chain_strikes_span_around_spot():
    snap = StubStructureProvider().get_snapshot("SPX")
    strikes = [r.strike for r in snap.chain]
    assert min(strikes) <= snap.spot <= max(strikes)
    assert 5780 in strikes and 5830 in strikes
