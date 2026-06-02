"""Tastytrade capability probe — READ-ONLY scaffold for Phase 3.

This module is NOT the production `TastytradeQuoteProvider`. It is a
deliberately narrow client that exists ONLY to answer the question:
*"can this account auth, list accounts, fetch the SPX chain, and pull
per-strike quotes from Tastytrade?"*

What it does:
    - POST /sessions  (legacy session-token flow; OAuth2 land later)
    - GET  /customers/me/accounts
    - GET  /option-chains/{symbol}/nested
    - GET  /market-data/by-type?equity-option=...
    - GET  /api-quote-tokens   (token only — does NOT open the WebSocket)

What it deliberately does NOT do (raises NotImplementedError):
    - POST /orders                  (live single-leg submit)
    - POST /complex-orders          (live multi-leg submit)
    - POST /orders/dry-run          (no routing — but still gated; see CLI)
    - POST /complex-orders/dry-run  (no routing — but still gated)
    - DXLink WebSocket connect      (token-only here)

Auth flow (legacy /sessions):
    POST /sessions  body={login, password, remember-me:true}
    → data.session-token
    Subsequent requests: `Authorization: <token>`  (BARE — no Bearer prefix)
See `docs/reference_notes.md §8b` for the full contract + sources.

Safety contract:
    - `status()` and `__repr__` NEVER include token, password, or full
      account number values. Account numbers are redacted to the last 4
      chars.
    - Constructor stores secrets in private attrs; no `__dict__`-style
      logging happens.
    - HTTP client is injectable via `client_factory` so tests use
      `httpx.MockTransport` — no real network in CI.
    - Isolated from the scanner: no strategy / scanner imports; no
      production QuoteProvider wiring.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from src.utils.logging import get_logger

log = get_logger("provider.tasty_probe")


# Default production / certification hosts. The probe picks one from
# `TASTY_ENV` unless `TASTY_BASE_URL` is set explicitly.
DEFAULT_BASE_URLS = {
    "production":    "https://api.tastyworks.com",
    "certification": "https://api.cert.tastyworks.com",
}
DEFAULT_USER_AGENT = "zerosigma-algo/0.1 (probe)"

# Scope name used by Tastytrade OAuth to grant order-submission rights.
# The probe NEVER submits orders; we surface its presence as informational
# only — execution requires the SEPARATE `TASTY_ENABLE_ORDER_SUBMISSION`
# safety gate AND a deliberate future code change.
TRADE_SCOPE_NAME = "trade"


class SafetyGateError(RuntimeError):
    """Raised when code tries to perform an account-changing action while
    the safety gate is closed.

    The gate is `TASTY_ENABLE_ORDER_SUBMISSION` (default False). In this
    phase the gate is ALWAYS closed by code — the probe does not expose
    any submit path. The class exists so future execution code can fail
    loudly and consistently if it ever tries to bypass the gate.
    """


def _redact_account(account_number: str | None) -> str:
    """Redact an account number to '****1234' for safe display."""
    if not account_number:
        return ""
    s = str(account_number)
    return "****" + s[-4:] if len(s) >= 4 else "****"


def _build_occ_option_symbol(
    root: str,
    expiry: str,
    strike: float,
    right: str,
) -> str:
    """Build an OCC 21-char option symbol.

    Format: ROOT(6, space-padded) YYMMDD R STRIKE(8, milli-dollars)
    Example: SPXW  260620C00050000 → SPXW + 260620 + C + 00050000
    """
    parts = expiry.split("-")
    if len(parts) != 3 or len(parts[0]) != 4:
        raise ValueError(f"expiry must be YYYY-MM-DD, got {expiry!r}")
    yymmdd = f"{parts[0][2:]}{parts[1]}{parts[2]}"
    r = right.upper().strip()
    if r not in ("C", "P"):
        raise ValueError(f"right must be C or P, got {right!r}")
    root_padded = root.upper().strip().ljust(6, " ")
    # OCC strike is integer milli-dollars (8 digits), padded with zeros.
    strike_int = round(float(strike) * 1000)
    if strike_int < 0 or strike_int > 99999999:
        raise ValueError(f"strike {strike} outside OCC encodable range")
    return f"{root_padded}{yymmdd}{r}{strike_int:08d}"


def _parse_scopes(scopes_raw: str | None) -> list[str]:
    """Normalize a scopes string into a list of lowercase scope names.

    Accepts both formats the user might put in `.env`:
        TASTY_SCOPES=read trade openid          (space-delimited)
        TASTY_SCOPES=read,trade,openid          (comma-delimited)
        TASTY_SCOPES=read, trade openid         (mixed — both delimiters OK)
    Returns an empty list when input is None / empty.
    """
    if not scopes_raw:
        return []
    # Split on commas first, then on whitespace inside each piece.
    out: list[str] = []
    for chunk in str(scopes_raw).split(","):
        for piece in chunk.split():
            piece = piece.strip().lower()
            if piece and piece not in out:
                out.append(piece)
    return out


# ──────────────────────────────────────────────────────────────────────
# Config + status
# ──────────────────────────────────────────────────────────────────────

@dataclass
class TastyProbeConfig:
    """Probe configuration. Secrets are present here but are NEVER printed.

    Auth precedence inside `login()`:
        1. OAuth refresh token  →  POST /oauth/token (Bearer header thereafter)
        2. Legacy /sessions     →  POST /sessions (BARE token header thereafter)
        3. Neither              →  `is_configured()` returns False, no HTTP.
    """
    # ── environment ─────────────────────────────────────────────────────
    env:                str             = "certification"   # "production" | "certification"
    base_url:           str | None      = None
    account_number:     str | None      = None

    # ── auth: legacy /sessions ──────────────────────────────────────────
    username:           str | None      = None
    password:           str | None      = None

    # ── auth: OAuth (Personal OAuth Application) ────────────────────────
    # `client_secret` is set in the Tasty dev UI when registering the app;
    # `refresh_token` is captured ONCE manually via the authorization-code
    # flow (out-of-band), then dropped in .env so this probe can do the
    # refresh dance non-interactively.
    client_id:          str | None      = None
    client_secret:      str | None      = None
    redirect_uri:       str | None      = None    # informational only
    refresh_token:      str | None      = None
    scopes:             list[str]       = field(default_factory=list)

    # ── safety gates (Phase 3 extension) ─────────────────────────────────
    # `allow_trade_scope` lets the OAuth app advertise the `trade` scope
    # without the probe complaining — useful when the app is registered
    # for future execution but the probe must not submit orders.
    allow_trade_scope:        bool      = True
    # The HARD gate. False (default) prevents any submit path from ever
    # running, regardless of scope, regardless of broker. The probe does
    # not expose a submit path at all; this gate exists so future code
    # has one explicit place to check.
    enable_order_submission:  bool      = False

    # ── transport ───────────────────────────────────────────────────────
    use_dxlink:         bool            = False
    timeout_seconds:    int             = 10
    verify_ssl:         bool            = True
    user_agent:         str             = DEFAULT_USER_AGENT

    # ── derived ─────────────────────────────────────────────────────────
    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        env = (self.env or "certification").strip().lower()
        return DEFAULT_BASE_URLS.get(env, DEFAULT_BASE_URLS["certification"])

    def has_oauth(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def has_legacy_session(self) -> bool:
        return bool(self.username and self.password)

    def is_configured(self) -> bool:
        return self.has_oauth() or self.has_legacy_session()

    def auth_mode(self) -> str:
        if self.has_oauth():
            return "oauth"
        if self.has_legacy_session():
            return "legacy_session"
        return "none"

    def trade_scope_present(self) -> bool:
        return TRADE_SCOPE_NAME in (self.scopes or [])

    def usable_auth_modes(self) -> list[str]:
        """Which auth modes are FULLY configured right now."""
        out: list[str] = []
        if self.has_oauth():
            out.append("oauth")
        if self.has_legacy_session():
            out.append("legacy_session")
        return out

    def oauth_missing_fields(self) -> list[str]:
        out: list[str] = []
        for name, val in (
            ("TASTY_CLIENT_ID",     self.client_id),
            ("TASTY_CLIENT_SECRET", self.client_secret),
            ("TASTY_REFRESH_TOKEN", self.refresh_token),
        ):
            if not val:
                out.append(name)
        return out

    def legacy_missing_fields(self) -> list[str]:
        out: list[str] = []
        if not self.username:
            out.append("TASTY_USERNAME")
        if not self.password:
            out.append("TASTY_PASSWORD")
        return out

    def missing_fields(self) -> dict[str, Any]:
        """Per-mode breakdown of which credential blocks aren't filled.

        Phase 3.1 fix: when at least one auth mode is complete, the
        TOP-LEVEL `missing_fields` key (used by config_summary) is empty.
        Per-mode lists still surface so the user can see what's left if
        they wanted to set up the other mode too.

        Returns:
            {
              oauth_missing_fields:  [...],
              legacy_missing_fields: [...],
              usable_auth_modes:     ['oauth', 'legacy_session'],
              fully_configured:      bool,
            }
        """
        usable = self.usable_auth_modes()
        return {
            "oauth_missing_fields":  self.oauth_missing_fields(),
            "legacy_missing_fields": self.legacy_missing_fields(),
            "usable_auth_modes":     usable,
            "fully_configured":      bool(usable),
        }

    def __repr__(self) -> str:           # never echo secrets
        return (
            f"TastyProbeConfig(env={self.env!r}, "
            f"base_url={self.resolved_base_url()!r}, "
            f"auth_mode={self.auth_mode()!r}, "
            f"username_present={bool(self.username)}, "
            f"password_present={bool(self.password)}, "
            f"client_id_present={bool(self.client_id)}, "
            f"client_secret_present={bool(self.client_secret)}, "
            f"refresh_token_present={bool(self.refresh_token)}, "
            f"scopes={self.scopes!r}, "
            f"trade_scope_present={self.trade_scope_present()}, "
            f"enable_order_submission={self.enable_order_submission}, "
            f"account={_redact_account(self.account_number)!r}, "
            f"use_dxlink={self.use_dxlink})"
        )


@dataclass
class TastyProbeStatus:
    """Sanitized health summary — safe to print / serialize."""
    configured:           bool
    env:                  str
    base_url:             str
    auth_mode:            str = "none"             # oauth | legacy_session | none
    auth_attempted:       bool = False
    auth_success:         bool | None = None
    session_token_present: bool = False
    last_http_status:     int | None = None
    last_error:           str | None = None        # exception TYPE only
    accounts_count:       int | None = None
    # Phase 3 extension — trade scope + safety gate
    scopes:                       list[str] = field(default_factory=list)
    trade_scope_present:          bool = False
    order_submission_enabled:     bool = False
    execution_blocked_by_safety_gate: bool = True
    capabilities:         dict[str, Any] = field(default_factory=dict)

    def sanitize(self) -> dict[str, Any]:
        return {
            "provider":              "tasty_probe",
            "configured":            self.configured,
            "env":                   self.env,
            "base_url":              self.base_url,
            "auth_mode":             self.auth_mode,
            "auth_attempted":        self.auth_attempted,
            "auth_success":          self.auth_success,
            "session_token_present": self.session_token_present,
            "last_http_status":      self.last_http_status,
            "last_error":            self.last_error,
            "accounts_count":        self.accounts_count,
            # safety gate + scope reporting
            "scopes":                list(self.scopes),
            "trade_scope_present":   self.trade_scope_present,
            "order_submission_enabled": self.order_submission_enabled,
            "execution_blocked_by_safety_gate": self.execution_blocked_by_safety_gate,
            "capabilities":          dict(self.capabilities),
        }


# ──────────────────────────────────────────────────────────────────────
# Probe client
# ──────────────────────────────────────────────────────────────────────

class TastyProbeClient:
    """Narrow client for the Phase 3 capability probe.

    Methods return SANITIZED summaries (dicts) — never raw payloads,
    never the session token. Tests inject `client_factory` to use
    `httpx.MockTransport`.
    """

    name = "tasty_probe"

    def __init__(
        self,
        config: TastyProbeConfig,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._cfg = config
        self._token: str | None = None
        self._remember_token: str | None = None
        self._auth_mode_used: str | None = None      # "oauth" | "legacy_session"
        self._last_http_status: int | None = None
        self._last_error: str | None = None
        self._client_factory = client_factory

    # ── housekeeping ──────────────────────────────────────────────────

    def _build_client(self) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(
            base_url=self._cfg.resolved_base_url(),
            timeout=self._cfg.timeout_seconds,
            verify=self._cfg.verify_ssl,
            headers={"User-Agent": self._cfg.user_agent},
        )

    def _auth_headers(self) -> dict[str, str]:
        """Tasty Authorization header.

        Two formats coexist:
          - Legacy /sessions: BARE token, no `Bearer ` prefix.
          - OAuth /oauth/token: `Bearer <access_token>` per OAuth 2.0.

        Picks the right one based on which login flow succeeded.
        """
        if not self._token:
            raise RuntimeError("Tasty probe: not authenticated yet")
        if self._auth_mode_used == "oauth":
            return {"Authorization": f"Bearer {self._token}"}
        return {"Authorization": self._token}      # legacy session = BARE

    def _record(self, response: httpx.Response) -> None:
        self._last_http_status = response.status_code

    def status(self) -> TastyProbeStatus:
        # Safety-gate truth source: the gate is OPEN only when the config
        # opts in. Anything else (default, scope-only, etc.) → blocked.
        gate_open = bool(self._cfg.enable_order_submission)
        return TastyProbeStatus(
            configured=self._cfg.is_configured(),
            env=self._cfg.env,
            base_url=self._cfg.resolved_base_url(),
            auth_mode=self._cfg.auth_mode(),
            auth_attempted=self._token is not None or self._last_error is not None,
            auth_success=(self._token is not None) if self._last_error is None else False,
            session_token_present=self._token is not None,
            last_http_status=self._last_http_status,
            last_error=self._last_error,
            scopes=list(self._cfg.scopes),
            trade_scope_present=self._cfg.trade_scope_present(),
            order_submission_enabled=gate_open,
            execution_blocked_by_safety_gate=not gate_open,
        )

    def config_summary(self) -> dict[str, Any]:
        """Sanitized config dump — for the CLI `--config` subcommand.

        Returns ONLY whether each credential field is present, never the
        value. Trade scope + safety-gate state always surface.

        Phase 3.1: per-mode missing_fields. Top-level `missing_fields` is
        empty when at least one auth mode is fully configured (back-compat:
        old callers that just `len(out["missing_fields"]) == 0`-test will
        report 'configured' as long as either OAuth OR legacy is complete).
        """
        mf = self._cfg.missing_fields()
        return {
            "provider":                "tasty_probe",
            "configured":              self._cfg.is_configured(),
            "auth_mode":               self._cfg.auth_mode(),
            "usable_auth_modes":       mf["usable_auth_modes"],
            "missing_fields":          [] if mf["fully_configured"] else (
                # Show whichever mode the user appears to be trying to set
                # up (whichever has FEWER missing fields). When neither is
                # touched at all, include both lists.
                mf["oauth_missing_fields"]
                if len(mf["oauth_missing_fields"]) <= len(mf["legacy_missing_fields"])
                else mf["legacy_missing_fields"]
            ),
            "oauth_missing_fields":    mf["oauth_missing_fields"],
            "legacy_missing_fields":   mf["legacy_missing_fields"],
            "env":                     self._cfg.env,
            "base_url":                self._cfg.resolved_base_url(),
            "redirect_uri":            self._cfg.redirect_uri,   # safe — not a secret
            "scopes":                  list(self._cfg.scopes),
            "trade_scope_present":     self._cfg.trade_scope_present(),
            "trade_scope_allowed":     self._cfg.allow_trade_scope,
            "order_submission_enabled":          self._cfg.enable_order_submission,
            "execution_blocked_by_safety_gate":  not self._cfg.enable_order_submission,
            "execution_status_note":   (
                "trade scope is FUTURE-only; this phase NEVER submits orders"
                if self._cfg.trade_scope_present()
                else "no trade scope — read-only OAuth"
            ),
            "credentials_present": {
                "username":       bool(self._cfg.username),
                "password":       bool(self._cfg.password),
                "client_id":      bool(self._cfg.client_id),
                "client_secret":  bool(self._cfg.client_secret),
                "refresh_token":  bool(self._cfg.refresh_token),
                "account_number": bool(self._cfg.account_number),
            },
            "account_redacted":        _redact_account(self._cfg.account_number),
            "use_dxlink":              self._cfg.use_dxlink,
            "timeout_seconds":         self._cfg.timeout_seconds,
            "verify_ssl":              self._cfg.verify_ssl,
        }

    # ── auth ─────────────────────────────────────────────────────────

    def login(self) -> dict[str, Any]:
        """Authenticate via OAuth first, fall back to legacy /sessions.

        Returns a sanitized dict — never the raw token. The chosen
        auth mode appears in `auth_mode`.
        """
        if not self._cfg.is_configured():
            return {
                "auth_success":   False,
                "configured":     False,
                "env":            self._cfg.env,
                "base_url":       self._cfg.resolved_base_url(),
                "auth_mode":      "none",
                "reason":         (
                    "no auth configured — set TASTY_CLIENT_ID + "
                    "TASTY_CLIENT_SECRET + TASTY_REFRESH_TOKEN (OAuth) "
                    "OR TASTY_USERNAME + TASTY_PASSWORD (legacy)"
                ),
                "token_received": False,
            }
        if self._cfg.has_oauth():
            return self.login_oauth()
        return self.login_legacy_session()

    def login_oauth(self) -> dict[str, Any]:
        """POST /oauth/token with grant_type=refresh_token. Returns a
        sanitized dict; access_token is stored internally only.

        Tastytrade's OAuth refresh endpoint expects the form fields
        `grant_type=refresh_token&client_secret=...&refresh_token=...`.
        Body is form-urlencoded (per OAuth 2.0 spec compliance — the
        ONE Tasty endpoint that's NOT kebab-case JSON).
        """
        if not self._cfg.has_oauth():
            return {
                "auth_success":   False,
                "auth_mode":      "oauth",
                "reason":         "missing TASTY_CLIENT_ID / SECRET / REFRESH_TOKEN",
                "token_received": False,
            }
        try:
            with self._build_client() as c:
                r = c.post(
                    "/oauth/token",
                    data={
                        "grant_type":    "refresh_token",
                        "client_secret": self._cfg.client_secret,
                        "refresh_token": self._cfg.refresh_token,
                    },
                )
                self._record(r)
                if r.status_code >= 400:
                    self._last_error = f"HTTP {r.status_code}"
                    return {
                        "auth_success":   False,
                        "auth_mode":      "oauth",
                        "env":            self._cfg.env,
                        "base_url":       self._cfg.resolved_base_url(),
                        "http_status":    r.status_code,
                        "token_received": False,
                    }
                body = r.json() or {}
                self._token = body.get("access_token") or None
                self._auth_mode_used = "oauth" if self._token else None
                return {
                    "auth_success":   bool(self._token),
                    "auth_mode":      "oauth",
                    "env":            self._cfg.env,
                    "base_url":       self._cfg.resolved_base_url(),
                    "http_status":    r.status_code,
                    "token_received": self._token is not None,
                    "token_type":     body.get("token_type"),
                    "expires_in":     body.get("expires_in"),
                    "scopes_present": list(self._cfg.scopes),
                    "trade_scope_present":    self._cfg.trade_scope_present(),
                    "order_submission_enabled": self._cfg.enable_order_submission,
                }
        except Exception as exc:
            self._last_error = type(exc).__name__
            return {
                "auth_success":   False,
                "auth_mode":      "oauth",
                "env":            self._cfg.env,
                "base_url":       self._cfg.resolved_base_url(),
                "error_type":     type(exc).__name__,
                "token_received": False,
            }

    def login_legacy_session(self) -> dict[str, Any]:
        """POST /sessions with login + password + remember-me (legacy)."""
        if not self._cfg.has_legacy_session():
            return {
                "auth_success":   False,
                "auth_mode":      "legacy_session",
                "reason":         "missing TASTY_USERNAME / TASTY_PASSWORD",
                "token_received": False,
            }
        try:
            with self._build_client() as c:
                r = c.post(
                    "/sessions",
                    json={
                        "login":       self._cfg.username,
                        "password":    self._cfg.password,
                        "remember-me": True,
                    },
                )
                self._record(r)
                if r.status_code >= 400:
                    self._last_error = f"HTTP {r.status_code}"
                    return {
                        "auth_success":   False,
                        "auth_mode":      "legacy_session",
                        "env":            self._cfg.env,
                        "base_url":       self._cfg.resolved_base_url(),
                        "http_status":    r.status_code,
                        "token_received": False,
                    }
                data = (r.json().get("data") or {})
                self._token = data.get("session-token") or None
                self._remember_token = data.get("remember-token") or None
                self._auth_mode_used = "legacy_session" if self._token else None
                return {
                    "auth_success":           bool(self._token),
                    "auth_mode":              "legacy_session",
                    "env":                    self._cfg.env,
                    "base_url":               self._cfg.resolved_base_url(),
                    "http_status":            r.status_code,
                    "token_received":         self._token is not None,
                    "remember_token_received": self._remember_token is not None,
                }
        except Exception as exc:
            self._last_error = type(exc).__name__
            return {
                "auth_success":   False,
                "auth_mode":      "legacy_session",
                "env":            self._cfg.env,
                "base_url":       self._cfg.resolved_base_url(),
                "error_type":     type(exc).__name__,
                "token_received": False,
            }

    # ── account ──────────────────────────────────────────────────────

    def list_accounts(self) -> dict[str, Any]:
        """GET /customers/me/accounts → sanitized summary (redacted ids)."""
        if not self._token:
            return {"ok": False, "reason": "not_authenticated"}
        with self._build_client() as c:
            r = c.get("/customers/me/accounts", headers=self._auth_headers())
            self._record(r)
            if r.status_code >= 400:
                return {"ok": False, "http_status": r.status_code}
            items = (((r.json().get("data") or {}).get("items")) or [])
            accounts: list[dict[str, Any]] = []
            for it in items:
                acct = it.get("account") or {}
                num = acct.get("account-number")
                accounts.append({
                    "account_redacted": _redact_account(num),
                    "account_type":     acct.get("account-type-name"),
                    "margin_or_cash":   acct.get("margin-or-cash"),
                    "is_closed":        acct.get("is-closed", False),
                    "authority_level":  it.get("authority-level"),
                })
            return {
                "ok":             True,
                "http_status":    r.status_code,
                "accounts_count": len(accounts),
                "accounts":       accounts,
            }

    # ── option chain ─────────────────────────────────────────────────

    def get_option_chain_summary(self, symbol: str) -> dict[str, Any]:
        """GET /option-chains/{symbol}/nested → expirations + strike counts.

        Returns a SUMMARY only: counts + a small sample. Raw chain payload
        is intentionally NOT returned — the probe doesn't need it.
        """
        if not self._token:
            return {"ok": False, "reason": "not_authenticated"}
        with self._build_client() as c:
            r = c.get(
                f"/option-chains/{symbol.upper()}/nested",
                headers=self._auth_headers(),
            )
            self._record(r)
            if r.status_code >= 400:
                return {
                    "ok": False, "http_status": r.status_code, "symbol": symbol,
                }
            items = (((r.json().get("data") or {}).get("items")) or [])
            # `items` is one element per `root-symbol` (e.g. SPX + SPXW).
            roots: list[dict[str, Any]] = []
            today = datetime.now().strftime("%Y-%m-%d")
            has_0dte = False
            for root_entry in items:
                root_symbol = root_entry.get("root-symbol") or root_entry.get("underlying-symbol")
                expirations = root_entry.get("expirations") or []
                exp_dates = [e.get("expiration-date") for e in expirations]
                sample_strikes: list[float] = []
                for e in expirations:
                    for s in (e.get("strikes") or []):
                        sp = s.get("strike-price")
                        try:
                            sample_strikes.append(float(sp))
                        except (TypeError, ValueError):
                            continue
                if any(d == today for d in exp_dates):
                    has_0dte = True
                # Sample 5 strikes spread across the range
                sample_strikes.sort()
                step = max(1, len(sample_strikes) // 5)
                sample = sample_strikes[::step][:5] if sample_strikes else []
                roots.append({
                    "root_symbol":      root_symbol,
                    "expirations_count": len(exp_dates),
                    "expirations":      list(exp_dates),   # FULL list — needed by resolve_root_for
                    "expirations_sample": exp_dates[:5],   # back-compat (was the only field before 3.1)
                    "strike_count":      len(sample_strikes),
                    "sample_strikes":    sample,
                })
            return {
                "ok":             True,
                "http_status":    r.status_code,
                "symbol":         symbol.upper(),
                "roots":          roots,
                "supports_spxw": any((r.get("root_symbol") == "SPXW") for r in roots),
                "supports_spx":  any((r.get("root_symbol") == "SPX")  for r in roots),
                "has_0dte_today": has_0dte,
            }

    # ── Phase 3.1: root resolution ───────────────────────────────────

    def resolve_root_for(
        self,
        underlying: str,
        expiry: str,
    ) -> dict[str, Any]:
        """Pick the correct OPRA root for an `underlying` + `expiry` pair.

        Returns a sanitized dict (NEVER raises on a missing chain):
            {ok: True,  root_symbol: 'SPXW',
             source:  'auto_chain' | 'direct_match',
             available_roots: ['SPX', 'SPXW'],
             expiry: '2026-06-01'}
            {ok: False, reason: ..., requested_symbol, requested_expiry,
             available_roots, sample_expirations_by_root}

        Resolution rules:
          - If `underlying` itself appears as a root with `expiry` listed
            in its expirations → use it (source='direct_match').
          - Else walk all roots in the chain; pick the first whose
            expirations include `expiry`. SPXW is preferred over SPX
            when both list the same expiry (SPXW is the daily/PM-settled
            root the algo wants for 0DTE work).
          - Else return ok=False with diagnostics.
        """
        sym = (underlying or "").upper().strip()
        if not sym:
            return {"ok": False, "reason": "empty underlying"}
        chain = self.get_option_chain_summary(sym)
        if not chain.get("ok"):
            return {
                "ok":               False,
                "reason":           "chain_unavailable",
                "requested_symbol": sym,
                "requested_expiry": expiry,
                "http_status":      chain.get("http_status"),
            }
        roots = chain.get("roots") or []
        available = [r.get("root_symbol") for r in roots if r.get("root_symbol")]

        # Direct match: caller asked for SPXW and SPXW lists the expiry.
        for r in roots:
            if r.get("root_symbol") == sym and expiry in (r.get("expirations") or []):
                return {
                    "ok":              True,
                    "root_symbol":     sym,
                    "source":          "direct_match",
                    "available_roots": available,
                    "expiry":          expiry,
                }

        # Auto-resolve: pick the first root whose expirations include `expiry`.
        # SPXW wins over SPX when both list the same day (the daily/PM
        # settlement is what 0DTE Vertical Wingy targets).
        matches = [
            r.get("root_symbol") for r in roots
            if expiry in (r.get("expirations") or [])
        ]
        if matches:
            picked = "SPXW" if "SPXW" in matches else matches[0]
            return {
                "ok":              True,
                "root_symbol":     picked,
                "source":          "auto_chain",
                "available_roots": available,
                "expiry":          expiry,
                "matched_roots":   matches,
            }

        # No matching expiry — sanitized error with sample expirations
        # per root so the caller can see what they SHOULD have asked for.
        sample_by_root = {
            r.get("root_symbol"): (r.get("expirations") or [])[:8]
            for r in roots
        }
        return {
            "ok":               False,
            "reason":           "expiry_not_in_chain",
            "requested_symbol": sym,
            "requested_expiry": expiry,
            "available_roots":  available,
            "sample_expirations_by_root": sample_by_root,
        }

    # ── Phase 4.1: validate an explicit root_hint against the chain ──

    def validate_root_hint(
        self,
        underlying: str,
        root_hint: str,
        expiry: str,
    ) -> dict[str, Any]:
        """Confirm `root_hint` is a real root for `underlying`+`expiry`.

        READ-ONLY chain inspection. Reuses the chain summary that
        `resolve_root_for` would have fetched, so a single tick that calls
        BOTH only hits the wire once (cache is per-tick implicit — the
        caller reuses the same `TastyProbeClient` instance).

        Returns one of:
          {ok: True,  root_symbol: <hint>, validated_via: 'chain',
           available_roots: [...]}
          {ok: False, reason: 'root_not_in_chain' | 'expiry_not_in_root',
           requested_root, requested_expiry, available_roots,
           sample_expirations_by_root,
           fallback_root: <auto-resolved if any, else None>}
        """
        sym = (underlying or "").upper().strip()
        hint = (root_hint or "").upper().strip()
        if not sym or not hint:
            return {"ok": False, "reason": "empty_underlying_or_hint"}
        chain = self.get_option_chain_summary(sym)
        if not chain.get("ok"):
            return {
                "ok":               False,
                "reason":           "chain_unavailable",
                "requested_root":   hint,
                "requested_symbol": sym,
                "requested_expiry": expiry,
                "http_status":      chain.get("http_status"),
            }
        roots = chain.get("roots") or []
        available = [r.get("root_symbol") for r in roots if r.get("root_symbol")]
        sample_by_root = {
            r.get("root_symbol"): (r.get("expirations") or [])[:8]
            for r in roots
        }

        # Find the hint in the chain
        hit = next(
            (r for r in roots if (r.get("root_symbol") or "").upper() == hint),
            None,
        )
        if hit is None:
            # Hint not in chain at all — propose a fallback via auto resolve.
            auto = self.resolve_root_for(sym, expiry)
            return {
                "ok":                          False,
                "reason":                      "root_not_in_chain",
                "requested_root":              hint,
                "requested_symbol":            sym,
                "requested_expiry":            expiry,
                "available_roots":             available,
                "sample_expirations_by_root":  sample_by_root,
                "fallback_root":               (auto.get("root_symbol") if auto.get("ok") else None),
            }
        # Hint exists; does the requested expiry land in this root?
        if expiry not in (hit.get("expirations") or []):
            auto = self.resolve_root_for(sym, expiry)
            return {
                "ok":                          False,
                "reason":                      "expiry_not_in_root",
                "requested_root":              hint,
                "requested_symbol":            sym,
                "requested_expiry":            expiry,
                "available_roots":             available,
                "sample_expirations_by_root":  sample_by_root,
                "fallback_root":               (auto.get("root_symbol") if auto.get("ok") else None),
            }
        return {
            "ok":              True,
            "root_symbol":     hint,
            "validated_via":   "chain",
            "available_roots": available,
            "expiry":          expiry,
        }

    # ── Phase 3.1: quote lookup with auto root resolution ────────────

    def get_option_quotes_for_strikes(
        self,
        underlying: str,
        expiry: str,
        strikes: list[float],
        right: str,
        *,
        root_symbol: str | None = None,
    ) -> dict[str, Any]:
        """High-level wrapper that auto-resolves SPX vs SPXW.

        - If `root_symbol` is supplied → use it directly
          (`root_resolution_source = 'explicit'`).
        - Else if `underlying` already names a root the chain advertises
          for `expiry` → use it (`source = 'direct_symbol'`).
        - Else look up the chain and pick the right root, preferring SPXW
          when ambiguous (`source = 'auto_chain'`).
        - On unresolved expiry → return a sanitized error with samples
          per available root; **no traceback, no silent guess**.

        Output ALWAYS includes:
            requested_underlying_symbol, resolved_root_symbol,
            root_resolution_source, requested_symbols,
            quote_count, quotes
        """
        if not self._token:
            return {"ok": False, "reason": "not_authenticated"}

        # 1. resolve root
        if root_symbol:
            resolved_root = root_symbol.upper().strip()
            resolution_source = "explicit"
            resolution_meta: dict[str, Any] = {
                "ok": True, "root_symbol": resolved_root, "source": "explicit",
                "expiry": expiry,
            }
            # Phase 4.1 — validate explicit hints against the chain so the
            # operator can't silently OCC-build against a wrong root.
            # Under STRICT_ROOT_HINT=true, an invalid hint hard-fails;
            # otherwise we auto-fall back to the resolver's pick and stamp
            # `root_hint_mismatch=true` on the result for audit.
            # When the chain itself is UNAVAILABLE (404 / no auth / etc.),
            # we KEEP the explicit hint — that's a transient network issue,
            # not a hint vs chain mismatch. Only an actual mismatch downgrades.
            import os as _os
            strict_hint = _os.environ.get("STRICT_ROOT_HINT", "").strip().lower() in {
                "true", "1", "yes", "on",
            }
            v = self.validate_root_hint(underlying, resolved_root, expiry)
            if not v.get("ok") and v.get("reason") in (
                "root_not_in_chain", "expiry_not_in_root",
            ):
                if strict_hint:
                    return {
                        "ok":                          False,
                        "requested_underlying_symbol": (underlying or "").upper(),
                        "requested_expiry":            expiry,
                        "resolved_root_symbol":        None,
                        "root_resolution_source":      "explicit_invalid",
                        "reason":                      v.get("reason"),
                        "available_roots":             v.get("available_roots") or [],
                        "sample_expirations_by_root":  v.get("sample_expirations_by_root") or {},
                        "requested_symbols":           [],
                        "quote_count":                 0,
                        "quotes":                      [],
                        "root_hint_invalid":           True,
                    }
                # Lax mode — auto-fallback (or auto-resolve fresh)
                auto_root = v.get("fallback_root")
                if not auto_root:
                    auto = self.resolve_root_for(underlying, expiry)
                    if not auto.get("ok"):
                        return {
                            "ok":                          False,
                            "requested_underlying_symbol": (underlying or "").upper(),
                            "requested_expiry":            expiry,
                            "resolved_root_symbol":        None,
                            "root_resolution_source":      "unresolved",
                            "reason":                      auto.get("reason"),
                            "available_roots":             auto.get("available_roots") or [],
                            "sample_expirations_by_root":  auto.get(
                                "sample_expirations_by_root") or {},
                            "requested_symbols":           [],
                            "quote_count":                 0,
                            "quotes":                      [],
                            "root_hint_invalid":           True,
                        }
                    auto_root = auto.get("root_symbol")
                resolved_root = auto_root
                resolution_source = "auto_chain_after_hint_mismatch"
                resolution_meta = {
                    "ok": True, "root_symbol": resolved_root,
                    "source": resolution_source, "expiry": expiry,
                    "root_hint_mismatch": True,
                    "available_roots": v.get("available_roots") or [],
                }
        else:
            resolution_meta = self.resolve_root_for(underlying, expiry)
            if not resolution_meta.get("ok"):
                # Clean sanitized error — propagate diagnostics.
                return {
                    "ok":                          False,
                    "requested_underlying_symbol": (underlying or "").upper(),
                    "requested_expiry":            expiry,
                    "resolved_root_symbol":        None,
                    "root_resolution_source":      "unresolved",
                    "reason":                      resolution_meta.get("reason"),
                    "available_roots":             resolution_meta.get("available_roots") or [],
                    "sample_expirations_by_root":  resolution_meta.get(
                        "sample_expirations_by_root") or {},
                    "requested_symbols":           [],
                    "quote_count":                 0,
                    "quotes":                      [],
                }
            resolved_root = resolution_meta["root_symbol"]
            resolution_source = resolution_meta.get("source") or "auto_chain"

        # 2. build OCC symbols against the resolved root
        occ_symbols = [
            _build_occ_option_symbol(resolved_root, expiry, k, right)
            for k in strikes
        ]

        # 3. fetch quotes
        quote_result = self.get_option_quotes(occ_symbols)

        # 4. annotate with resolution metadata
        quote_result.update({
            "requested_underlying_symbol": (underlying or "").upper(),
            "resolved_root_symbol":        resolved_root,
            "root_resolution_source":      resolution_source,
            "available_roots":             resolution_meta.get("available_roots") or [],
            "requested_symbols":           occ_symbols,
        })
        return quote_result

    # ── quotes ───────────────────────────────────────────────────────

    def get_option_quotes(
        self,
        equity_option_symbols: list[str],
    ) -> dict[str, Any]:
        """GET /market-data/by-type?equity-option=SYM1,SYM2,...

        Returns a list of {symbol, bid, ask, mid, last, mark} dicts.
        Tastytrade caps `by-type` at 100 symbols across all instrument
        types combined; the caller passes the OCC-format symbols.
        """
        if not self._token:
            return {"ok": False, "reason": "not_authenticated"}
        if not equity_option_symbols:
            return {"ok": False, "reason": "no_symbols_requested"}
        joined = ",".join(equity_option_symbols[:100])
        with self._build_client() as c:
            r = c.get(
                "/market-data/by-type",
                params={"equity-option": joined},
                headers=self._auth_headers(),
            )
            self._record(r)
            if r.status_code >= 400:
                return {
                    "ok": False,
                    "http_status": r.status_code,
                    "requested_count": len(equity_option_symbols),
                }
            items = (((r.json().get("data") or {}).get("items")) or [])
            quotes: list[dict[str, Any]] = []
            for it in items:
                quotes.append({
                    "symbol":  it.get("symbol"),
                    "instrument_type": it.get("instrument-type"),
                    "bid":     _safe_float(it.get("bid")),
                    "ask":     _safe_float(it.get("ask")),
                    "mid":     _safe_float(it.get("mid") or it.get("mark")),
                    "last":    _safe_float(it.get("last")),
                    "mark":    _safe_float(it.get("mark")),
                    "ts":      it.get("updated-at") or it.get("ts"),
                })
            return {
                "ok":              True,
                "http_status":     r.status_code,
                "requested_count": len(equity_option_symbols),
                "quote_count":     len(quotes),
                "quotes":          quotes,
            }

    # ── DXLink token (NOT the websocket) ─────────────────────────────

    def get_dxlink_token(self) -> dict[str, Any]:
        """GET /api-quote-tokens → presence of token + dxlink-url only.

        The probe DELIBERATELY does NOT open the WebSocket. We confirm the
        token endpoint is reachable; the production QuoteProvider will
        connect later.
        """
        if not self._token:
            return {"ok": False, "reason": "not_authenticated"}
        with self._build_client() as c:
            r = c.get("/api-quote-tokens", headers=self._auth_headers())
            self._record(r)
            if r.status_code >= 400:
                return {"ok": False, "http_status": r.status_code}
            data = (r.json().get("data") or {})
            dxlink_url = data.get("dxlink-url")
            return {
                "ok":                  True,
                "http_status":         r.status_code,
                "token_present":       bool(data.get("token")),
                "dxlink_url_present":  bool(dxlink_url),
                "dxlink_url_host":     _host_of(dxlink_url),
                "level":               data.get("level"),
            }

    # ── capabilities summary ─────────────────────────────────────────

    def capabilities_summary(
        self,
        symbol: str = "SPX",
        *,
        capability_expiry: str | None = None,
        capability_strikes: list[float] | None = None,
        capability_right: str = "C",
        root_symbol: str | None = None,
    ) -> dict[str, Any]:
        """Run a sequence of read-only probes + report a capability matrix.

        Each individual sub-probe is non-fatal — if `/option-chains` fails
        it surfaces as `has_chain=False`, the probe doesn't abort.

        Optional Phase 3.1 args trigger a REAL quote probe:
          capability_expiry / capability_strikes / capability_right
        When all three are supplied, `has_quotes` becomes True/False
        (was always 'unknown_…' before) and the matrix adds:
          quote_probe_count, quote_probe_resolved_root_symbol,
          quote_probe_http_status, quote_probe_error.
        """
        caps: dict[str, Any] = {
            "has_auth":                  False,
            "has_accounts":              False,
            "has_chain":                 False,
            "has_quotes":                False,
            "has_streaming_token":       False,
            "has_paper_or_sandbox":      "unknown",
            "has_vertical_order_preview": "unknown",
        }
        auth = self.login()
        caps["has_auth"] = bool(auth.get("auth_success"))
        if not caps["has_auth"]:
            caps["env"] = self._cfg.env
            caps["reason"] = auth.get("reason") or auth.get("error_type")
            return caps

        accts = self.list_accounts()
        caps["has_accounts"] = bool(accts.get("ok"))
        caps["accounts_count"] = accts.get("accounts_count")

        chain = self.get_option_chain_summary(symbol)
        caps["has_chain"] = bool(chain.get("ok"))
        caps["chain_supports_spxw"] = chain.get("supports_spxw")
        caps["chain_supports_spx"]  = chain.get("supports_spx")
        caps["chain_has_0dte_today"] = chain.get("has_0dte_today")

        # Probe quotes against a tiny sample. Default behavior unchanged:
        # report 'unknown' and tell the user to use the explicit --quotes
        # subcommand. NEW (Phase 3.1): if the caller supplied capability
        # quote args, run a REAL quote probe with auto-resolved root.
        if caps["has_chain"]:
            if (
                capability_expiry
                and capability_strikes
                and capability_right
            ):
                qp = self.get_option_quotes_for_strikes(
                    symbol,
                    capability_expiry,
                    list(capability_strikes),
                    capability_right,
                    root_symbol=root_symbol,
                )
                caps["has_quotes"] = bool(qp.get("ok") and qp.get("quote_count", 0) > 0)
                caps["quote_probe_count"]                  = qp.get("quote_count", 0)
                caps["quote_probe_resolved_root_symbol"]   = qp.get("resolved_root_symbol")
                caps["quote_probe_root_resolution_source"] = qp.get("root_resolution_source")
                caps["quote_probe_http_status"]            = qp.get("http_status")
                if not qp.get("ok"):
                    caps["quote_probe_error"] = qp.get("reason") or "unknown"
            else:
                caps["has_quotes"] = "unknown_via_capabilities_use_quotes_subcmd"

        if self._cfg.use_dxlink:
            tok = self.get_dxlink_token()
            caps["has_streaming_token"] = bool(tok.get("ok") and tok.get("token_present"))

        # Sandbox / dry-run signal: hostname tells us the env, dry-run is
        # documented as exposed in cert (see docs/reference_notes.md §8b).
        caps["has_paper_or_sandbox"] = (
            "yes_certification" if self._cfg.env == "certification" else "production"
        )
        caps["has_certification_or_sandbox"] = self._cfg.env == "certification"
        caps["has_vertical_order_preview"] = "exposed_per_docs_not_probed"
        caps["has_paper_or_sandbox_order_support"] = (
            "yes_per_docs" if self._cfg.env == "certification" else "unknown_in_production"
        )

        # ── Phase 3 extension: trade scope + safety gate (NEVER omitted) ──
        caps["has_dxlink"] = caps.get("has_streaming_token", False)
        caps["trade_scope_present"]              = self._cfg.trade_scope_present()
        caps["trade_scope_allowed"]              = self._cfg.allow_trade_scope
        caps["order_submission_enabled"]         = self._cfg.enable_order_submission
        caps["execution_blocked_by_safety_gate"] = not self._cfg.enable_order_submission
        # Defense in depth: even if someone flips the gate later, the probe
        # itself doesn't expose a submit path. Surface that fact explicitly.
        caps["probe_exposes_submit_path"] = False
        return caps

    # ── intentionally NOT implemented ────────────────────────────────

    # Safety-gate stubs. These never call any HTTP path and always
    # short-circuit before reaching network code. Even if a future change
    # flipped `enable_order_submission=True`, the probe MODULE doesn't
    # expose a real submit implementation — the gate is belt + suspenders.

    def submit_order(self, *args: Any, **kwargs: Any) -> None:
        raise SafetyGateError(
            "Tasty probe never submits orders. Phase 3 is read-only — "
            "trade scope and TASTY_ENABLE_ORDER_SUBMISSION are tracked "
            "for future capability ONLY. Use the production "
            "ExecutionProvider (does not yet exist) with a deliberate "
            "code change after probe review."
        )

    def submit_complex_order(self, *args: Any, **kwargs: Any) -> None:
        raise SafetyGateError(
            "Tasty probe never submits complex orders. Phase 3 is "
            "read-only — the multi-leg /complex-orders path is documented "
            "but NOT implemented here."
        )

    def open_streaming(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(
            "Tasty probe only fetches the DXLink token. The WebSocket "
            "connection is implemented in the production QuoteProvider."
        )


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    # Strip scheme + path manually to avoid an extra dependency.
    s = str(url)
    if "://" in s:
        s = s.split("://", 1)[1]
    return s.split("/", 1)[0] or None


# ──────────────────────────────────────────────────────────────────────
# Convenience: build TastyProbeConfig from environment
# ──────────────────────────────────────────────────────────────────────

def config_from_env() -> TastyProbeConfig:
    """Build a `TastyProbeConfig` from the TASTY_* env variables. Misses
    are left as None — `is_configured()` decides whether the probe can run."""
    import os

    def _bool(name: str, default: bool) -> bool:
        v = os.environ.get(name)
        if v is None or v == "":
            return default
        return v.strip().lower() in {"true", "1", "yes", "on"}

    def _int(name: str, default: int) -> int:
        v = os.environ.get(name)
        try:
            return int(v) if v not in (None, "") else default
        except ValueError:
            return default

    return TastyProbeConfig(
        env=os.environ.get("TASTY_ENV", "certification") or "certification",
        base_url=os.environ.get("TASTY_BASE_URL") or None,
        username=os.environ.get("TASTY_USERNAME") or None,
        password=os.environ.get("TASTY_PASSWORD") or None,
        account_number=os.environ.get("TASTY_ACCOUNT_NUMBER") or None,
        # OAuth fields
        client_id=os.environ.get("TASTY_CLIENT_ID") or None,
        client_secret=os.environ.get("TASTY_CLIENT_SECRET") or None,
        redirect_uri=os.environ.get("TASTY_REDIRECT_URI") or None,
        refresh_token=os.environ.get("TASTY_REFRESH_TOKEN") or None,
        scopes=_parse_scopes(os.environ.get("TASTY_SCOPES")),
        # Safety gates — default closed
        allow_trade_scope=_bool("TASTY_ALLOW_TRADE_SCOPE", True),
        enable_order_submission=_bool("TASTY_ENABLE_ORDER_SUBMISSION", False),
        # Transport
        use_dxlink=_bool("TASTY_USE_DXLINK", False),
        timeout_seconds=_int("TASTY_TIMEOUT_SECONDS", 10),
        verify_ssl=_bool("TASTY_VERIFY_SSL", True),
    )
