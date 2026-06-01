"""Tests for ZeroSigmaApiStructureProvider.

These tests run entirely against `httpx.MockTransport`. No real network
calls are made. No real credentials are used or printed.
"""

from __future__ import annotations

import json
import sys
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


# ──────────────────────────────────────────────────────────────────────
# Phase 2.5: public_only mode
# ──────────────────────────────────────────────────────────────────────

def test_public_only_calls_snapshot_without_authorization_header():
    """In public_only mode, /market/snapshot is called with NO auth header,
    and /exposure/series is never attempted — even when enable_exposure_series=True."""
    seen_paths: list[tuple[str, dict[str, str]]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append((req.url.path, dict(req.headers)))
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=FAKE_SNAPSHOT)
        # Any other path => unexpected
        return httpx.Response(404, json={"detail": "should-not-be-called"})

    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="public_only",
        # Intentionally provide a token to confirm it is NOT used.
        token="should-never-be-sent",
        enable_exposure_series=True,
        symbol="SPX",
        client_factory=_client_factory(_mock_transport(handler)),
    )
    assert p._is_configured() is True
    assert p.status()["configured"] is True

    snap = p.get_snapshot("SPX")

    # Exactly one HTTP call: the public snapshot
    paths = [p for p, _ in seen_paths]
    assert paths == ["/api/v1/market/snapshot"]

    # NO Authorization header was sent
    _path, headers = seen_paths[0]
    auth_header = next(
        (v for k, v in headers.items() if k.lower() == "authorization"),
        None,
    )
    assert auth_header is None, f"unexpected Authorization header: {auth_header!r}"

    # Snapshot still populated for public fields; VW levels stay None
    assert snap.spot == 5800.25
    assert snap.exposures.total_gex_bn == 4.2
    assert snap.exposures.put_ceiling_2k is None
    assert snap.exposures.call_floor_2k is None
    assert snap.exposures.maxvol is None
    missing = (snap.raw or {}).get("missing_fields") or []
    assert "put_ceiling_2k" in missing
    assert "maxvol" in missing


def test_public_only_status_reports_effective_exposure_series_false():
    """Even with enable_exposure_series=True at construction, the effective
    flag is False under public_only so the cockpit can show a warning."""
    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="public_only",
        enable_exposure_series=True,
        symbol="SPX",
    )
    s = p.status()
    assert s["public_only"] is True
    assert s["configured"] is True
    assert s["exposure_series_enabled"] is True
    assert s["exposure_series_effective"] is False


def test_public_only_status_contains_no_secrets():
    """A token may have been passed at construction (e.g., leftover from
    another mode in .env). public_only must never expose it via status()."""
    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="public_only",
        token="leftover-secret-from-another-mode",
        username="someone@example",
        password="another-secret",
        service_key="and-another",
        symbol="SPX",
    )
    payload = json.dumps(p.status())
    assert "leftover-secret-from-another-mode" not in payload
    assert "another-secret" not in payload
    assert "and-another" not in payload


def test_none_mode_makes_no_http_calls_even_with_base_url():
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
    assert p._is_configured() is False
    with pytest.raises(RuntimeError):
        p.get_snapshot("SPX")
    assert calls == []


def test_bearer_mode_still_attaches_authorization_on_exposure_series():
    """Regression — Phase 2.5 must NOT break the original bearer flow."""
    seen: list[tuple[str, str | None]] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((
            req.url.path,
            next((v for k, v in req.headers.items() if k.lower() == "authorization"), None),
        ))
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=FAKE_SNAPSHOT)
        if req.url.path == "/api/v1/exposure/series":
            return httpx.Response(200, json=FAKE_VOLUME_SERIES_SPLIT)
        return httpx.Response(404)
    p = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="bearer",
        token="bearer-token-test",
        enable_exposure_series=True,
        symbol="SPX",
        client_factory=_client_factory(_mock_transport(handler)),
    )
    snap = p.get_snapshot("SPX")
    # snapshot endpoint: no auth
    snap_call = next(c for c in seen if c[0] == "/api/v1/market/snapshot")
    assert snap_call[1] is None
    # exposure series endpoint: auth header attached
    series_call = next(c for c in seen if c[0] == "/api/v1/exposure/series")
    assert series_call[1] == "Bearer bearer-token-test"
    # Volume-derived fields populated
    assert snap.exposures.put_ceiling_2k == 5815


# ──────────────────────────────────────────────────────────────────────
# smoke script (in-process invocation, no live network)
# ──────────────────────────────────────────────────────────────────────

def test_smoke_script_unconfigured_returns_zero_and_warns(capsys, monkeypatch):
    """When ZS_API_AUTH_MODE is none (default), the smoke script must NOT
    raise, must NOT exit nonzero, and must NOT print any traceback."""
    # Force unconfigured: empty all ZS_API_* env vars.
    for v in ("ZS_API_BASE_URL", "ZS_API_AUTH_MODE", "ZS_API_TOKEN",
              "ZS_API_USERNAME", "ZS_API_PASSWORD", "ZS_API_SERVICE_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ZS_API_AUTH_MODE", "none")

    monkeypatch.setattr(sys, "argv", ["scripts.smoke_zs_api"])
    from scripts.smoke_zs_api import main as smoke_main
    rc = smoke_main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "NOT configured" in out
    # nothing that looks like a stack trace
    assert "Traceback" not in out


def test_smoke_script_public_only_with_mocked_transport(monkeypatch, capsys):
    """End-to-end public_only smoke against MockTransport — no live network."""
    # Build a mocked provider and inject it via the factory override path.
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/market/snapshot":
            return httpx.Response(200, json=FAKE_SNAPSHOT)
        return httpx.Response(404)

    fake_provider = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="public_only",
        symbol="SPX",
        enable_exposure_series=True,
        client_factory=_client_factory(_mock_transport(handler)),
    )

    # Patch build_structure_provider AT ITS SOURCE so the smoke script's
    # lazy import inside main() picks up the test double.
    monkeypatch.setattr(
        "src.providers.structure.factory.build_structure_provider",
        lambda cfg, override=None: (fake_provider, "zerosigma_api"),
    )
    monkeypatch.setattr(sys, "argv", ["scripts.smoke_zs_api", "--symbol", "SPX"])

    import scripts.smoke_zs_api as smoke_mod
    rc = smoke_mod.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "public_only" in out
    assert "5800.25" in out
    assert "put_ceiling_2k" not in out or "None" in out  # field is None under public_only
    # secrets-from-construction never appear (we didn't pass any, but check
    # that nothing tokenish leaked)
    assert "service_key" not in out.lower()
    assert "password" not in out.lower()


def test_smoke_script_configured_failure_returns_one(monkeypatch, capsys):
    """Configured provider whose HTTP call fails → exit 1, clean message, no traceback."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    fake_provider = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example",
        auth_mode="public_only",
        symbol="SPX",
        client_factory=_client_factory(_mock_transport(handler)),
    )

    monkeypatch.setattr(
        "src.providers.structure.factory.build_structure_provider",
        lambda cfg, override=None: (fake_provider, "zerosigma_api"),
    )
    monkeypatch.setattr(sys, "argv", ["scripts.smoke_zs_api"])
    import scripts.smoke_zs_api as smoke_mod
    rc = smoke_mod.main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "Traceback" not in err


# ──────────────────────────────────────────────────────────────────────
# scanner integration: public_only mode produces a NO_TRADE (no VW levels)
# ──────────────────────────────────────────────────────────────────────

def test_scanner_runs_with_zerosigma_api_public_only(monkeypatch, tmp_path):
    """The scanner must accept the zerosigma_api provider in public_only
    mode, never make live calls (we inject a mock factory), and produce
    a NO_TRADE decision because VW levels are None."""
    import os
    import subprocess
    out = tmp_path / "outputs"

    # Smoke test the scanner subprocess in stub mode — we already know
    # zerosigma_api needs a network/server to be useful, and we cover the
    # provider-side behavior above. This guards against import / argparse
    # regressions in scripts.run_scanner under Phase 2.5.
    cmd = [sys.executable, "-m", "scripts.run_scanner", "--structure-provider", "stub"]
    rc = subprocess.call(
        cmd, cwd=str(REPO_ROOT_PATH),
        env={**os.environ, "OUTPUT_DIR": str(out), "PYTHONPATH": str(REPO_ROOT_PATH)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert rc == 0
    assert (out / "latest" / "decision_log.jsonl").exists()
