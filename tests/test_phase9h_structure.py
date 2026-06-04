"""Phase 9H — 10K wing tier + primary/secondary gamma mapping + shared
payload→snapshot extraction reused by the replay scaffold.

Structure/mapping only. Nothing here executes or previews an order.
"""

from __future__ import annotations

from src.providers.structure.stub import StubStructureProvider
from src.providers.structure.types import ExposureContext
from src.providers.structure.zerosigma_api import ZeroSigmaApiStructureProvider
from src.replay import snapshot_loader as sl


def _prov() -> ZeroSigmaApiStructureProvider:
    return ZeroSigmaApiStructureProvider(base_url="http://x", auth_mode="bearer", token="t")


_SERIES = {
    "strikes": [7500, 7550, 7570, 7600, 7650],
    "puts":    [12000, 6000, 3000, 500, 100],    # 7500 >= 10k
    "calls":   [100, 400, 3000, 6000, 11000],    # 7650 >= 10k
}
_SNAP = {
    "exposures": {
        "total_gex_1pct": 4.0, "total_da_gex_1pct": -2.0,
        "max_call_oi_strike": 7600, "max_put_oi_strike": 7500,
        "gamma": {"regime": "negative", "flip": 7560,
                  "cluster_primary": 7600, "cluster_secondary": 7570},
    },
    "spot": {"spot": 7609.0},
}


# ── 10K wing derivation (ZS mapper) ──────────────────────────────────────────

def test_zs_mapper_derives_10k_wings():
    miss: list[str] = []
    ex = _prov()._build_exposures(_SNAP, _SERIES, miss)
    assert ex.put_ceiling_10k == 7500
    assert ex.call_floor_10k == 7650
    assert ex.put_ceiling_10k_volume == 12000
    assert ex.call_floor_10k_volume == 11000
    # 2K / 5K still derived as before
    assert ex.put_ceiling_2k == 7570 and ex.put_ceiling_5k == 7550


def test_zs_mapper_10k_none_without_qualifying_volume():
    series = {"strikes": [7500, 7600], "puts": [3000, 100], "calls": [100, 3000]}
    miss: list[str] = []
    ex = _prov()._build_exposures({"exposures": {}}, series, miss)
    assert ex.put_ceiling_10k is None and ex.call_floor_10k is None
    assert "put_ceiling_10k" in miss and "call_floor_10k" in miss


# ── primary / secondary gamma mapping ────────────────────────────────────────

def test_zs_mapper_maps_gamma_clusters():
    miss: list[str] = []
    ex = _prov()._build_exposures(_SNAP, _SERIES, miss)
    assert ex.gamma_primary == 7600.0
    assert ex.gamma_secondary == 7570.0


def test_zs_mapper_gamma_clusters_absent_tracked_missing():
    miss: list[str] = []
    ex = _prov()._build_exposures({"exposures": {"gamma": {"regime": "positive"}}}, None, miss)
    assert ex.gamma_primary is None and ex.gamma_secondary is None
    assert "gamma_primary" in miss and "gamma_secondary" in miss


def test_zs_mapper_gamma_cluster_aliases():
    miss: list[str] = []
    snap = {"exposures": {"gamma": {"primary": 100, "secondary_strike": 90}}}
    ex = _prov()._build_exposures(snap, None, miss)
    assert ex.gamma_primary == 100.0 and ex.gamma_secondary == 90.0


# ── stub: 10K honest None (mock peaks ~5.5K) + demo gamma clusters ───────────

def test_stub_10k_unavailable_but_gamma_clusters_present():
    ex = StubStructureProvider().get_snapshot("SPX").exposures
    assert ex.put_ceiling_10k is None and ex.call_floor_10k is None
    assert ex.put_ceiling_2k is not None and ex.put_ceiling_5k is not None
    assert ex.gamma_primary == 5795.0 and ex.gamma_secondary == 5825.0


# ── shared build_snapshot_from_payload + replay loader parity ─────────────────

def test_build_snapshot_from_payload_matches_fields():
    snap = _prov().build_snapshot_from_payload(_SNAP, _SERIES, symbol="SPX")
    assert snap.symbol == "SPX" and snap.spot == 7609.0
    assert snap.exposures.put_ceiling_10k == 7500
    assert snap.exposures.gamma_primary == 7600.0
    assert snap.source == "zerosigma_api"


def test_replay_loader_reuses_same_mapping():
    snap = sl.map_payload_to_snapshot(_SNAP, _SERIES, symbol="SPX")
    assert snap.source == "replay"                       # replay tag
    assert snap.exposures.put_ceiling_10k == 7500        # same 10K derivation
    assert snap.exposures.gamma_primary == 7600.0        # same gamma mapping


def test_replay_loader_accepts_bundle_and_raw():
    raw = sl.load_snapshot_record(_SNAP)
    assert raw.exposures.gamma_primary == 7600.0
    bundle = sl.load_snapshot_record(
        {"symbol": "SPX", "snapshot": _SNAP, "exposure_series": _SERIES})
    assert bundle.exposures.put_ceiling_10k == 7500


def test_discover_snapshot_files_empty_ok():
    # No saved snapshots in-repo yet → empty list, no error.
    assert sl.discover_snapshot_files() == []


def test_exposure_context_backward_compatible_defaults():
    # New fields all default to None → old construction still valid.
    ex = ExposureContext()
    assert ex.put_ceiling_10k is None and ex.call_floor_10k is None
    assert ex.gamma_primary is None and ex.gamma_secondary is None
