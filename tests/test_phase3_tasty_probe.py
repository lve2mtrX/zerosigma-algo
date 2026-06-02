"""Phase 3 — Tastytrade capability probe (read-only)."""

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
    _parse_scopes,
    _redact_account,
    config_from_env,
)

# ──────────────────────────────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────────────────────────────

SESSIONS_OK = {
    "data": {
        "user": {"email": "tester@example.test", "username": "tester",
                 "external-id": "ext-abc-123"},
        "session-token":  "fake-session-token-AAA",
        "remember-token": "fake-remember-token-BBB",
    },
    "context": "/sessions",
}

ACCOUNTS_OK = {
    "data": {
        "items": [
            {
                "account": {
                    "account-number":     "5WX12345",
                    "account-type-name":  "Margin",
                    "margin-or-cash":     "Margin",
                    "is-closed":          False,
                },
                "authority-level": "owner",
            },
            {
                "account": {
                    "account-number":     "5WX98765",
                    "account-type-name":  "Cash",
                    "margin-or-cash":     "Cash",
                    "is-closed":          False,
                },
                "authority-level": "owner",
            },
        ]
    }
}

CHAIN_NESTED_OK = {
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
                            {"strike-price": "5800.0", "call": "SPX...", "put": "SPX..."},
                            {"strike-price": "5810.0", "call": "SPX...", "put": "SPX..."},
                        ],
                    },
                ],
            },
            {
                "root-symbol": "SPXW",
                "underlying-symbol": "SPX",
                "expirations": [
                    {
                        "expiration-date": "2026-06-30",
                        "days-to-expiration": 0,
                        "settlement-type": "PM",
                        "strikes": [
                            {"strike-price": "5790.0", "call": "SPXW...", "put": "SPXW..."},
                            {"strike-price": "5800.0", "call": "SPXW...", "put": "SPXW..."},
                            {"strike-price": "5810.0", "call": "SPXW...", "put": "SPXW..."},
                            {"strike-price": "5820.0", "call": "SPXW...", "put": "SPXW..."},
                        ],
                    },
                ],
            },
        ]
    }
}

QUOTES_OK = {
    "data": {
        "items": [
            {
                "symbol": "SPXW  260630C00058000",
                "instrument-type": "Equity Option",
                "bid": "0.95", "ask": "1.05", "mid": "1.00", "last": "1.00",
                "mark": "1.00", "updated-at": "2026-06-30T14:30:00Z",
            },
        ]
    }
}

DXLINK_OK = {
    "data": {
        "token": "fake-dxlink-token-CCC",
        "dxlink-url": "wss://tasty-openapi-ws.dxfeed.com/realtime",
        "level": "api",
    }
}


def _factory(handler):
    """Wrap an httpx.MockTransport handler into a client_factory."""
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


# ──────────────────────────────────────────────────────────────────────
# 1. unconfigured + redaction safety
# ──────────────────────────────────────────────────────────────────────

def test_unconfigured_status_and_login_short_circuit():
    p = TastyProbeClient(TastyProbeConfig(env="certification"))
    assert p._cfg.is_configured() is False
    out = p.login()
    assert out["auth_success"] is False
    assert out["configured"] is False
    # Status object never carries secret values either
    s = p.status().sanitize()
    payload = json.dumps(s)
    for needle in ("password", "hunter2", "session-token", "Bearer "):
        assert needle not in payload


def test_redact_account_helper():
    assert _redact_account("5WX12345") == "****2345"
    assert _redact_account("12") == "****"
    assert _redact_account(None) == ""
    assert _redact_account("") == ""


def test_repr_never_includes_password_or_token():
    cfg = _cfg(account_number="5WX12345")
    p = TastyProbeClient(cfg)
    p._token = "fake-session-token-AAA"
    p._remember_token = "fake-remember-token-BBB"
    text = repr(cfg)
    assert "hunter2"               not in text
    assert "fake-session-token"    not in text
    assert "fake-remember-token"   not in text
    assert "5WX12345"              not in text   # full account redacted
    assert "****2345"              in text       # last-4 OK


# ──────────────────────────────────────────────────────────────────────
# 2. auth flow — bare token, sanitized output
# ──────────────────────────────────────────────────────────────────────

def test_login_sends_login_password_remember_me_and_records_token():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            seen["method"] = req.method
            seen["body"] = json.loads(req.content.decode("utf-8"))
            return httpx.Response(200, json=SESSIONS_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    out = p.login()
    assert seen["method"] == "POST"
    assert seen["body"] == {
        "login": "tester", "password": "hunter2", "remember-me": True,
    }
    assert out["auth_success"] is True
    assert out["token_received"] is True
    assert out["remember_token_received"] is True
    # Sanitized output: no raw token values
    payload = json.dumps(out)
    assert "fake-session-token-AAA" not in payload
    assert "fake-remember-token-BBB" not in payload
    # Stored internally only
    assert p._token == "fake-session-token-AAA"


def test_authenticated_requests_use_bare_token_no_bearer_prefix():
    seen_auth_headers: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        seen_auth_headers.append(req.headers.get("Authorization") or "")
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json=ACCOUNTS_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    p.login()
    p.list_accounts()
    assert seen_auth_headers, "no authenticated requests captured"
    for h in seen_auth_headers:
        # MUST be the bare session token, NOT `Bearer ...`
        assert h == "fake-session-token-AAA"
        assert not h.lower().startswith("bearer ")


def test_login_http_error_is_sanitized_no_traceback():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad creds"})

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    out = p.login()
    assert out["auth_success"] is False
    assert out["http_status"] == 401
    assert out["token_received"] is False


# ──────────────────────────────────────────────────────────────────────
# 3. accounts — redacted ids
# ──────────────────────────────────────────────────────────────────────

def test_list_accounts_redacts_account_numbers():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json=ACCOUNTS_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    p.login()
    out = p.list_accounts()
    assert out["ok"] is True
    assert out["accounts_count"] == 2
    text = json.dumps(out)
    # Full account numbers MUST NOT appear
    assert "5WX12345" not in text
    assert "5WX98765" not in text
    # Last-4 redacted forms MUST appear
    assert "****2345" in text
    assert "****8765" in text


# ──────────────────────────────────────────────────────────────────────
# 4. chain — summary mapping (SPX vs SPXW, 0DTE detection)
# ──────────────────────────────────────────────────────────────────────

def test_chain_summary_maps_roots_and_strike_counts():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    p.login()
    out = p.get_option_chain_summary("SPX")
    assert out["ok"] is True
    assert out["symbol"] == "SPX"
    assert out["supports_spx"]  is True
    assert out["supports_spxw"] is True
    roots = {r["root_symbol"]: r for r in out["roots"]}
    assert roots["SPX"]["strike_count"]  == 2
    assert roots["SPXW"]["strike_count"] == 4
    assert roots["SPXW"]["expirations_sample"] == ["2026-06-30"]


# ──────────────────────────────────────────────────────────────────────
# 5. quotes — bulk mapping
# ──────────────────────────────────────────────────────────────────────

def test_quotes_maps_bid_ask_mid_last_mark():
    seen_query = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/market-data/by-type":
            # capture the query-string filter
            seen_query["equity-option"] = req.url.params.get("equity-option")
            return httpx.Response(200, json=QUOTES_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    p.login()
    syms = ["SPXW  260630C00058000"]
    out = p.get_option_quotes(syms)
    assert out["ok"] is True
    assert out["requested_count"] == 1
    assert out["quote_count"] == 1
    q = out["quotes"][0]
    assert q["bid"]  == 0.95
    assert q["ask"]  == 1.05
    assert q["mid"]  == 1.00
    assert q["mark"] == 1.00
    # Query carried the OCC symbol verbatim
    assert seen_query["equity-option"] == "SPXW  260630C00058000"


def test_quotes_caps_at_100_symbols():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/market-data/by-type":
            # confirm the joined query has <= 100 comma-separated symbols
            joined = req.url.params.get("equity-option") or ""
            assert joined.count(",") <= 99
            return httpx.Response(200, json={"data": {"items": []}})
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    p.login()
    syms = [f"SYM_{i}" for i in range(150)]
    out = p.get_option_quotes(syms)
    assert out["ok"] is True
    # requested_count reflects what the user asked for, not the slice
    assert out["requested_count"] == 150


# ──────────────────────────────────────────────────────────────────────
# 6. DXLink token — fetched, websocket NOT opened
# ──────────────────────────────────────────────────────────────────────

def test_dxlink_token_returns_presence_no_websocket_opened():
    websocket_opens: list = []   # would record any WS attempt

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/api-quote-tokens":
            return httpx.Response(200, json=DXLINK_OK)
        # if the probe ever tried to open the WS, it would have to call
        # a wss:// URL — MockTransport doesn't even support that scheme.
        websocket_opens.append(req.url)
        return httpx.Response(500)

    p = TastyProbeClient(_cfg(use_dxlink=True), client_factory=_factory(handler))
    p.login()
    out = p.get_dxlink_token()
    assert out["ok"] is True
    assert out["token_present"] is True
    assert out["dxlink_url_present"] is True
    assert out["dxlink_url_host"] == "tasty-openapi-ws.dxfeed.com"
    # Critical: no websocket attempt
    assert websocket_opens == []


# ──────────────────────────────────────────────────────────────────────
# 7. probe NEVER implements order paths
# ──────────────────────────────────────────────────────────────────────

def test_probe_refuses_to_submit_anything():
    p = TastyProbeClient(_cfg())
    # Phase 3.5: submit_* paths raise SafetyGateError (richer context).
    # open_streaming stays NotImplementedError — it's a future-feature
    # gap, not a safety boundary.
    with pytest.raises(SafetyGateError):
        p.submit_order()
    with pytest.raises(SafetyGateError):
        p.submit_complex_order()
    with pytest.raises(NotImplementedError):
        p.open_streaming()


def test_probe_module_does_not_define_order_methods_beyond_safety_stubs():
    """The probe class exposes ONLY the read-only methods. No 'place_order',
    'route', 'execute', or similar."""
    from src.providers.quotes import tasty_probe
    members = dir(tasty_probe.TastyProbeClient)
    forbidden = {"place_order", "route", "execute", "preview", "dry_run"}
    leaks = forbidden & set(members)
    assert not leaks, f"probe class exposes forbidden methods: {leaks}"


# ──────────────────────────────────────────────────────────────────────
# 8. capabilities — non-fatal under live ZS API failure
# ──────────────────────────────────────────────────────────────────────

def test_capabilities_runs_full_sequence_under_mocked_transport():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json=ACCOUNTS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_OK)
        if req.url.path == "/api-quote-tokens":
            return httpx.Response(200, json=DXLINK_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_cfg(use_dxlink=True), client_factory=_factory(handler))
    caps = p.capabilities_summary("SPX")
    assert caps["has_auth"]    is True
    assert caps["has_accounts"] is True
    assert caps["has_chain"]    is True
    assert caps["chain_supports_spxw"] is True
    assert caps["has_streaming_token"] is True
    assert caps["has_paper_or_sandbox"] == "yes_certification"


def test_capabilities_under_auth_failure_returns_partial_matrix():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad creds"})

    p = TastyProbeClient(_cfg(), client_factory=_factory(handler))
    caps = p.capabilities_summary("SPX")
    assert caps["has_auth"] is False
    assert caps["has_accounts"] is False
    assert caps["has_chain"] is False
    assert caps["env"] == "certification"


# ──────────────────────────────────────────────────────────────────────
# 9. CLI smoke + sanitization
# ──────────────────────────────────────────────────────────────────────

def test_cli_help_renders_without_credentials(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scripts.probe_tastytrade", "--help"])
    import scripts.probe_tastytrade as cli
    with pytest.raises(SystemExit) as ex:
        cli.main()
    assert ex.value.code == 0
    out = capsys.readouterr().out
    assert "probe_tastytrade" in out
    assert "--auth-only"   in out
    assert "--capabilities" in out


def test_cli_unconfigured_returns_zero_and_warns(capsys, monkeypatch):
    # Clear EVERY TASTY_* env var (OAuth + legacy) so the developer's
    # local .env doesn't accidentally configure the probe.
    for v in ("TASTY_USERNAME", "TASTY_PASSWORD",
              "TASTY_CLIENT_ID", "TASTY_CLIENT_SECRET", "TASTY_REFRESH_TOKEN",
              "TASTY_ACCOUNT_NUMBER", "TASTY_SCOPES"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr("src.utils.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", ["scripts.probe_tastytrade", "--auth-only"])
    import scripts.probe_tastytrade as cli
    rc = cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    # Warning message references the credential blocks the user can set
    # (still mentions both auth modes by name)
    assert ("TASTY_USERNAME" in out) or ("TASTY_CLIENT_ID" in out)
    assert "Traceback"        not in out


def test_cli_capabilities_via_mocked_probe(monkeypatch, capsys):
    """Drive the CLI all the way through capabilities mode with a mocked
    probe client — confirms wiring + no live network."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json=ACCOUNTS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_OK)
        return httpx.Response(404)

    # Patch config_from_env so the CLI uses our handler + creds.
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
    monkeypatch.setattr(
        sys, "argv",
        ["scripts.probe_tastytrade", "--capabilities", "--symbol", "SPX", "--json"],
    )
    import scripts.probe_tastytrade as cli
    rc = cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    # JSON output includes the capability matrix
    parsed = json.loads(out)
    assert parsed["mode"] == "capabilities"
    assert parsed["has_auth"] is True
    assert parsed["has_chain"] is True
    # Never leaks the password / token
    assert "hunter2"             not in out
    assert "fake-session-token"  not in out


# ──────────────────────────────────────────────────────────────────────
# 10. config_from_env reads TASTY_* without leaks
# ──────────────────────────────────────────────────────────────────────

def test_config_from_env_reads_tasty_vars(monkeypatch):
    monkeypatch.setenv("TASTY_ENV",            "production")
    monkeypatch.setenv("TASTY_BASE_URL",       "https://override.example")
    monkeypatch.setenv("TASTY_USERNAME",       "tester")
    monkeypatch.setenv("TASTY_PASSWORD",       "hunter2")
    monkeypatch.setenv("TASTY_ACCOUNT_NUMBER", "5WX99988")
    monkeypatch.setenv("TASTY_USE_DXLINK",     "true")
    monkeypatch.setenv("TASTY_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("TASTY_VERIFY_SSL",     "false")
    cfg = config_from_env()
    assert cfg.env == "production"
    assert cfg.resolved_base_url() == "https://override.example"
    assert cfg.username == "tester"
    assert cfg.use_dxlink is True
    assert cfg.timeout_seconds == 20
    assert cfg.verify_ssl is False
    # __repr__ never includes the password
    assert "hunter2"  not in repr(cfg)
    assert "5WX99988" not in repr(cfg)


# ──────────────────────────────────────────────────────────────────────
# 11. Phase 3 extension — scope parser
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("read trade openid",   ["read", "trade", "openid"]),
    ("read,trade,openid",   ["read", "trade", "openid"]),
    ("read, trade openid",  ["read", "trade", "openid"]),  # mixed delim
    ("  READ  Trade  ",     ["read", "trade"]),            # case + whitespace
    ("read trade trade",    ["read", "trade"]),            # dedup
    ("",                    []),
    (None,                  []),
])
def test_parse_scopes_accepts_space_comma_or_mixed(raw, expected):
    assert _parse_scopes(raw) == expected


# ──────────────────────────────────────────────────────────────────────
# 12. Safety gate — defaults, trade-scope-alone, SafetyGateError
# ──────────────────────────────────────────────────────────────────────

def test_enable_order_submission_defaults_false(monkeypatch):
    """Even when every other TASTY_* is set, the safety gate stays CLOSED
    unless TASTY_ENABLE_ORDER_SUBMISSION is explicitly true."""
    for v in ("TASTY_ENABLE_ORDER_SUBMISSION", "TASTY_USERNAME", "TASTY_PASSWORD",
              "TASTY_CLIENT_ID", "TASTY_CLIENT_SECRET", "TASTY_REFRESH_TOKEN",
              "TASTY_SCOPES", "TASTY_ALLOW_TRADE_SCOPE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TASTY_SCOPES", "read trade openid")
    cfg = config_from_env()
    assert cfg.trade_scope_present() is True
    # The gate did NOT open just because trade scope is present.
    assert cfg.enable_order_submission is False
    # Probe reports the gate as blocking even when trade scope is granted.
    p = TastyProbeClient(cfg)
    s = p.status().sanitize()
    assert s["trade_scope_present"]               is True
    assert s["order_submission_enabled"]          is False
    assert s["execution_blocked_by_safety_gate"]  is True


def test_trade_scope_alone_does_not_enable_execution():
    cfg = TastyProbeConfig(
        scopes=["read", "trade", "openid"],
        enable_order_submission=False,           # gate explicitly closed
    )
    p = TastyProbeClient(cfg)
    caps = p.status().sanitize()
    assert caps["trade_scope_present"] is True
    assert caps["order_submission_enabled"] is False
    # Even with trade scope + a token present, calling submit must raise.
    p._token = "fake-token"
    p._auth_mode_used = "oauth"
    with pytest.raises(SafetyGateError):
        p.submit_order()
    with pytest.raises(SafetyGateError):
        p.submit_complex_order()


def test_safety_gate_message_mentions_trade_scope_and_phase3():
    cfg = TastyProbeConfig()
    p = TastyProbeClient(cfg)
    with pytest.raises(SafetyGateError) as ex1:
        p.submit_order()
    assert "Phase 3" in str(ex1.value)
    with pytest.raises(SafetyGateError) as ex2:
        p.submit_complex_order()
    assert "Phase 3" in str(ex2.value) or "read-only" in str(ex2.value)


# ──────────────────────────────────────────────────────────────────────
# 13. OAuth refresh flow — Bearer header, sanitized output
# ──────────────────────────────────────────────────────────────────────

OAUTH_TOKEN_OK = {
    "access_token": "fake-oauth-access-token-XYZ",
    "token_type":   "Bearer",
    "expires_in":   900,
}


def _oauth_cfg(**overrides):
    base = TastyProbeConfig(
        env="certification",
        client_id="fake-client-id",
        client_secret="fake-client-secret",
        refresh_token="fake-refresh-token",
        scopes=["read", "trade", "openid"],
        enable_order_submission=False,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_oauth_login_uses_refresh_token_grant_and_form_body():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/oauth/token":
            seen["method"] = req.method
            seen["content_type"] = req.headers.get("content-type", "").split(";")[0]
            # form-urlencoded body
            body_raw = req.content.decode("utf-8")
            seen["body_raw"] = body_raw
            return httpx.Response(200, json=OAUTH_TOKEN_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_oauth_cfg(), client_factory=_factory(handler))
    out = p.login()
    assert seen["method"] == "POST"
    assert seen["content_type"] == "application/x-www-form-urlencoded"
    # body has all three OAuth fields
    body = seen["body_raw"]
    assert "grant_type=refresh_token" in body
    assert "client_secret=fake-client-secret" in body
    assert "refresh_token=fake-refresh-token" in body
    # output is sanitized — no token value
    assert out["auth_success"] is True
    assert out["auth_mode"] == "oauth"
    assert out["token_received"] is True
    assert out["trade_scope_present"] is True
    assert out["order_submission_enabled"] is False
    payload = json.dumps(out)
    assert "fake-oauth-access-token-XYZ" not in payload
    assert "fake-client-secret"          not in payload
    assert "fake-refresh-token"          not in payload


def test_oauth_authenticated_requests_use_bearer_prefix():
    seen_auth_headers: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/oauth/token":
            return httpx.Response(200, json=OAUTH_TOKEN_OK)
        seen_auth_headers.append(req.headers.get("Authorization") or "")
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json=ACCOUNTS_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_oauth_cfg(), client_factory=_factory(handler))
    p.login()
    p.list_accounts()
    assert seen_auth_headers, "no authenticated requests captured"
    for h in seen_auth_headers:
        # OAuth flow MUST use `Bearer <token>` prefix
        assert h == "Bearer fake-oauth-access-token-XYZ"


def test_login_picks_oauth_when_both_oauth_and_legacy_present():
    """Precedence rule: OAuth wins when fully configured, even if
    username/password are also set."""
    cfg = _oauth_cfg(username="someone", password="hunter2")
    seen_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append(req.url.path)
        if req.url.path == "/oauth/token":
            return httpx.Response(200, json=OAUTH_TOKEN_OK)
        # /sessions would be the WRONG path here
        return httpx.Response(500)

    p = TastyProbeClient(cfg, client_factory=_factory(handler))
    out = p.login()
    assert out["auth_mode"] == "oauth"
    assert "/oauth/token" in seen_paths
    assert "/sessions" not in seen_paths


def test_oauth_login_http_error_is_sanitized():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    p = TastyProbeClient(_oauth_cfg(), client_factory=_factory(handler))
    out = p.login()
    assert out["auth_success"] is False
    assert out["auth_mode"] == "oauth"
    assert out["http_status"] == 400
    assert out["token_received"] is False


def test_oauth_unconfigured_short_circuits_without_http():
    calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(200, json={})

    cfg = TastyProbeConfig(client_id="abc", client_secret="def")  # no refresh_token
    p = TastyProbeClient(cfg, client_factory=_factory(handler))
    out = p.login_oauth()
    assert out["auth_success"] is False
    assert out["auth_mode"] == "oauth"
    assert "missing" in (out.get("reason") or "").lower()
    assert calls == []                  # NO http call attempted


# ──────────────────────────────────────────────────────────────────────
# 14. capabilities_summary surfaces trade_scope + safety gate
# ──────────────────────────────────────────────────────────────────────

def test_capabilities_includes_trade_scope_and_safety_gate():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/oauth/token":
            return httpx.Response(200, json=OAUTH_TOKEN_OK)
        if req.url.path == "/customers/me/accounts":
            return httpx.Response(200, json=ACCOUNTS_OK)
        if req.url.path == "/option-chains/SPX/nested":
            return httpx.Response(200, json=CHAIN_NESTED_OK)
        if req.url.path == "/api-quote-tokens":
            return httpx.Response(200, json=DXLINK_OK)
        return httpx.Response(404)

    p = TastyProbeClient(_oauth_cfg(use_dxlink=True), client_factory=_factory(handler))
    caps = p.capabilities_summary("SPX")
    assert caps["has_auth"] is True
    assert caps["trade_scope_present"]              is True
    assert caps["order_submission_enabled"]         is False
    assert caps["execution_blocked_by_safety_gate"] is True
    assert caps["probe_exposes_submit_path"]        is False
    assert caps["has_dxlink"] is True
    assert caps["has_certification_or_sandbox"]     is True


# ──────────────────────────────────────────────────────────────────────
# 15. --config CLI subcommand
# ──────────────────────────────────────────────────────────────────────

def test_cli_config_works_without_credentials(monkeypatch, capsys):
    """--config NEVER makes an HTTP call. Safe even with empty .env."""
    for v in ("TASTY_USERNAME", "TASTY_PASSWORD", "TASTY_CLIENT_ID",
              "TASTY_CLIENT_SECRET", "TASTY_REFRESH_TOKEN", "TASTY_SCOPES",
              "TASTY_ENABLE_ORDER_SUBMISSION", "TASTY_ALLOW_TRADE_SCOPE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr("src.utils.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", ["scripts.probe_tastytrade", "--config", "--json"])
    import scripts.probe_tastytrade as cli
    rc = cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["mode"] == "config"
    assert parsed["configured"] is False
    assert parsed["auth_mode"] == "none"
    assert parsed["order_submission_enabled"] is False
    assert parsed["execution_blocked_by_safety_gate"] is True
    # No credential values can possibly leak — none were set
    assert parsed["credentials_present"]["username"] is False
    assert parsed["credentials_present"]["password"] is False
    assert parsed["credentials_present"]["client_id"] is False
    assert parsed["credentials_present"]["client_secret"] is False
    assert parsed["credentials_present"]["refresh_token"] is False


def test_cli_config_with_partial_creds_lists_missing_fields(monkeypatch, capsys):
    monkeypatch.setattr("src.utils.config.load_dotenv", lambda *a, **k: False)
    for v in ("TASTY_USERNAME", "TASTY_PASSWORD", "TASTY_REFRESH_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TASTY_CLIENT_ID",         "abc")
    monkeypatch.setenv("TASTY_CLIENT_SECRET",     "def")
    monkeypatch.setenv("TASTY_SCOPES",            "read trade openid")
    monkeypatch.setenv("TASTY_ENABLE_ORDER_SUBMISSION", "false")
    monkeypatch.setattr(sys, "argv", ["scripts.probe_tastytrade", "--config", "--json"])
    import scripts.probe_tastytrade as cli
    rc = cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    # Phase 3.1: top-level missing_fields shows the SHORTER mode list
    # (OAuth is just one field away). Legacy still surfaces in per-mode.
    assert "TASTY_REFRESH_TOKEN" in parsed["missing_fields"]
    assert "TASTY_REFRESH_TOKEN" in parsed["oauth_missing_fields"]
    assert "TASTY_USERNAME"      in parsed["legacy_missing_fields"]
    assert "TASTY_PASSWORD"      in parsed["legacy_missing_fields"]
    assert parsed["usable_auth_modes"] == []     # neither mode complete
    assert parsed["trade_scope_present"] is True
    assert parsed["execution_blocked_by_safety_gate"] is True
    # Secret values never appear (client_secret 'def' must not be echoed)
    text = json.dumps(parsed)
    assert '"def"' not in text


def test_cli_config_runs_before_unconfigured_short_circuit(monkeypatch, capsys):
    """If --config required credentials, it would defeat its purpose. The
    main() flow must dispatch --config BEFORE the unconfigured warning."""
    for v in ("TASTY_USERNAME", "TASTY_PASSWORD", "TASTY_CLIENT_ID",
              "TASTY_CLIENT_SECRET", "TASTY_REFRESH_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr("src.utils.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", ["scripts.probe_tastytrade", "--config"])
    import scripts.probe_tastytrade as cli
    rc = cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    # Should be the config dump, NOT the unconfigured warning
    assert "WARNING" not in out.upper()
    assert "mode: config" in out
