"""Phase 3.1 — Tasty probe root auto-resolution + missing_fields fix."""

from __future__ import annotations

import json
import sys

import httpx
import pytest

from src.providers.quotes.tasty_probe import (
    DEFAULT_BASE_URLS,
    SafetyGateError,
    TastyProbeClient,
    TastyProbeConfig,
    _build_occ_option_symbol,
)

# ──────────────────────────────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────────────────────────────

# Real-shape Tasty nested chain: SPX has only the monthly 3rd-Friday
# expiry; SPXW has weeklies + 0DTE.
CHAIN_NESTED_REAL = {
    "data": {
        "items": [
            {
                "root-symbol": "SPX",
                "underlying-symbol": "SPX",
                "expirations": [
                    {
                        "expiration-date": "2026-06-19",
                        "days-to-expiration": 18,
                        "settlement-type": "AM",
                        "strikes": [
                            {"strike-price": "5800.0"},
                            {"strike-price": "5810.0"},
                        ],
                    },
                ],
            },
            {
                "root-symbol": "SPXW",
                "underlying-symbol": "SPX",
                "expirations": [
                    {
                        "expiration-date": "2026-06-01",
                        "days-to-expiration": 0,
                        "settlement-type": "PM",
                        "strikes": [
                            {"strike-price": "7550.0"},
                            {"strike-price": "7570.0"},
                            {"strike-price": "7600.0"},
                            {"strike-price": "7605.0"},
                        ],
                    },
                    {
                        "expiration-date": "2026-06-03",
                        "days-to-expiration": 2,
                        "settlement-type": "PM",
                        "strikes": [
                            {"strike-price": "7560.0"},
                            {"strike-price": "7580.0"},
                        ],
                    },
                ],
            },
        ]
    }
}

SESSIONS_OK = {
    "data": {
        "user": {"email": "tester@example.test", "username": "tester",
                 "external-id": "ext"},
        "session-token":  "fake-session-token",
        "remember-token": "fake-remember-token",
    },
    "context": "/sessions",
}

QUOTES_OK_SPXW = {
    "data": {
        "items": [
            {"symbol": "SPXW  260601C07600000", "instrument-type": "Equity Option",
             "bid": "0.50", "ask": "0.60", "mid": "0.55", "last": "0.55", "mark": "0.55",
             "updated-at": "2026-06-01T14:30:00Z"},
            {"symbol": "SPXW  260601C07605000", "instrument-type": "Equity Option",
             "bid": "0.25", "ask": "0.30", "mid": "0.27", "last": "0.27", "mark": "0.27",
             "updated-at": "2026-06-01T14:30:00Z"},
        ]
    }
}


def _factory(handler):
    transport = httpx.MockTransport(handler)

    def make() -> httpx.Client:
        return httpx.Client(
            base_url=DEFAULT_BASE_URLS["certification"],
            transport=transport,
        )
    return make


def _cfg(**overrides):
    base = TastyProbeConfig(
        env="certification",
        username="tester",
        password="hunter2",
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _logged_in_probe(handler):
    """Return a probe that's already authenticated via the mock handler."""
    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    out = p.login()
    assert out["auth_success"], f"login failed: {out}"
    return p


# ──────────────────────────────────────────────────────────────────────
# 1. resolve_root_for — SPX-asks-SPXW for daily expiry
# ──────────────────────────────────────────────────────────────────────

def test_resolve_root_for_spx_daily_picks_spxw():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        return httpx.Response(404)

    p = _logged_in_probe(handler)
    out = p.resolve_root_for("SPX", "2026-06-01")
    assert out["ok"] is True
    assert out["root_symbol"] == "SPXW"
    assert out["source"] == "auto_chain"
    assert "SPX"  in out["available_roots"]
    assert "SPXW" in out["available_roots"]


def test_resolve_root_for_spx_monthly_picks_spx():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        return httpx.Response(404)

    p = _logged_in_probe(handler)
    out = p.resolve_root_for("SPX", "2026-06-19")
    assert out["ok"] is True
    assert out["root_symbol"] == "SPX"
    assert out["source"] in ("auto_chain", "direct_match")


def test_resolve_root_for_direct_match_when_caller_already_picks_spxw():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPXW/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        return httpx.Response(404)

    p = _logged_in_probe(handler)
    out = p.resolve_root_for("SPXW", "2026-06-01")
    assert out["ok"] is True
    assert out["root_symbol"] == "SPXW"
    assert out["source"] == "direct_match"


def test_resolve_root_for_unresolved_expiry_returns_clean_error():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        return httpx.Response(404)

    p = _logged_in_probe(handler)
    out = p.resolve_root_for("SPX", "2099-12-31")
    assert out["ok"] is False
    assert out["reason"] == "expiry_not_in_chain"
    assert out["requested_symbol"] == "SPX"
    assert out["requested_expiry"] == "2099-12-31"
    assert "SPX"  in out["available_roots"]
    assert "SPXW" in out["available_roots"]
    # Diagnostics: sample expirations per root
    sbr = out["sample_expirations_by_root"]
    assert "SPX"  in sbr
    assert "SPXW" in sbr
    assert "2026-06-01" in sbr["SPXW"]


def test_resolve_root_for_chain_unavailable_propagates_cleanly():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        return httpx.Response(404, json={"detail": "not found"})

    p = _logged_in_probe(handler)
    out = p.resolve_root_for("XYZ", "2026-06-01")
    assert out["ok"] is False
    assert out["reason"] == "chain_unavailable"
    # No traceback would have surfaced if this returned cleanly.


# ──────────────────────────────────────────────────────────────────────
# 2. get_option_quotes_for_strikes — quote lookup with auto-resolution
# ──────────────────────────────────────────────────────────────────────

def test_quotes_for_strikes_auto_resolves_spx_to_spxw_for_0dte():
    """Dan's actual failure mode: --symbol SPX --expiry <0DTE> returned 0
    quotes because the OCC symbol used root=SPX instead of SPXW."""
    seen_quote_query: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        if req.url.path == "/market-data/by-type":
            seen_quote_query["equity-option"] = req.url.params.get("equity-option") or ""
            return httpx.Response(200, json=QUOTES_OK_SPXW)
        return httpx.Response(404)

    p = _logged_in_probe(handler)
    out = p.get_option_quotes_for_strikes("SPX", "2026-06-01", [7600.0, 7605.0], "C")
    assert out["ok"] is True
    assert out["requested_underlying_symbol"] == "SPX"
    assert out["resolved_root_symbol"]        == "SPXW"
    assert out["root_resolution_source"]      == "auto_chain"
    assert out["quote_count"]                 == 2
    # OCC symbols on the wire must carry SPXW, not SPX
    sent = seen_quote_query["equity-option"]
    assert "SPXW" in sent
    assert "SPX " not in sent.split(",")[0]   # root padding shouldn't be raw SPX


def test_quotes_for_strikes_uses_explicit_root_symbol_override():
    seen_quote_query: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        # NOTE: no /option-chains/* handler — explicit root must skip the chain call
        if req.url.path == "/market-data/by-type":
            seen_quote_query["equity-option"] = req.url.params.get("equity-option") or ""
            return httpx.Response(200, json=QUOTES_OK_SPXW)
        return httpx.Response(404)

    p = _logged_in_probe(handler)
    out = p.get_option_quotes_for_strikes(
        "SPX", "2026-06-01", [7600.0], "C", root_symbol="SPXW",
    )
    assert out["ok"] is True
    assert out["root_resolution_source"] == "explicit"
    assert out["resolved_root_symbol"]   == "SPXW"
    assert "SPXW" in seen_quote_query["equity-option"]


def test_quotes_for_strikes_unresolved_expiry_returns_sanitized_error():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        if req.url.path == "/market-data/by-type":
            # MUST NOT reach this — unresolved expiry should short-circuit
            return httpx.Response(500, json={"error": "should-not-be-called"})
        return httpx.Response(404)

    p = _logged_in_probe(handler)
    out = p.get_option_quotes_for_strikes("SPX", "2099-12-31", [7600.0], "C")
    assert out["ok"] is False
    assert out["resolved_root_symbol"]      is None
    assert out["root_resolution_source"]    == "unresolved"
    assert out["reason"]                    == "expiry_not_in_chain"
    assert out["quote_count"]               == 0
    assert out["requested_symbols"]         == []
    assert "SPXW" in out["available_roots"]
    # Diagnostics surface so the user can see what they SHOULD have asked for
    assert out["sample_expirations_by_root"]["SPXW"]


def test_quotes_for_strikes_output_schema_has_all_required_keys():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        if req.url.path == "/market-data/by-type":
            return httpx.Response(200, json=QUOTES_OK_SPXW)
        return httpx.Response(404)

    p = _logged_in_probe(handler)
    out = p.get_option_quotes_for_strikes("SPX", "2026-06-01", [7600.0, 7605.0], "C")
    # The task spec REQUIRES these exact fields
    for k in (
        "requested_underlying_symbol",
        "resolved_root_symbol",
        "root_resolution_source",
        "requested_symbols",
        "quote_count",
        "quotes",
    ):
        assert k in out, f"missing required key {k}"


# ──────────────────────────────────────────────────────────────────────
# 3. capabilities — optional quote probe
# ──────────────────────────────────────────────────────────────────────

def test_capabilities_runs_real_quote_probe_when_args_supplied():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json={"data": {"items": []}})
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        if req.url.path == "/market-data/by-type":
            return httpx.Response(200, json=QUOTES_OK_SPXW)
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    caps = p.capabilities_summary(
        "SPX",
        capability_expiry="2026-06-01",
        capability_strikes=[7600.0, 7605.0],
        capability_right="C",
    )
    assert caps["has_quotes"] is True
    assert caps["quote_probe_count"] == 2
    assert caps["quote_probe_resolved_root_symbol"] == "SPXW"
    assert caps["quote_probe_root_resolution_source"] in ("auto_chain", "direct_match")
    assert caps["quote_probe_http_status"] == 200


def test_capabilities_omits_quote_probe_without_args():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json={"data": {"items": []}})
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    caps = p.capabilities_summary("SPX")
    # Back-compat: without args, has_quotes is the legacy unknown string
    assert caps["has_quotes"] == "unknown_via_capabilities_use_quotes_subcmd"
    assert "quote_probe_count" not in caps


# ──────────────────────────────────────────────────────────────────────
# 4. missing_fields cosmetic fix — OAuth-complete suppresses legacy
# ──────────────────────────────────────────────────────────────────────

def test_missing_fields_oauth_complete_does_not_list_legacy_at_top_level():
    """The cosmetic bug Dan hit: --config reported TASTY_USERNAME +
    TASTY_PASSWORD as missing even though OAuth was fully configured."""
    cfg = TastyProbeConfig(
        client_id="abc", client_secret="def", refresh_token="ghi",
    )
    p = TastyProbeClient(cfg)
    summary = p.config_summary()
    assert summary["configured"] is True
    assert summary["auth_mode"] == "oauth"
    assert "oauth" in summary["usable_auth_modes"]
    # Top-level missing_fields is EMPTY when OAuth is complete
    assert summary["missing_fields"] == []
    # Per-mode breakdowns still surface for diagnostic visibility
    assert summary["oauth_missing_fields"] == []
    assert "TASTY_USERNAME" in summary["legacy_missing_fields"]
    assert "TASTY_PASSWORD" in summary["legacy_missing_fields"]


def test_missing_fields_legacy_complete_does_not_list_oauth_at_top_level():
    cfg = TastyProbeConfig(username="dan", password="hunter2")
    p = TastyProbeClient(cfg)
    summary = p.config_summary()
    assert summary["auth_mode"] == "legacy_session"
    assert "legacy_session" in summary["usable_auth_modes"]
    assert summary["missing_fields"] == []
    assert summary["legacy_missing_fields"] == []
    assert "TASTY_CLIENT_ID" in summary["oauth_missing_fields"]


def test_missing_fields_partial_oauth_shows_oauth_block_at_top_level():
    """When neither mode is complete AND OAuth has fewer missing fields,
    surface OAuth's missing list at top-level (user is closer to that mode)."""
    cfg = TastyProbeConfig(client_id="abc", client_secret="def")  # no refresh
    p = TastyProbeClient(cfg)
    summary = p.config_summary()
    assert summary["configured"] is False
    assert summary["usable_auth_modes"] == []
    assert summary["missing_fields"] == ["TASTY_REFRESH_TOKEN"]
    assert summary["oauth_missing_fields"]  == ["TASTY_REFRESH_TOKEN"]
    assert summary["legacy_missing_fields"] == ["TASTY_USERNAME", "TASTY_PASSWORD"]


# ──────────────────────────────────────────────────────────────────────
# 5. CLI smoke
# ──────────────────────────────────────────────────────────────────────

def test_cli_quotes_passes_root_symbol_through(monkeypatch, capsys):
    """End-to-end CLI: --root-symbol SPXW must reach get_option_quotes_for_strikes."""
    seen_calls: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/market-data/by-type":
            seen_calls["equity-option"] = req.url.params.get("equity-option") or ""
            return httpx.Response(200, json=QUOTES_OK_SPXW)
        return httpx.Response(404)

    fake_cfg = _cfg()
    fake_client = TastyProbeClient(fake_cfg, client_factory=_factory(handler))
    monkeypatch.setattr(
        "src.providers.quotes.tasty_probe.config_from_env",
        lambda: fake_cfg,
    )
    monkeypatch.setattr(
        "src.providers.quotes.tasty_probe.TastyProbeClient",
        lambda cfg, **_: fake_client,
    )
    monkeypatch.setattr(sys, "argv", [
        "scripts.probe_tastytrade", "--quotes",
        "--symbol", "SPX", "--root-symbol", "SPXW",
        "--expiry", "2026-06-01",
        "--strikes", "7600,7605", "--right", "C", "--json",
    ])

    import scripts.probe_tastytrade as cli
    rc = cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["resolved_root_symbol"] == "SPXW"
    assert parsed["root_resolution_source"] == "explicit"
    assert parsed["quote_count"] == 2
    # Quote query carried the SPXW root
    assert "SPXW" in seen_calls["equity-option"]


def test_cli_quotes_auto_resolves_when_root_omitted(monkeypatch, capsys):
    """No --root-symbol → auto-resolve SPX→SPXW for 0DTE expiry."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        if req.url.path == "/market-data/by-type":
            return httpx.Response(200, json=QUOTES_OK_SPXW)
        return httpx.Response(404)

    fake_cfg = _cfg()
    fake_client = TastyProbeClient(fake_cfg, client_factory=_factory(handler))
    monkeypatch.setattr(
        "src.providers.quotes.tasty_probe.config_from_env", lambda: fake_cfg,
    )
    monkeypatch.setattr(
        "src.providers.quotes.tasty_probe.TastyProbeClient",
        lambda cfg, **_: fake_client,
    )
    monkeypatch.setattr(sys, "argv", [
        "scripts.probe_tastytrade", "--quotes",
        "--symbol", "SPX",
        "--expiry", "2026-06-01",
        "--strikes", "7600,7605", "--right", "C", "--json",
    ])

    import scripts.probe_tastytrade as cli
    rc = cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["resolved_root_symbol"] == "SPXW"
    assert parsed["root_resolution_source"] == "auto_chain"
    assert parsed["quote_count"] == 2


def test_cli_capabilities_with_capability_quote_args(monkeypatch, capsys):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json={"data": {"items": []}})
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_REAL)
        if req.url.path == "/market-data/by-type":
            return httpx.Response(200, json=QUOTES_OK_SPXW)
        return httpx.Response(404)

    fake_cfg = _cfg()
    fake_client = TastyProbeClient(fake_cfg, client_factory=_factory(handler))
    monkeypatch.setattr(
        "src.providers.quotes.tasty_probe.config_from_env", lambda: fake_cfg,
    )
    monkeypatch.setattr(
        "src.providers.quotes.tasty_probe.TastyProbeClient",
        lambda cfg, **_: fake_client,
    )
    monkeypatch.setattr(sys, "argv", [
        "scripts.probe_tastytrade", "--capabilities",
        "--symbol", "SPX",
        "--capability-expiry", "2026-06-01",
        "--capability-strikes", "7600,7605",
        "--capability-right", "C",
        "--json",
    ])

    import scripts.probe_tastytrade as cli
    rc = cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["has_quotes"] is True
    assert parsed["quote_probe_count"] == 2
    assert parsed["quote_probe_resolved_root_symbol"] == "SPXW"


# ──────────────────────────────────────────────────────────────────────
# 6. safety gate unchanged
# ──────────────────────────────────────────────────────────────────────

def test_safety_gate_unchanged_after_phase3p1():
    """Phase 3.1 didn't touch the safety boundary — every Phase 3 guarantee
    still holds."""
    cfg = TastyProbeConfig(
        client_id="abc", client_secret="def", refresh_token="ghi",
        scopes=["read", "trade", "openid"],
        enable_order_submission=False,
    )
    p = TastyProbeClient(cfg)
    # Capabilities still report the gate as closed
    caps = p.status().sanitize()
    assert caps["trade_scope_present"] is True
    assert caps["order_submission_enabled"] is False
    assert caps["execution_blocked_by_safety_gate"] is True
    # submit_* still raise SafetyGateError
    with pytest.raises(SafetyGateError):
        p.submit_order()
    with pytest.raises(SafetyGateError):
        p.submit_complex_order()


# ──────────────────────────────────────────────────────────────────────
# 7. OCC builder helper math
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("root,expiry,strike,right,expected", [
    ("SPXW", "2026-06-01", 7600.0, "C", "SPXW  260601C07600000"),
    ("SPXW", "2026-06-01", 7605.0, "C", "SPXW  260601C07605000"),
    ("SPX",  "2026-06-19", 5800.0, "P", "SPX   260619P05800000"),
    ("SPXW", "2026-06-01", 5800.5, "C", "SPXW  260601C05800500"),  # half-dollar
])
def test_build_occ_option_symbol(root, expiry, strike, right, expected):
    assert _build_occ_option_symbol(root, expiry, strike, right) == expected


def test_build_occ_option_symbol_rejects_bad_inputs():
    with pytest.raises(ValueError):
        _build_occ_option_symbol("SPX", "06-01-2026", 5800.0, "C")  # wrong format
    with pytest.raises(ValueError):
        _build_occ_option_symbol("SPX", "2026-06-01", 5800.0, "X")  # bad right
