"""Phase 2.8 — anchor-volume correctness across ZS structure → VW → scoring."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from src.providers.quotes.mock_provider import MockQuoteProvider
from src.providers.quotes.types import QuoteRequest
from src.providers.structure.stub import StubStructureProvider
from src.providers.structure.types import ExposureContext, StructureSnapshot
from src.providers.structure.zerosigma_api import ZeroSigmaApiStructureProvider
from src.strategies.vertical_wing.scoring import _structure_strength_score
from src.strategies.vertical_wing.strategy import VerticalWingV1

REPO_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────────────────────────────────────────────────
# 1. ZS provider stores anchor VOLUMES alongside strikes
# ──────────────────────────────────────────────────────────────────────

# Same fixture used by test_phase2p6_alignment / test_zerosigma_api_provider —
# strikes ordered, calls + puts arrays per strike.
FAKE_VOLUME_SERIES = {
    "symbol": "SPX", "metric": "volume", "mode": "split", "weight": "oi",
    "spot": 5800.25, "ts": "2026-06-01T14:30:00",
    "strikes": [5780, 5785, 5790, 5795, 5800, 5810, 5815, 5820],
    "calls":   [300,  2200, 5400, 600,  120,  400,  350,  250],
    "puts":    [200,  300,  400,  500,  120,  5500, 4500, 400],
}

FAKE_SNAPSHOT_PUBLIC_ONLY = {
    "symbol": "SPX", "timestamp": "2026-06-01T14:30:00",
    "spot": {"symbol": "SPX", "spot": 5800.0},
    "exposures": {
        "symbol": "SPX", "total_gex_1pct": 1.0,
        "wings": {"call_floor": 5790.0, "put_ceiling": 5815.0},
    },
    "chain": {"expiry": "2026-06-01", "dte": 0},
}


def _client_factory(transport: httpx.MockTransport):
    def factory() -> httpx.Client:
        return httpx.Client(base_url="https://api.test.example", transport=transport)
    return factory


def test_zs_provider_records_volume_at_each_anchor_strike():
    """When /exposure/series resolves put_ceiling_2k = 5815, the volume at
    that strike (4500) must land on ExposureContext.put_ceiling_2k_volume."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=FAKE_SNAPSHOT_PUBLIC_ONLY)
        if req.url.path == "/api/v1/exposure/series":
            return httpx.Response(200, json=FAKE_VOLUME_SERIES)
        return httpx.Response(404)

    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="bearer", token="fake", symbol="SPX",
        enable_exposure_series=True,
        client_factory=_client_factory(httpx.MockTransport(handler)),
    )
    e = p.get_snapshot("SPX").exposures

    assert e.put_ceiling_2k == 5815.0
    assert e.put_ceiling_2k_volume == 4500           # put volume at 5815
    assert e.put_ceiling_5k == 5810.0
    assert e.put_ceiling_5k_volume == 5500           # put volume at 5810

    assert e.call_floor_2k == 5785.0
    assert e.call_floor_2k_volume == 2200            # call volume at 5785
    assert e.call_floor_5k == 5790.0
    assert e.call_floor_5k_volume == 5400            # call volume at 5790

    # maxvol = strike with greatest combined volume; record its TOTAL.
    assert e.maxvol == 5810.0
    assert e.maxvol_volume == 5900                   # 400 + 5500


def test_zs_provider_anchor_volumes_none_when_series_unavailable():
    """public_only mode (no /exposure/series) → anchor volumes None even
    if wings.* populates the strike."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=FAKE_SNAPSHOT_PUBLIC_ONLY)
        return httpx.Response(404)

    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="public_only", symbol="SPX",
        client_factory=_client_factory(httpx.MockTransport(handler)),
    )
    e = p.get_snapshot("SPX").exposures

    # strike populated from wings.* fallback
    assert e.put_ceiling_2k == 5815.0
    assert e.call_floor_2k == 5790.0
    # but no per-strike volume available
    assert e.put_ceiling_2k_volume is None
    assert e.call_floor_2k_volume is None
    assert e.put_ceiling_5k_volume is None
    assert e.call_floor_5k_volume is None


# ──────────────────────────────────────────────────────────────────────
# 2. Stub provider populates anchor volumes from MOCK_CHAIN
# ──────────────────────────────────────────────────────────────────────

def test_stub_provider_carries_anchor_volumes_from_mock_chain():
    snap = StubStructureProvider().get_snapshot("SPX")
    e = snap.exposures
    # PUT_CEILING_2K = 5815 with put_volume = 4500 in MOCK_CHAIN
    assert e.put_ceiling_2k == 5815.0
    assert e.put_ceiling_2k_volume == 4500
    # PUT_CEILING_5K = 5810 with put_volume = 5500
    assert e.put_ceiling_5k == 5810.0
    assert e.put_ceiling_5k_volume == 5500
    # CALL_FLOOR_2K = 5785 with call_volume = 2200
    assert e.call_floor_2k == 5785.0
    assert e.call_floor_2k_volume == 2200
    # CALL_FLOOR_5K = 5790 with call_volume = 5400
    assert e.call_floor_5k == 5790.0
    assert e.call_floor_5k_volume == 5400


# ──────────────────────────────────────────────────────────────────────
# 3. VW candidates use STRUCTURE volume, not chain volume
# ──────────────────────────────────────────────────────────────────────

def _live_like_structure() -> StructureSnapshot:
    """Live ZS levels at ~7580 — no MOCK_CHAIN match → forces aligned-mode
    synthesis in MockQuoteProvider, which would return token volumes."""
    return StructureSnapshot(
        symbol="SPX", spot=7574.55,
        quote_ts=datetime(2026, 6, 1, 14, 30),
        exposures=ExposureContext(
            put_ceiling_2k=7600.0, put_ceiling_5k=7585.0,
            call_floor_2k=7560.0,  call_floor_5k=7570.0,
            put_ceiling_2k_volume=2400, put_ceiling_5k_volume=5300,
            call_floor_2k_volume=2200,  call_floor_5k_volume=5100,
            maxvol=7580.0, maxvol_volume=8200,
            gamma_regime="positive", da_gex_signed=1.0,
        ),
        expiry="2026-06-01", dte=0, source="zerosigma_api",
    )


def test_vw_call_credit_uses_put_ceiling_volume_from_structure():
    """The CALL_CREDIT candidate's anchor_volume must come from
    structure.put_ceiling_2k_volume (2400) — NOT from the mock chain's
    token volume (100.0 at the synthesized 7600 strike)."""
    structure = _live_like_structure()
    strat = VerticalWingV1()
    required = strat.required_quote_strikes(structure, strat.default_parameters)
    chain = MockQuoteProvider().get_option_chain(
        "SPX", request=QuoteRequest(
            symbol="SPX", spot_hint=structure.spot,
            required_strikes=tuple(required),
        ),
    )
    candidates = strat.generate_candidates(structure, chain, strat.default_parameters)
    by_side = {c.side: c for c in candidates}
    call = by_side["CALL_CREDIT"]
    assert call.meta["anchor_source"] == "put_ceiling_2k"
    assert call.meta["anchor_volume"] == 2400
    assert call.meta["anchor_volume_source"] == "zs_exposure_series"


def test_vw_put_credit_uses_call_floor_volume_from_structure():
    structure = _live_like_structure()
    strat = VerticalWingV1()
    required = strat.required_quote_strikes(structure, strat.default_parameters)
    chain = MockQuoteProvider().get_option_chain(
        "SPX", request=QuoteRequest(
            symbol="SPX", spot_hint=structure.spot,
            required_strikes=tuple(required),
        ),
    )
    candidates = strat.generate_candidates(structure, chain, strat.default_parameters)
    by_side = {c.side: c for c in candidates}
    put = by_side["PUT_CREDIT"]
    assert put.meta["anchor_source"] == "call_floor_2k"
    assert put.meta["anchor_volume"] == 2200
    assert put.meta["anchor_volume_source"] == "zs_exposure_series"


def test_vw_falls_back_to_chain_when_structure_has_level_but_no_volume():
    """public_only-style structure (level set, volume None) → chain fallback,
    anchor_volume_source = 'quote_provider_fallback'."""
    structure = StructureSnapshot(
        symbol="SPX", spot=5800.0,
        quote_ts=datetime(2026, 6, 1, 14, 30),
        exposures=ExposureContext(
            put_ceiling_2k=5815.0, call_floor_2k=5785.0,
            put_ceiling_2k_volume=None, call_floor_2k_volume=None,
            gamma_regime="positive",
        ),
        expiry="2026-06-01", dte=0, source="zerosigma_api",
    )
    strat = VerticalWingV1()
    # Default mode (no QuoteRequest) → static MOCK_CHAIN, which has put_vol
    # 4500 at 5815 and call_vol 2200 at 5785. Those ARE the fallback values.
    chain = MockQuoteProvider().get_option_chain("SPX")
    candidates = strat.generate_candidates(structure, chain, strat.default_parameters)
    by_side = {c.side: c for c in candidates}
    assert by_side["CALL_CREDIT"].meta["anchor_volume_source"] == "quote_provider_fallback"
    assert by_side["PUT_CREDIT"].meta["anchor_volume_source"]  == "quote_provider_fallback"
    # The fallback volume IS the chain's put/call volume at those strikes.
    assert by_side["CALL_CREDIT"].meta["anchor_volume"] == 4500   # chain put_vol @ 5815
    assert by_side["PUT_CREDIT"].meta["anchor_volume"]  == 2200   # chain call_vol @ 5785


# ──────────────────────────────────────────────────────────────────────
# 4. scoring._structure_strength_score policy
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "vol,expected_score,expected_source",
    [
        (5000.0, 1.0, "zs_volume_series"),          # cap
        (3000.0, 0.5, "zs_volume_series"),
        (2000.0, 0.25, "zs_volume_series"),
        (1000.0, 0.0, "zs_volume_series"),          # floor
        (500.0,  0.0, "zs_volume_series"),          # clipped
    ],
)
def test_structure_strength_with_volume(vol, expected_score, expected_source):
    s, src = _structure_strength_score(vol, anchor_source="put_ceiling_2k")
    assert s == pytest.approx(expected_score, abs=1e-9)
    assert src == expected_source


def test_structure_strength_neutral_when_level_present_no_volume():
    s, src = _structure_strength_score(None, anchor_source="put_ceiling_2k")
    assert s == 0.5
    assert src == "missing_anchor_volume_neutral"


def test_structure_strength_zero_when_no_level_at_all():
    s, src = _structure_strength_score(None, anchor_source=None)
    assert s == 0.0
    assert src == "no_anchor"


# ──────────────────────────────────────────────────────────────────────
# 5. CSV + JSONL surface anchor observability
# ──────────────────────────────────────────────────────────────────────

def _spawn_scanner(tmp_path: Path) -> tuple[Path, Path]:
    env = dict(os.environ)
    for k in list(env.keys()):
        if k.startswith("ZS_API_") or k == "ZS_STRUCTURE_PROVIDER":
            env.pop(k, None)
    env["ZS_STRUCTURE_PROVIDER"] = "stub"
    env["ZS_API_AUTH_MODE"]      = "none"
    env["OUTPUT_DIR"]            = str(tmp_path / "outputs")
    env["PYTHONPATH"]            = str(REPO_ROOT)
    rc = subprocess.call(
        [sys.executable, "-m", "scripts.run_scanner"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert rc == 0
    return (
        tmp_path / "outputs" / "latest" / "ranked_candidates.csv",
        tmp_path / "outputs" / "latest" / "decision_log.jsonl",
    )


def test_csv_includes_anchor_fields(tmp_path: Path):
    csv_path, _ = _spawn_scanner(tmp_path)
    with csv_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    fields = set(rows[0].keys())
    for col in (
        "anchor_source", "anchor_volume", "anchor_volume_source",
        "structure_strength_source",
    ):
        assert col in fields, f"missing CSV column {col}"

    # In stub mode, anchor_volume is the structure-derived MOCK_CHAIN volume
    # (4500 at 5815 for CALL_CREDIT, 2200 at 5785 for PUT_CREDIT).
    by_side = {r["side"]: r for r in rows}
    call = by_side["CALL_CREDIT"]
    assert call["anchor_source"] == "put_ceiling_2k"
    assert float(call["anchor_volume"]) == 4500.0
    assert call["anchor_volume_source"] == "zs_exposure_series"
    assert call["structure_strength_source"] == "zs_volume_series"
    # structure_strength now reflects the real volume → > 0
    assert float(call["score_structure_strength"]) > 0


def test_jsonl_per_candidate_carries_anchor_meta(tmp_path: Path):
    _, log_path = _spawn_scanner(tmp_path)
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records
    for c in records[0]["all_candidates"]:
        meta = c["meta"]
        assert "anchor_source" in meta
        assert "anchor_volume" in meta
        assert "anchor_volume_source" in meta
        assert "structure_strength_source" in meta
