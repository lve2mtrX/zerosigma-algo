"""ZeroSigmaApiStructureProvider — read-only ZS API client.

Consumes the public ZerσSigma API at `${ZS_API_BASE_URL}/api/v1/*` and
maps responses into `StructureSnapshot` / `ExposureContext`. No quote
data is fetched here (that's the QuoteProvider's job — see Phase 1.5).

What it calls:
  GET /api/v1/market/snapshot?symbol=...           (public, 1s cache)
  GET /api/v1/exposure/series?symbol=...&metric=volume&mode=split
                                                   (subscription-gated)
  Optionally (auth):
    POST /api/v1/auth/login           when ZS_API_AUTH_MODE=login
    POST /api/v1/auth/service-token   when ZS_API_AUTH_MODE=service_token

What it never does:
  - writes to the ZS API
  - touches Redis or DigitalOcean Spaces
  - logs tokens, passwords, or service keys
  - re-implements Greeks

Auth modes (env: `ZS_API_AUTH_MODE`):
  none           → reject `get_snapshot`; `status()` reports unconfigured.
  public_only    → call `/market/snapshot` and any other public endpoint
                   WITHOUT an Authorization header. Subscription-gated
                   endpoints (`/exposure/series`, `/exposure/ddoi`) are
                   skipped — even if `enable_exposure_series=True` — so
                   volume-derived fields (PUT_CEILING / CALL_FLOOR / MaxVol)
                   land as None and are tracked in `missing_fields`. No
                   secrets required; nothing is ever sent.
  bearer         → use env `ZS_API_TOKEN` verbatim as the bearer.
  login          → POST /auth/login with ZS_API_USERNAME + ZS_API_PASSWORD.
  service_token  → POST /auth/service-token with ZS_API_USERNAME + ZS_API_SERVICE_KEY.

The HTTP client is injectable via `client_factory=lambda: httpx.Client(...)`
so tests can substitute `httpx.MockTransport` without monkeypatching modules.

See docs/reference_notes.md §8a for the contract details.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from src.providers.structure.types import ExposureContext, StructureSnapshot
from src.utils.logging import get_logger
from src.utils.time import now_et

log = get_logger("provider.zerosigma_api")


_VALID_AUTH_MODES = {"none", "public_only", "bearer", "login", "service_token"}

# Modes that may attach an Authorization header. `public_only` deliberately
# is NOT in this set — even if a token happened to be present, the provider
# must not send it when the user has opted into public-only reads.
_AUTHED_MODES = {"bearer", "login", "service_token"}


def _coerce_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"true", "1", "yes", "on"}:
        return True
    if s in {"false", "0", "no", "off", ""}:
        return False
    return default


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _highest_strike_where(strikes: list[float], series: list[float], threshold: float) -> float | None:
    out = [s for s, v in zip(strikes, series, strict=False) if v is not None and v >= threshold]
    return max(out) if out else None


def _lowest_strike_where(strikes: list[float], series: list[float], threshold: float) -> float | None:
    out = [s for s, v in zip(strikes, series, strict=False) if v is not None and v >= threshold]
    return min(out) if out else None


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO timestamp; fall back to current ET time if unparseable."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return now_et()


@dataclass
class _ProviderState:
    last_refresh_ts: dict[str, float]
    last_error: str | None
    last_status_code: int | None
    last_missing_fields: list[str]
    has_subscription: bool | None   # None = unknown; True after 200; False after 403


class ZeroSigmaApiStructureProvider:
    """Read-only client against the ZerσSigma public API."""

    name = "zerosigma_api"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        auth_mode: str | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        service_key: str | None = None,
        symbol: str = "SPX",
        refresh_seconds: Any = 60,
        timeout_seconds: Any = 10,
        verify_ssl: Any = True,
        max_retries: Any = 3,
        enable_exposure_series: Any = True,
        enable_ddoi: Any = False,
        client_factory: Callable[[], httpx.Client] | None = None,
        **_: object,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.auth_mode = (auth_mode or "none").strip().lower()
        if self.auth_mode not in _VALID_AUTH_MODES:
            self.auth_mode = "none"
        self._token = token or None
        self._username = username or None
        self._password = password or None
        self._service_key = service_key or None
        self.symbol = symbol or "SPX"
        self.refresh_seconds = _coerce_int(refresh_seconds, 60)
        self.timeout_seconds = _coerce_int(timeout_seconds, 10)
        self.verify_ssl = _coerce_bool(verify_ssl, default=True)
        self.max_retries = _coerce_int(max_retries, 3)
        self.enable_exposure_series = _coerce_bool(enable_exposure_series, default=True)
        self.enable_ddoi = _coerce_bool(enable_ddoi, default=False)
        self._client_factory = client_factory
        self._state = _ProviderState(
            last_refresh_ts={}, last_error=None, last_status_code=None,
            last_missing_fields=[], has_subscription=None,
        )

    # ── auth ──────────────────────────────────────────────────────────

    def _is_configured(self) -> bool:
        if not self.base_url:
            return False
        if self.auth_mode == "none":
            return False
        if self.auth_mode == "public_only":
            # No creds required — just a base_url. Subscription-gated
            # endpoints are skipped by `_use_authed_endpoints`.
            return True
        if self.auth_mode == "bearer":
            return bool(self._token)
        if self.auth_mode == "login":
            return bool(self._username and self._password)
        if self.auth_mode == "service_token":
            return bool(self._username and self._service_key)
        return False

    def _use_authed_endpoints(self) -> bool:
        """True when the provider may call subscription-gated endpoints."""
        return self.auth_mode in _AUTHED_MODES and self._is_configured()

    def _build_client(self) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            verify=self.verify_ssl,
        )

    def _ensure_token(self, client: httpx.Client) -> str:
        if self.auth_mode in {"none", "public_only"}:
            # Caller should never reach here in these modes; guard defensively.
            raise RuntimeError(
                f"ZS API auth_mode={self.auth_mode!r} does not use tokens"
            )
        if self._token:
            return self._token
        if self.auth_mode == "bearer":
            raise RuntimeError("ZS API auth_mode=bearer but ZS_API_TOKEN is empty")

        if self.auth_mode == "login":
            r = client.post("/api/v1/auth/login",
                            json={"email": self._username, "password": self._password})
            r.raise_for_status()
            tok = r.json().get("access_token")
            if not tok:
                raise RuntimeError("ZS API /auth/login returned no access_token")
            self._token = tok
            return tok

        if self.auth_mode == "service_token":
            r = client.post("/api/v1/auth/service-token",
                            json={"email": self._username, "service_key": self._service_key})
            r.raise_for_status()
            tok = r.json().get("access_token")
            if not tok:
                raise RuntimeError("ZS API /auth/service-token returned no access_token")
            self._token = tok
            return tok

        raise RuntimeError(f"ZS API auth_mode {self.auth_mode!r} not configured")

    def _auth_headers(self, client: httpx.Client) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._ensure_token(client)}"}

    # ── HTTP ──────────────────────────────────────────────────────────

    def _get_json(
        self,
        client: httpx.Client,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        authed: bool = False,
    ) -> Any:
        """GET path → JSON. Returns None for 404/403 (degrade gracefully)."""
        headers = self._auth_headers(client) if authed else {}
        r = client.get(path, params=params, headers=headers)
        self._state.last_status_code = r.status_code
        if r.status_code in (401, 403, 404, 501, 503):
            log.info("ZS API %s → %s (treated as no data)", path, r.status_code)
            return None
        r.raise_for_status()
        return r.json()

    # ── public API ────────────────────────────────────────────────────

    def get_snapshot(self, symbol: str | None = None) -> StructureSnapshot:
        sym = (symbol or self.symbol or "SPX").upper()
        if not self._is_configured():
            raise RuntimeError(
                "ZeroSigmaApiStructureProvider is not configured. "
                "Set ZS_API_BASE_URL + ZS_API_AUTH_MODE (and matching creds) in .env, "
                "or fall back to the stub provider."
            )

        missing: list[str] = []
        with self._build_client() as client:
            # 1) consolidated market snapshot (public — no auth required)
            snap_payload = self._get_json(client, "/api/v1/market/snapshot", params={"symbol": sym})
            if not snap_payload:
                raise RuntimeError(f"ZS API /market/snapshot returned no data for {sym}")

            # 2) per-strike volume series for VW levels (subscription-gated)
            #    Only attempted when caller has creds AND has opted in.
            #    public_only / none → skipped silently; fields land as None.
            vol_series = None
            if self.enable_exposure_series and self._use_authed_endpoints():
                vol_series = self._get_json(
                    client, "/api/v1/exposure/series",
                    params={"symbol": sym, "metric": "volume", "mode": "split"},
                    authed=True,
                )
                self._state.has_subscription = vol_series is not None

        exposures = self._build_exposures(snap_payload, vol_series, missing)
        spot_payload = snap_payload.get("spot") or {}
        spot = _safe_float(spot_payload.get("price"))
        if spot is None:
            spot = _safe_float(snap_payload.get("spot_price"))
            if spot is None:
                missing.append("spot.price")
                spot = 0.0

        quote_ts = _parse_ts(snap_payload.get("timestamp") or spot_payload.get("timestamp"))
        chain_meta = snap_payload.get("chain") or {}
        expiry = chain_meta.get("expiry") or snap_payload.get("expiry")
        # `or` short-circuits on 0 (a valid DTE), so check None explicitly.
        dte_raw = chain_meta.get("dte")
        if dte_raw is None:
            dte_raw = snap_payload.get("dte")
        dte = _safe_int(dte_raw)

        self._state.last_refresh_ts[sym] = datetime.now().timestamp()
        self._state.last_missing_fields = missing
        self._state.last_error = None

        return StructureSnapshot(
            symbol=sym,
            spot=spot,
            quote_ts=quote_ts,
            exposures=exposures,
            expiry=expiry,
            dte=dte,
            source=self.name,
            raw={
                "missing_fields": list(missing),
                "subscription_active": self._state.has_subscription,
            },
        )

    def is_fresh(self, symbol: str, max_age_seconds: int) -> bool:
        last = self._state.last_refresh_ts.get(symbol.upper())
        if last is None:
            return False
        return (datetime.now().timestamp() - last) <= max_age_seconds

    def last_refresh_ts(self, symbol: str) -> float | None:
        return self._state.last_refresh_ts.get(symbol.upper())

    def status(self) -> dict[str, Any]:
        """Light status snapshot — never contains secrets."""
        # `effective_exposure_series` reflects what the provider WILL actually
        # try, after the public_only / no-auth guard. Useful for the cockpit
        # to show "exposure series disabled in public_only mode" warnings.
        effective_exposure_series = (
            self.enable_exposure_series and self._use_authed_endpoints()
        )
        return {
            "provider": self.name,
            "base_url": self.base_url or None,
            "auth_mode": self.auth_mode,
            "configured": self._is_configured(),
            "public_only": self.auth_mode == "public_only",
            "last_status_code": self._state.last_status_code,
            "last_error": self._state.last_error,
            "last_missing_fields": list(self._state.last_missing_fields),
            "subscription_active": self._state.has_subscription,
            "exposure_series_enabled": self.enable_exposure_series,
            "exposure_series_effective": effective_exposure_series,
            "ddoi_enabled": self.enable_ddoi,
        }

    # ── mapping ───────────────────────────────────────────────────────

    def _build_exposures(
        self,
        snap_payload: dict[str, Any],
        vol_series: dict[str, Any] | None,
        missing: list[str],
    ) -> ExposureContext:
        exp = snap_payload.get("exposures") or {}

        total_gex_bn  = _safe_float(exp.get("total_gex_bn"))
        # ZS uses unsuffixed names (vex/dex/cex) — algo uses *_bn for clarity.
        total_vex_bn  = _safe_float(exp.get("vex") or exp.get("total_vex_bn"))
        total_cex_bn  = _safe_float(exp.get("cex") or exp.get("total_cex_bn"))  # noqa: F841 — reserved for future ExposureContext field
        da_gex_signed = _safe_float(exp.get("da_gex_bn") or exp.get("da_gex_signed"))

        # Gamma regime: derive from sign of DA-GEX if not directly provided.
        gamma_regime: str | None = exp.get("gamma_regime")
        if gamma_regime is None and da_gex_signed is not None:
            gamma_regime = "positive" if da_gex_signed > 0 else "negative" if da_gex_signed < 0 else None

        # Vertical-Wing levels — require /exposure/series volume payload.
        put_ceiling_2k = put_ceiling_5k = call_floor_2k = call_floor_5k = None
        maxvol = None
        if vol_series:
            strikes = vol_series.get("strikes") or []
            calls = vol_series.get("calls") or []
            puts = vol_series.get("puts") or []
            if strikes and len(strikes) == len(calls) == len(puts):
                put_ceiling_2k = _highest_strike_where(strikes, puts, 2000.0)
                put_ceiling_5k = _highest_strike_where(strikes, puts, 5000.0)
                call_floor_2k  = _lowest_strike_where(strikes, calls, 2000.0)
                call_floor_5k  = _lowest_strike_where(strikes, calls, 5000.0)
                combined = [(c or 0) + (p or 0) for c, p in zip(calls, puts, strict=False)]
                if combined:
                    maxvol = strikes[combined.index(max(combined))]
        else:
            missing.extend([
                "put_ceiling_2k", "put_ceiling_5k",
                "call_floor_2k", "call_floor_5k", "maxvol",
            ])

        # Fields the ZS API does not currently expose to the cockpit:
        gamma_flip = None
        call_wall = None
        put_wall = None
        ddoi_pin = None
        missing.extend(["gamma_flip", "call_wall", "put_wall", "ddoi_pin"])

        return ExposureContext(
            total_gex_bn=total_gex_bn,
            total_vex_bn=total_vex_bn,
            gamma_flip=gamma_flip,
            call_wall=call_wall,
            put_wall=put_wall,
            maxvol=maxvol,
            gamma_regime=gamma_regime,
            da_gex_signed=da_gex_signed,
            put_ceiling_2k=put_ceiling_2k,
            put_ceiling_5k=put_ceiling_5k,
            call_floor_2k=call_floor_2k,
            call_floor_5k=call_floor_5k,
            ddoi_pin=ddoi_pin,
        )


# ──────────────────────────────────────────────────────────────────────
# tiny utils
# ──────────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    with contextlib.suppress(TypeError, ValueError):
        return float(v)
    return None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    with contextlib.suppress(TypeError, ValueError):
        return int(v)
    return None
