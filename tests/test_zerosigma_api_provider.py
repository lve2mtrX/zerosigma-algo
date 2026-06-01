"""Tests for ZeroSigmaApiStructureProvider.

These tests run entirely against `httpx.MockTransport`. No real network
calls are made. No real credentials are used or printed.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.providers.structure.factory import build_structure_provider
from src.providers.structure.types import StructureSnapshot
from src.providers.structure.zerosigma_api import ZeroSigmaApiStructureProvider
from src.utils.config import load_config

REPO_ROOT_PATH = (
    __import__("pathlib").Path(__file__).resolve().parents[1]
)


# ──────────────────────────────────────────────────────────────────────
# fixtures: representative fake ZS API JSON
# ──────────────────────────────────────────────────────────────────────

FAKE_SNAPSHOT = {
    "symbol": "SPX",
    "timestamp": "2026-06-01T14:30:00-04:00",
    "spot":      {"underlying": "SPX", "price": 5800.25, "timestamp": "2026-06-01T14:30:00-04:00"},
    "exposures": {"ts": "2026-06-01T14:30:00-04:00",
                  "total_gex_bn": 4.2, "da_gex_bn": 1.8,
                  "dex": 2.1, "vex": -1.1, "cex": 0.3},
    "chain":     {"expiry": "2026-06-01", "dte": 0, "strikes": []},
}

FAKE_VOLUME_SERIES_SPLIT = {
    "symbol": "SPX", "metric": "volume", "mode": "split", "weight": "oi",
    "spot": 5800.25, "ts": "2026-06-01T14:30:00-04:00",
    "strikes": [5780, 5785, 5790, 5795, 5800, 5810, 5815, 5820],
    "calls":   [300,  2200, 5400, 600,  120,  400,  350,  250],
    "puts":    [200,  300,  400,  500,  120,  5500, 4500, 400],
}

FAKE_LOGIN_RESPONSE = {
    "access_token": "fake.jwt.token",
    "token_type": "bearer",
    "user_id": 42,
}


def _mock_transport(handler) -> httpx.MockTransport:  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


def _client_factory(transport: httpx.MockTransport) -> Any:
    def factory() -> httpx.Client:
        return httpx.Client(base_url="https://api.test.example", transport=transport)
    return factory


# ──────────────────────────────────────────────────────────────────────
# happy path: bearer token + both endpoints populated
# ──────────────────────────────────────────────────────────────────────

def test_bearer_path_maps_snapshot_into_structure():
    auth_headers_seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        auth_headers_seen.append(req.headers.get("Authorization") or "")
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=FAKE_SNAPSHOT)
        if req.url.path == "/api/v1/exposure/series":
            return httpx.Response(200, json=FAKE_VOLUME_SERIES_SPLIT)
        return httpx.Response(404, json={"detail": "unmocked"})

    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="bearer",
        token="fake.jwt.token",
        symbol="SPX",
        client_factory=_client_factory(_mock_transport(handler)),
    )
    snap = p.get_snapshot("SPX")
    assert isinstance(snap, StructureSnapshot)

    # Spot from snapshot.spot.price
    assert snap.spot == 5800.25
    assert snap.source == "zerosigma_api"
    assert snap.expiry == "2026-06-01"
    assert snap.dte == 0

    # Exposures mapping
    e = snap.exposures
    assert e.total_gex_bn == 4.2
    assert e.total_vex_bn == -1.1            # vex → total_vex_bn
    assert e.da_gex_signed == 1.8
    assert e.gamma_regime == "positive"      # derived from da_gex sign

    # VW levels derived from the volume series
    assert e.put_ceiling_2k == 5815          # highest strike where puts >= 2000
    assert e.put_ceiling_5k == 5810
    assert e.call_floor_2k  == 5785
    assert e.call_floor_5k  == 5790
    assert e.maxvol         == 5810          # max combined volume

    # Auth header on /exposure/series — bearer header attached, no key/password leak
    assert any("Bearer fake.jwt.token" in h for h in auth_headers_seen)
    for h in auth_headers_seen:
        assert "service_key" not in h.lower()
        assert "password" not in h.lower()


# ──────────────────────────────────────────────────────────────────────
# subscription gate: /exposure/series 403 → VW levels are None, snapshot still returns
# ──────────────────────────────────────────────────────────────────────

def test_exposure_series_403_degrades_gracefully():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=FAKE_SNAPSHOT)
        if req.url.path == "/api/v1/exposure/series":
            return httpx.Response(403, json={"detail": "subscription required"})
        return httpx.Response(404)

    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example", auth_mode="bearer",
        token="fake", symbol="SPX",
        client_factory=_client_factory(_mock_transport(handler)),
    )
    snap = p.get_snapshot("SPX")
    e = snap.exposures
    # Subscription-gated fields drop to None
    assert e.put_ceiling_2k is None
    assert e.put_ceiling_5k is None
    assert e.call_floor_2k  is None
    assert e.call_floor_5k  is None
    assert e.maxvol is None
    # Public fields still populated
    assert e.total_gex_bn == 4.2
    assert snap.spot == 5800.25
    # Tracked diagnostics
    missing = (snap.raw or {}).get("missing_fields") or []
    assert "put_ceiling_2k" in missing
    assert snap.raw.get("subscription_active") is False
    assert p.status()["subscription_active"] is False


# ──────────────────────────────────────────────────────────────────────
# missing exposures payload: stays None instead of crashing
# ──────────────────────────────────────────────────────────────────────

def test_missing_exposures_payload_falls_back_to_none():
    skinny = {
        "symbol": "SPX", "timestamp": "2026-06-01T14:30:00-04:00",
        "spot": {"price": 5810.0, "timestamp": "2026-06-01T14:30:00-04:00"},
        "chain": {"expiry": "2026-06-01", "dte": 0},
        # no "exposures" key at all
    }
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=skinny)
        if req.url.path == "/api/v1/exposure/series":
            return httpx.Response(200, json=FAKE_VOLUME_SERIES_SPLIT)
        return httpx.Response(404)

    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example", auth_mode="bearer",
        token="fake", symbol="SPX",
        client_factory=_client_factory(_mock_transport(handler)),
    )
    snap = p.get_snapshot("SPX")
    assert snap.spot == 5810.0
    assert snap.exposures.total_gex_bn is None
    assert snap.exposures.da_gex_signed is None
    assert snap.exposures.gamma_regime is None
    # VW levels still populated (from /exposure/series)
    assert snap.exposures.put_ceiling_2k == 5815


# ──────────────────────────────────────────────────────────────────────
# auth: service_token flow exchanges service_key for a bearer token
# ──────────────────────────────────────────────────────────────────────

def test_service_token_mode_obtains_bearer_then_uses_it():
    seen_paths: list[str] = []
    service_token_body_seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append(req.url.path)
        if req.url.path == "/api/v1/auth/service-token":
            nonlocal service_token_body_seen
            service_token_body_seen = json.loads(req.content.decode())
            return httpx.Response(200, json=FAKE_LOGIN_RESPONSE)
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=FAKE_SNAPSHOT)
        if req.url.path == "/api/v1/exposure/series":
            return httpx.Response(200, json=FAKE_VOLUME_SERIES_SPLIT)
        return httpx.Response(404)

    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="service_token",
        username="admin@example.test",
        service_key="fake-service-key-NOT-A-REAL-SECRET",
        symbol="SPX",
        client_factory=_client_factory(_mock_transport(handler)),
    )
    snap = p.get_snapshot("SPX")
    assert isinstance(snap, StructureSnapshot)
    assert "/api/v1/auth/service-token" in seen_paths
    assert service_token_body_seen.get("email") == "admin@example.test"
    assert "service_key" in service_token_body_seen
    # Provider cached the token returned from /auth/service-token
    assert p._token == "fake.jwt.token"


# ──────────────────────────────────────────────────────────────────────
# unconfigured: get_snapshot must NOT make any HTTP call
# ──────────────────────────────────────────────────────────────────────

def test_unconfigured_provider_raises_before_any_http():
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(200, json={})

    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="none",
        symbol="SPX",
        client_factory=_client_factory(_mock_transport(handler)),
    )
    assert p.status()["configured"] is False
    with pytest.raises(RuntimeError):
        p.get_snapshot("SPX")
    assert calls == []  # no network attempt


def test_status_never_contains_secrets():
    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="bearer",
        token="super-secret-token-should-not-leak",
        symbol="SPX",
    )
    s = p.status()
    payload = json.dumps(s)
    assert "super-secret-token-should-not-leak" not in payload
    assert "service_key" not in payload.lower()
    assert "password" not in payload.lower()


# ──────────────────────────────────────────────────────────────────────
# factory + scanner integration: default = stub; explicit = zerosigma_api
# ──────────────────────────────────────────────────────────────────────

def test_factory_default_is_stub():
    cfg = load_config(REPO_ROOT_PATH)
    provider, name = build_structure_provider(cfg)
    assert name == "stub"
    # actual class name from the resolved instance
    assert provider.__class__.__name__ == "StubStructureProvider"


def test_factory_can_select_zerosigma_api_explicitly():
    cfg = load_config(REPO_ROOT_PATH)
    provider, name = build_structure_provider(cfg, override="zerosigma_api")
    # We don't have creds in CI/.env, but the factory still returns the
    # instance (status() will report unconfigured). The key invariant: the
    # factory doesn't fall back to stub on instantiation alone.
    assert name == "zerosigma_api"
    assert provider.__class__.__name__ == "ZeroSigmaApiStructureProvider"
    assert provider.status()["configured"] is False


def test_factory_unknown_falls_back_to_stub():
    cfg = load_config(REPO_ROOT_PATH)
    provider, name = build_structure_provider(cfg, override="this_does_not_exist")
    assert name == "stub"
    assert provider.__class__.__name__ == "StubStructureProvider"
