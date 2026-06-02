"""Phase 4.1 — tasty_probe.validate_root_hint tests.

NO live HTTP. Monkey-patches httpx.MockTransport at the probe boundary.
"""

from __future__ import annotations

import json

import httpx

from src.providers.quotes.tasty_probe import (
    TastyProbeClient,
    TastyProbeConfig,
)

SESSIONS_OK = {
    "data": {"session-token": "TEST_TOKEN", "remember-token": "REM",
             "user": {"email": "test@example.com"}}
}

CHAIN_NESTED_REAL = {
    "data": {"items": [
        {"root-symbol": "SPX",  "expirations": [
            {"expiration-date": "2026-06-15", "strikes": [
                {"strike-price": "7600.0"}, {"strike-price": "7610.0"},
            ]},
        ]},
        {"root-symbol": "SPXW", "expirations": [
            {"expiration-date": "2026-06-01", "strikes": [
                {"strike-price": "7600.0"}, {"strike-price": "7610.0"},
            ]},
            {"expiration-date": "2026-06-02", "strikes": [
                {"strike-price": "7600.0"},
            ]},
        ]},
    ]}
}


def _cfg() -> TastyProbeConfig:
    return TastyProbeConfig(
        env="certification", base_url="https://api.cert.tastyworks.com",
        username="u", password="p", use_dxlink=False,
        allow_trade_scope=False, enable_order_submission=False,
    )


def _factory(handler):
    def _make():
        transport = httpx.MockTransport(handler)
        return httpx.Client(
            base_url="https://api.cert.tastyworks.com",
            transport=transport, timeout=5.0,
        )
    return _make


def _logged_in_probe(handler) -> TastyProbeClient:
    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    out = p.login()
    assert out.get("auth_success"), out
    return p


# ── validate_root_hint ─────────────────────────────────────────────────

def _handler_with_chain(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/sessions":
        return httpx.Response(200, json=SESSIONS_OK)
    if req.url.path == "/option-chains/SPX/nested":
        return httpx.Response(200, json=CHAIN_NESTED_REAL)
    return httpx.Response(404)


class TestValidateRootHint:
    def test_valid_spxw_hint(self):
        p = _logged_in_probe(_handler_with_chain)
        out = p.validate_root_hint("SPX", "SPXW", "2026-06-01")
        assert out["ok"] is True
        assert out["root_symbol"] == "SPXW"
        assert out["validated_via"] == "chain"

    def test_root_not_in_chain(self):
        p = _logged_in_probe(_handler_with_chain)
        out = p.validate_root_hint("SPX", "XYZ", "2026-06-01")
        assert out["ok"] is False
        assert out["reason"] == "root_not_in_chain"
        assert "SPXW" in (out.get("available_roots") or [])
        # Falls back to SPXW since 2026-06-01 lands there
        assert out["fallback_root"] == "SPXW"

    def test_expiry_not_in_root(self):
        """SPX advertises only 2026-06-15. Asking for 2026-06-01 on SPX
        fails with expiry_not_in_root and proposes SPXW as fallback."""
        p = _logged_in_probe(_handler_with_chain)
        out = p.validate_root_hint("SPX", "SPX", "2026-06-01")
        assert out["ok"] is False
        assert out["reason"] == "expiry_not_in_root"
        assert out["fallback_root"] == "SPXW"

    def test_chain_unavailable_propagates_cleanly(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/sessions":
                return httpx.Response(200, json=SESSIONS_OK)
            return httpx.Response(404, json={"detail": "not found"})

        p = _logged_in_probe(h)
        out = p.validate_root_hint("SPX", "SPXW", "2026-06-01")
        assert out["ok"] is False
        assert out["reason"] == "chain_unavailable"
        # No traceback — clean return
        assert "Traceback" not in json.dumps(out)


# ── get_option_quotes_for_strikes interaction ──────────────────────────

class TestExplicitRootValidationInQuotesPath:
    def test_explicit_root_validated_via_chain_when_available(self):
        """When chain IS available, an invalid explicit root downgrades to
        auto-resolve under lax mode (default)."""
        seen_paths: list[str] = []

        def h(req: httpx.Request) -> httpx.Response:
            seen_paths.append(req.url.path)
            if req.url.path == "/sessions":
                return httpx.Response(200, json=SESSIONS_OK)
            if req.url.path == "/option-chains/SPX/nested":
                return httpx.Response(200, json=CHAIN_NESTED_REAL)
            if req.url.path == "/market-data/by-type":
                # Return ONE matching quote
                return httpx.Response(200, json={"data": {"items": [
                    {"symbol": "SPXW  260601C07600000",
                     "bid": "1.50", "ask": "1.60", "mid": "1.55"},
                ]}})
            return httpx.Response(404)

        p = _logged_in_probe(h)
        # User passes 'XYZ' explicitly; with chain available, validate finds
        # mismatch (root_not_in_chain) and falls back to auto SPXW.
        out = p.get_option_quotes_for_strikes(
            "SPX", "2026-06-01", [7600.0], "C", root_symbol="XYZ",
        )
        assert out["ok"] is True
        assert out["resolved_root_symbol"] == "SPXW"
        assert out["root_resolution_source"] == "auto_chain_after_hint_mismatch"

    def test_explicit_root_with_unavailable_chain_keeps_hint(self):
        """When chain itself is unavailable (404/auth fail), the explicit
        hint is preserved — that's the original Phase 3.1 behavior."""
        seen_paths: list[str] = []

        def h(req: httpx.Request) -> httpx.Response:
            seen_paths.append(req.url.path)
            if req.url.path == "/sessions":
                return httpx.Response(200, json=SESSIONS_OK)
            # NO chain handler — validate_root_hint sees chain_unavailable
            if req.url.path == "/market-data/by-type":
                return httpx.Response(200, json={"data": {"items": [
                    {"symbol": "SPXW  260601C07600000",
                     "bid": "1.50", "ask": "1.60", "mid": "1.55"},
                ]}})
            return httpx.Response(404)

        p = _logged_in_probe(h)
        out = p.get_option_quotes_for_strikes(
            "SPX", "2026-06-01", [7600.0], "C", root_symbol="SPXW",
        )
        assert out["ok"] is True
        assert out["root_resolution_source"] == "explicit"
        assert out["resolved_root_symbol"] == "SPXW"

    def test_strict_mode_hard_fails_invalid_hint(self, monkeypatch):
        """STRICT_ROOT_HINT=true → invalid hint returns ok=False, no fallback."""
        monkeypatch.setenv("STRICT_ROOT_HINT", "true")

        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/sessions":
                return httpx.Response(200, json=SESSIONS_OK)
            if req.url.path == "/option-chains/SPX/nested":
                return httpx.Response(200, json=CHAIN_NESTED_REAL)
            return httpx.Response(404)

        p = _logged_in_probe(h)
        out = p.get_option_quotes_for_strikes(
            "SPX", "2026-06-01", [7600.0], "C", root_symbol="XYZ",
        )
        assert out["ok"] is False
        assert out["root_resolution_source"] == "explicit_invalid"
        assert out.get("root_hint_invalid") is True
        # NEVER leaks a token or auth header
        for k in ("token", "TEST_TOKEN", "Authorization", "Bearer"):
            assert k not in json.dumps(out)
