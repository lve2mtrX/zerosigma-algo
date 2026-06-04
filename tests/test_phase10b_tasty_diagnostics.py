"""Phase 10B hotfix — Tasty quote-path diagnostics (read-only).

Each test forces ONE stage of the path to fail and asserts the diagnostic
surfaces the exact, sanitized reason: missing config, auth failure, root
unresolved, expiry unavailable, no chain, chain-but-invalid-quotes, plus the
happy path — and that NO secret value is ever echoed. Uses httpx.MockTransport
(no real network) via the probe's `client_factory` seam.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx

import scripts.diagnose_tasty_quotes as cli
from src.providers.quotes import tasty_diagnostics as diag
from src.providers.quotes.tasty_probe import (
    DEFAULT_BASE_URLS,
    TastyProbeConfig,
    _build_occ_option_symbol,
)

_NOW = datetime(2026, 6, 1, 14, 30, 0, tzinfo=UTC)   # Monday 14:30Z
_TODAY = "2026-06-01"
_SPOT = 7590.0
_LADDER = [7580.0, 7585.0, 7590.0, 7595.0, 7600.0]


def _factory(handler):
    transport = httpx.MockTransport(handler)

    def make() -> httpx.Client:
        return httpx.Client(base_url=DEFAULT_BASE_URLS["certification"], transport=transport)
    return make


def _cfg(**ov) -> TastyProbeConfig:
    base = TastyProbeConfig(env="certification", username="tester", password="hunter2")
    for k, v in ov.items():
        setattr(base, k, v)
    return base


SESSIONS_OK = {"data": {"session-token": "fake-session-token",
                        "remember-token": "fake-remember-token"}}

# SPX = monthly only; SPXW = today's 0DTE with an ATM strike ladder.
_CHAIN = {"data": {"items": [
    {"root-symbol": "SPX", "underlying-symbol": "SPX", "expirations": [
        {"expiration-date": "2026-06-19", "strikes": [{"strike-price": "5800.0"}]}]},
    {"root-symbol": "SPXW", "underlying-symbol": "SPX", "expirations": [
        {"expiration-date": _TODAY,
         "strikes": [{"strike-price": str(k)} for k in _LADDER]}]},
]}}

_CHAIN_NO_0DTE = {"data": {"items": [
    {"root-symbol": "SPX", "underlying-symbol": "SPX", "expirations": [
        {"expiration-date": "2026-06-19", "strikes": [{"strike-price": "5800.0"}]}]},
]}}


def _quote_items(*, bid, ask, ts):
    out = []
    for k in _LADDER:
        occ = _build_occ_option_symbol("SPXW", _TODAY, k, "C")
        out.append({"symbol": occ, "instrument-type": "Equity Option",
                    "bid": bid, "ask": ask, "mid": None, "mark": None,
                    "updated-at": ts})
    return {"data": {"items": out}}


def _run(handler, **kw):
    return diag.diagnose_quote_path(
        _cfg(**kw.pop("cfg", {})), symbol="SPX", target_dte=kw.pop("target_dte", 0),
        client_factory=_factory(handler), spot_hint=_SPOT, now=_NOW, **kw)


# ── 1. missing config ────────────────────────────────────────────────────────

def test_missing_config_reason():
    cfg = TastyProbeConfig(env="certification")   # no creds at all
    r = diag.diagnose_quote_path(cfg, symbol="SPX", target_dte=0, now=_NOW)
    assert r["configured"] is False and r["oauth_configured"] is False
    assert r["blocker"] == "not_configured"
    # OAuth-led message — NOT "missing username/password".
    assert "oauth credentials missing" in r["final_status"].lower()
    assert r["missing_config_fields"] == ["TASTY_CLIENT_ID", "TASTY_CLIENT_SECRET",
                                          "TASTY_REFRESH_TOKEN"]
    # legacy vars are reported SEPARATELY, never as the primary missing list.
    assert r["legacy_missing_fields"] == ["TASTY_USERNAME", "TASTY_PASSWORD"]


# ── 2. auth failure ──────────────────────────────────────────────────────────

def test_auth_failure_reason():
    def handler(req):
        if req.url.path == "/sessions":
            return httpx.Response(401, json={"error": {"code": "invalid_credentials"}})
        return httpx.Response(404)
    r = _run(handler)
    assert r["auth_success"] is False
    assert r["blocker"] == "auth_failed"
    assert r["final_status"] == "Tasty auth failed / session invalid."


def test_auth_network_error_is_non_fatal():
    def handler(req):
        raise httpx.ConnectError("connection refused")
    r = _run(handler)
    assert r["auth_success"] is False and r["blocker"] == "auth_failed"


# ── 3. root unresolved ───────────────────────────────────────────────────────

def test_root_unresolved_reason():
    def handler(req):
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path.endswith("/nested"):
            return httpx.Response(404)              # chain summary unavailable
        return httpx.Response(404)
    r = _run(handler)
    assert r["blocker"] == "root_unresolved"
    assert "root/expiry unresolved" in r["final_status"].lower()


# ── 4. expiry unavailable (no 0DTE today) ────────────────────────────────────

def test_expiry_unavailable_reason():
    def handler(req):
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path.endswith("/nested"):
            return httpx.Response(200, json=_CHAIN_NO_0DTE)
        return httpx.Response(404)
    r = _run(handler)
    assert r["blocker"] == "expiry_unavailable"
    assert "0dte expiration unavailable" in r["final_status"].lower()
    assert r["resolved_expiration"] is None


# ── 5. no chain returned ─────────────────────────────────────────────────────

def test_no_chain_returned_reason():
    def handler(req):
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path.endswith("/nested"):
            return httpx.Response(200, json=_CHAIN)
        if req.url.path == "/market-data/by-type":
            return httpx.Response(503)             # quote endpoint down
        return httpx.Response(404)
    r = _run(handler)
    assert r["resolved_root"] == "SPXW" and r["resolved_expiration"] == _TODAY
    assert r["chain_returned"] is False
    assert r["blocker"] == "no_chain"
    assert "no chain" in r["final_status"].lower()


# ── 6. chain returned but quotes invalid ─────────────────────────────────────

def test_chain_returned_but_invalid_quotes_reason():
    def handler(req):
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path.endswith("/nested"):
            return httpx.Response(200, json=_CHAIN)
        if req.url.path == "/market-data/by-type":
            # zero-bid quotes → every quote fails validation
            return httpx.Response(200, json=_quote_items(bid="0.0", ask="0.10",
                                                         ts=_NOW.isoformat()))
        return httpx.Response(404)
    r = _run(handler)
    assert r["chain_returned"] is True and r["quote_count"] == 5
    assert r["validation_passed_count"] == 0 and r["validation_failed_count"] == 5
    assert r["invalid_bid_ask_count"] == 5
    assert r["blocker"] == "quotes_invalid"
    assert "failed validation" in r["final_status"].lower()


def test_stale_quotes_flagged():
    old_ts = datetime(2026, 6, 1, 14, 0, 0, tzinfo=UTC).isoformat()   # 30 min old
    def handler(req):
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path.endswith("/nested"):
            return httpx.Response(200, json=_CHAIN)
        if req.url.path == "/market-data/by-type":
            return httpx.Response(200, json=_quote_items(bid="0.50", ask="0.60", ts=old_ts))
        return httpx.Response(404)
    r = _run(handler)
    assert r["chain_returned"] is True and r["stale_count"] == 5
    assert r["blocker"] == "quotes_invalid"


# ── 7. happy path ────────────────────────────────────────────────────────────

def test_happy_path_quotes_ok():
    def handler(req):
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path.endswith("/nested"):
            return httpx.Response(200, json=_CHAIN)
        if req.url.path == "/market-data/by-type":
            return httpx.Response(200, json=_quote_items(bid="0.50", ask="0.60",
                                                         ts=_NOW.isoformat()))
        return httpx.Response(404)
    r = _run(handler)
    assert r["auth_success"] is True
    assert r["resolved_root"] == "SPXW" and r["resolved_expiration"] == _TODAY
    assert r["chain_returned"] is True and r["quote_count"] == 5
    assert r["validation_passed_count"] == 5 and r["validation_failed_count"] == 0
    assert r["bid_ask_populated_count"] == 5 and r["missing_strikes"] == []
    assert r["strike_min"] == 7580.0 and r["strike_max"] == 7600.0
    assert r["blocker"] is None and "ok" in r["final_status"].lower()


# ── 8. no secrets ever echoed ────────────────────────────────────────────────

def test_no_secrets_in_diagnostic():
    secrets = {"username": "SUPER_SECRET_USER", "password": "SUPER_SECRET_PASS",
               "client_id": "cid", "client_secret": "SUPER_SECRET_CLIENT",
               "refresh_token": "SUPER_SECRET_REFRESH",
               "account_number": "9988776655"}
    def handler(req):
        if req.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok", "token_type": "Bearer"})
        if req.url.path == "/sessions":
            return httpx.Response(200, json=SESSIONS_OK)
        if req.url.path.endswith("/nested"):
            return httpx.Response(200, json=_CHAIN)
        if req.url.path == "/market-data/by-type":
            return httpx.Response(200, json=_quote_items(bid="0.50", ask="0.60",
                                                         ts=_NOW.isoformat()))
        return httpx.Response(404)
    r = diag.diagnose_quote_path(
        _cfg(**secrets), symbol="SPX", target_dte=0,
        client_factory=_factory(handler), spot_hint=_SPOT, now=_NOW)
    blob = str(r) + "\n".join(f"{a}{b}" for a, b in diag.summary_rows(r))
    for secret in ("SUPER_SECRET_USER", "SUPER_SECRET_PASS", "SUPER_SECRET_CLIENT",
                   "SUPER_SECRET_REFRESH", "9988776655"):
        assert secret not in blob


# ── 9. summary_rows + CLI wiring (no network) ────────────────────────────────

def test_summary_rows_has_final_and_core_fields():
    rows = diag.summary_rows(diag._blank_result("SPX", 0))
    labels = [lbl for lbl, _ in rows]
    assert "FINAL" in labels and "auth / session" in labels and "resolved root" in labels
    assert "quote count" in labels and "validation" in labels


def test_cli_prints_diagnostic(monkeypatch, capsys):
    canned = diag._blank_result("SPX", 0)
    canned["final_status"] = "Tasty auth failed / session invalid."
    canned["blocker"] = "auth_failed"
    monkeypatch.setattr(diag, "diagnose_from_env", lambda **kw: canned)
    rc = cli.main(["--symbol", "SPX", "--dte", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Tasty quote diagnostics" in out
    assert "Tasty auth failed / session invalid." in out
    assert "No secrets shown" in out


# ── OAuth/API config detection (the hotfix) ──────────────────────────────────

def _oauth_cfg(**ov) -> TastyProbeConfig:
    base = TastyProbeConfig(
        env="production", base_url="https://api.tastyworks.com",
        client_id="cid", client_secret="csecret", refresh_token="rtok",
        scopes=["read", "openid"])
    for k, v in ov.items():
        setattr(base, k, v)
    return base


def _oauth_handler(req):
    if req.url.path == "/oauth/token":
        return httpx.Response(200, json={"access_token": "tok", "token_type": "Bearer"})
    if req.url.path.endswith("/nested"):
        return httpx.Response(200, json=_CHAIN)
    if req.url.path == "/market-data/by-type":
        return httpx.Response(200, json=_quote_items(bid="0.50", ask="0.60",
                                                     ts=_NOW.isoformat()))
    return httpx.Response(404)


def _legacy_handler(req):
    if req.url.path == "/sessions":
        return httpx.Response(200, json=SESSIONS_OK)
    if req.url.path.endswith("/nested"):
        return httpx.Response(200, json=_CHAIN)
    if req.url.path == "/market-data/by-type":
        return httpx.Response(200, json=_quote_items(bid="0.50", ask="0.60",
                                                     ts=_NOW.isoformat()))
    return httpx.Response(404)


def test_oauth_configured_without_legacy_is_valid():
    r = diag.diagnose_quote_path(_oauth_cfg(), symbol="SPX", target_dte=0,
                                 client_factory=_factory(_oauth_handler),
                                 spot_hint=_SPOT, now=_NOW)
    assert r["configured"] is True
    assert r["oauth_configured"] is True and r["legacy_configured"] is False
    assert r["auth_mode"] == "oauth"
    assert r["oauth_missing_fields"] == []
    assert r["blocker"] != "not_configured"
    assert "oauth credentials found" in r["auth_summary"].lower()
    assert r["auth_success"] is True             # /oauth/token succeeded


def test_oauth_present_not_marked_unconfigured_even_when_auth_fails():
    def handler(req):
        if req.url.path == "/oauth/token":
            return httpx.Response(401, json={"error": "invalid_grant"})
        return httpx.Response(404)
    r = diag.diagnose_quote_path(_oauth_cfg(), symbol="SPX", target_dte=0,
                                 client_factory=_factory(handler), now=_NOW)
    assert r["configured"] is True and r["oauth_configured"] is True
    assert r["blocker"] == "auth_failed"          # NOT not_configured
    assert r["final_status"] == "Tasty auth failed / session invalid."


def test_legacy_only_is_optional_fallback():
    cfg = TastyProbeConfig(env="certification", username="u", password="p")
    r = diag.diagnose_quote_path(cfg, symbol="SPX", target_dte=0,
                                 client_factory=_factory(_legacy_handler),
                                 spot_hint=_SPOT, now=_NOW)
    assert r["configured"] is True
    assert r["oauth_configured"] is False and r["legacy_configured"] is True
    assert r["auth_mode"] == "legacy_session"
    assert "legacy" in r["auth_summary"].lower()


def test_missing_var_names_only_not_values():
    # OAuth half-configured (no refresh token) → missing NAME only; secret
    # VALUES never appear in the result.
    cfg = TastyProbeConfig(env="production", client_id="CID_SECRET_VAL",
                           client_secret="CSECRET_SECRET_VAL")
    r = diag.diagnose_quote_path(cfg, symbol="SPX", target_dte=0, now=_NOW)
    assert r["configured"] is False
    assert r["oauth_missing_fields"] == ["TASTY_REFRESH_TOKEN"]
    blob = str(r) + "\n".join(f"{a}{b}" for a, b in diag.summary_rows(r))
    assert "CID_SECRET_VAL" not in blob and "CSECRET_SECRET_VAL" not in blob


def test_quote_provider_mock_warning(monkeypatch):
    monkeypatch.setenv("QUOTE_PROVIDER", "mock")
    r = diag.diagnose_quote_path(_oauth_cfg(), symbol="SPX", target_dte=0,
                                 client_factory=_factory(_oauth_handler),
                                 spot_hint=_SPOT, now=_NOW)
    assert r["quote_provider"] == "mock"
    assert r["quote_provider_warning"] and "live tasty" in r["quote_provider_warning"].lower()
    assert any("quote provider" in lbl.lower() for lbl, _ in diag.summary_rows(r))


def test_quote_provider_tastytrade_no_warning(monkeypatch):
    monkeypatch.setenv("QUOTE_PROVIDER", "tastytrade")
    r = diag.diagnose_quote_path(_oauth_cfg(), symbol="SPX", target_dte=0,
                                 client_factory=_factory(_oauth_handler),
                                 spot_hint=_SPOT, now=_NOW)
    assert r["quote_provider"] == "tastytrade" and r["quote_provider_warning"] is None


def test_trade_scope_warning_does_not_enable_execution():
    cfg = _oauth_cfg(scopes=["read", "trade", "openid"], allow_trade_scope=True,
                     enable_order_submission=False)
    r = diag.diagnose_quote_path(cfg, symbol="SPX", target_dte=0,
                                 client_factory=_factory(_oauth_handler),
                                 spot_hint=_SPOT, now=_NOW)
    assert r["trade_scope_present"] is True and r["allow_trade_scope"] is True
    assert r["order_submission_enabled"] is False
    assert r["trade_scope_warning"] and "read-only" in r["trade_scope_warning"].lower()
    assert r["blocker"] != "not_configured"      # trade scope never blocks/fails


def test_no_execution_paths_in_diagnostics():
    repo = Path(__file__).resolve().parents[1]
    forbidden = ("submit_order", "place_order", "preview_order", "create_order",
                 "order_preview", "/orders", "/complex-orders", "dry-run")
    for rel in ("src/providers/quotes/tasty_diagnostics.py",
                "scripts/diagnose_tasty_quotes.py"):
        text = (repo / rel).read_text(encoding="utf-8").lower()
        for tok in forbidden:
            assert tok not in text, f"{rel} contains {tok!r}"
