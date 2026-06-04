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


def _volume_at(strikes: list[float], series: list[float], strike: float | None) -> float | None:
    """Return the value in `series` at the position where `strikes` equals
    `strike` (None if not found). Used to lift the actual put/call volume
    that defined a VW anchor."""
    if strike is None:
        return None
    for s, v in zip(strikes, series, strict=False):
        if s == strike:
            return v if isinstance(v, (int, float)) else None
    return None


def _adjacent_strike(strikes: list[float], w1: float | None, *, direction: str) -> float | None:
    """Phase 9J — the next AVAILABLE strike below ('lower') or above ('higher')
    `w1` in the chain (NOT assuming a fixed 5/10-pt increment — uses the actual
    neighbouring strike present in the series). None when `w1` is missing or
    there is no neighbour in that direction (→ true WDS unavailable)."""
    if w1 is None:
        return None
    nums = [s for s in strikes if isinstance(s, (int, float))]
    if direction == "lower":
        below = [s for s in nums if s < w1]
        return max(below) if below else None
    above = [s for s in nums if s > w1]
    return min(above) if above else None


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

        self._state.last_refresh_ts[sym] = datetime.now().timestamp()
        self._state.last_error = None
        return self.build_snapshot_from_payload(
            snap_payload, vol_series, missing=missing, symbol=sym,
        )

    def build_snapshot_from_payload(
        self,
        snap_payload: dict[str, Any],
        vol_series: dict[str, Any] | None = None,
        *,
        missing: list[str] | None = None,
        symbol: str | None = None,
        source: str | None = None,
    ) -> StructureSnapshot:
        """Pure mapping: ZS payload (+ optional volume series) → StructureSnapshot.

        Extracted (Phase 9H) so the SAME mapping serves both the live fetch and
        the Phase 10 replay/backtest loader — there is no second mapper to drift.
        No network here. `source` overrides the snapshot's provider tag (e.g.
        "replay") for replayed snapshots."""
        missing = missing if missing is not None else []
        sym = (symbol or self.symbol or "SPX").upper()

        exposures = self._build_exposures(snap_payload, vol_series, missing)
        spot_payload = snap_payload.get("spot") or {}
        # ZS worker_watchlist writes spot_json as a FLAT dict whose price
        # field is named "spot" (the scalar). Walk a sensible alias chain
        # so alternate payload shapes still resolve.
        spot = (
            _safe_float(spot_payload.get("spot"))      # ZS canonical
            or _safe_float(spot_payload.get("price"))  # older example shape
            or _safe_float(spot_payload.get("last"))
            or _safe_float(spot_payload.get("close"))
            or _safe_float(snap_payload.get("spot_price"))
            or _safe_float((snap_payload.get("exposures") or {}).get("spot"))
        )
        if spot is None:
            missing.append("spot.spot")
            spot = 0.0

        quote_ts = _parse_ts(snap_payload.get("timestamp") or spot_payload.get("timestamp"))
        # `chain` and `exposures` (metrics_json) both carry expiry/dte —
        # check both. `or` short-circuits on 0 (a valid DTE), so check
        # None explicitly.
        chain_meta = snap_payload.get("chain") or {}
        exp_meta   = snap_payload.get("exposures") or {}
        expiry = (
            chain_meta.get("expiry")
            or exp_meta.get("expiry")
            or snap_payload.get("expiry")
        )
        dte_raw = chain_meta.get("dte")
        if dte_raw is None:
            dte_raw = exp_meta.get("dte")
        if dte_raw is None:
            dte_raw = snap_payload.get("dte")
        dte = _safe_int(dte_raw)

        self._state.last_missing_fields = missing

        return StructureSnapshot(
            symbol=sym,
            spot=spot,
            quote_ts=quote_ts,
            exposures=exposures,
            expiry=expiry,
            dte=dte,
            source=source or self.name,
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
        """Map the ZS metrics_json payload into our ExposureContext.

        ZS field names confirmed against `Dashboard/app/ingest/worker_watchlist.py`
        (the writer of metrics_json) and `zerosigma-api/app/api/v1/market.py`
        (which passes the Redis blob through unchanged):

            total_gex_1pct, total_raw_gex_1pct, total_da_gex_1pct,
            total_dex_1pct, total_vex_1vol, total_cex,
            max_call_oi_strike, max_put_oi_strike,
            max_call_vol_strike, max_put_vol_strike,
            atm_strike, spot,
            wings: { call_floor, put_ceiling, midline, spot_vs_wings },
            gamma: { regime, flip, cluster_primary, ... },
            flow:  { call_pct, put_pct, dominance, strength },
            straddle_iv_meta: { atm_strike, implied_move, ... }

        Older / fallback names (kept for tests + future contract drift):
            total_gex_bn, da_gex_bn, vex, dex, cex, gamma_regime.
        """
        exp    = snap_payload.get("exposures") or {}
        wings  = exp.get("wings")  or {}
        gamma  = exp.get("gamma")  or {}

        # ── aggregate exposures ──
        total_gex_bn  = _safe_float(exp.get("total_gex_1pct") or exp.get("total_gex_bn"))
        total_vex_bn  = _safe_float(exp.get("total_vex_1vol") or exp.get("vex")
                                    or exp.get("total_vex_bn"))
        da_gex_signed = _safe_float(exp.get("total_da_gex_1pct") or exp.get("da_gex_bn")
                                    or exp.get("da_gex_signed"))
        # (total_dex_1pct / total_cex are read in case a future field lands;
        #  ExposureContext doesn't expose them today — kept as locals.)
        _ = _safe_float(exp.get("total_dex_1pct") or exp.get("dex"))
        _ = _safe_float(exp.get("total_cex")      or exp.get("cex"))

        # ── gamma regime / flip ──
        regime_raw: str | None = gamma.get("regime") or exp.get("gamma_regime")
        gamma_regime: str | None = regime_raw.lower() if isinstance(regime_raw, str) else None
        if gamma_regime is None and da_gex_signed is not None:
            gamma_regime = (
                "positive" if da_gex_signed > 0
                else "negative" if da_gex_signed < 0
                else None
            )
        gamma_flip = _safe_float(gamma.get("flip") or exp.get("gamma_flip"))

        # ── gamma clusters (Phase 9H): primary / secondary gamma levels ──
        # ZS `gamma` block is documented to carry `cluster_primary` (and a
        # `cluster_secondary` companion). Map both with a small alias chain;
        # absent → None (the UI derives a display fallback from walls/flip).
        gamma_primary = _safe_float(
            gamma.get("cluster_primary") or gamma.get("primary")
            or gamma.get("primary_strike")
        )
        gamma_secondary = _safe_float(
            gamma.get("cluster_secondary") or gamma.get("secondary")
            or gamma.get("secondary_strike")
        )

        # ── walls (public metrics_json carries these as max OI strikes) ──
        call_wall = _safe_float(exp.get("max_call_oi_strike") or exp.get("call_wall"))
        put_wall  = _safe_float(exp.get("max_put_oi_strike")  or exp.get("put_wall"))

        # ── single-level ceiling/floor from public payload ──
        # ZS exposes a single `wings.call_floor` / `wings.put_ceiling`. Use
        # these as the 2K-tier values whenever we don't have a per-strike
        # volume series. The 5K-tier values stay None unless the
        # subscription-gated volume series is available below.
        wings_put_ceiling  = _safe_float(wings.get("put_ceiling"))
        wings_call_floor   = _safe_float(wings.get("call_floor"))

        # ── per-strike volume series (subscription-gated) ──
        put_ceiling_2k = put_ceiling_5k = put_ceiling_10k = None
        call_floor_2k = call_floor_5k = call_floor_10k = None
        put_ceiling_2k_volume = put_ceiling_5k_volume = put_ceiling_10k_volume = None
        call_floor_2k_volume  = call_floor_5k_volume  = call_floor_10k_volume = None
        # Phase 9J — adjacent (W2) strike + side-specific volume for the 10K wing.
        call_floor_10k_w2_strike = call_floor_10k_w2_volume = None
        put_ceiling_10k_w2_strike = put_ceiling_10k_w2_volume = None
        maxvol = None
        maxvol_volume: float | None = None
        if vol_series:
            strikes = vol_series.get("strikes") or []
            calls = vol_series.get("calls") or []
            puts = vol_series.get("puts") or []
            if strikes and len(strikes) == len(calls) == len(puts):
                put_ceiling_2k = _highest_strike_where(strikes, puts, 2000.0)
                put_ceiling_5k = _highest_strike_where(strikes, puts, 5000.0)
                put_ceiling_10k = _highest_strike_where(strikes, puts, 10000.0)  # Phase 9H
                call_floor_2k  = _lowest_strike_where(strikes, calls, 2000.0)
                call_floor_5k  = _lowest_strike_where(strikes, calls, 5000.0)
                call_floor_10k = _lowest_strike_where(strikes, calls, 10000.0)   # Phase 9H
                # Phase 2.8/9H: also capture the ACTUAL volume at each anchor.
                put_ceiling_2k_volume = _volume_at(strikes, puts,  put_ceiling_2k)
                put_ceiling_5k_volume = _volume_at(strikes, puts,  put_ceiling_5k)
                put_ceiling_10k_volume = _volume_at(strikes, puts, put_ceiling_10k)
                call_floor_2k_volume  = _volume_at(strikes, calls, call_floor_2k)
                call_floor_5k_volume  = _volume_at(strikes, calls, call_floor_5k)
                call_floor_10k_volume = _volume_at(strikes, calls, call_floor_10k)
                # Phase 9J — W2 = adjacent strike (call floor: one LOWER; put
                # ceiling: one HIGHER), with side-specific volume → WDS inputs.
                call_floor_10k_w2_strike = _adjacent_strike(strikes, call_floor_10k, direction="lower")
                call_floor_10k_w2_volume = _volume_at(strikes, calls, call_floor_10k_w2_strike)
                put_ceiling_10k_w2_strike = _adjacent_strike(strikes, put_ceiling_10k, direction="higher")
                put_ceiling_10k_w2_volume = _volume_at(strikes, puts, put_ceiling_10k_w2_strike)
                combined = [(c or 0) + (p or 0) for c, p in zip(calls, puts, strict=False)]
                if combined:
                    maxvol_idx = combined.index(max(combined))
                    maxvol = strikes[maxvol_idx]
                    maxvol_volume = float(combined[maxvol_idx])

        # Fall back to the public single-level wings when the subscription
        # series isn't available. 5K-tier strikes remain None (we can't
        # synthesize a stricter threshold from a single value).
        if put_ceiling_2k is None and wings_put_ceiling is not None:
            put_ceiling_2k = wings_put_ceiling
            # No volume known under wings-only fallback — leave 2k_volume None.
        if call_floor_2k is None and wings_call_floor is not None:
            call_floor_2k = wings_call_floor

        # MaxVol fallback: the public payload exposes max_*_vol_strike —
        # use the one with the larger reported volume by preferring the
        # call-side strike when both are present (call walls usually have
        # heavier flow at SPX). Either way: better than None.
        if maxvol is None:
            maxvol = (
                _safe_float(exp.get("max_call_vol_strike"))
                or _safe_float(exp.get("max_put_vol_strike"))
                or _safe_float(exp.get("atm_strike"))
            )

        # ── DDOI pin: still not in the public payload ──
        ddoi_pin = None

        # Track which fields stayed None so the cockpit can show diagnostics.
        # 10K + gamma clusters are tracked too (they're often None unless the
        # subscription volume series / a gamma-cluster payload is present).
        for name, value in (
            ("put_ceiling_2k", put_ceiling_2k),
            ("put_ceiling_5k", put_ceiling_5k),
            ("put_ceiling_10k", put_ceiling_10k),
            ("call_floor_2k",  call_floor_2k),
            ("call_floor_5k",  call_floor_5k),
            ("call_floor_10k", call_floor_10k),
            ("maxvol",         maxvol),
            ("gamma_flip",     gamma_flip),
            ("gamma_primary",  gamma_primary),
            ("gamma_secondary", gamma_secondary),
            ("call_wall",      call_wall),
            ("put_wall",       put_wall),
            ("ddoi_pin",       ddoi_pin),
        ):
            if value is None:
                missing.append(name)

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
            put_ceiling_10k=put_ceiling_10k,
            call_floor_2k=call_floor_2k,
            call_floor_5k=call_floor_5k,
            call_floor_10k=call_floor_10k,
            put_ceiling_2k_volume=put_ceiling_2k_volume,
            put_ceiling_5k_volume=put_ceiling_5k_volume,
            put_ceiling_10k_volume=put_ceiling_10k_volume,
            call_floor_2k_volume=call_floor_2k_volume,
            call_floor_5k_volume=call_floor_5k_volume,
            call_floor_10k_volume=call_floor_10k_volume,
            maxvol_volume=maxvol_volume,
            call_floor_10k_w2_strike=call_floor_10k_w2_strike,
            call_floor_10k_w2_volume=call_floor_10k_w2_volume,
            put_ceiling_10k_w2_strike=put_ceiling_10k_w2_strike,
            put_ceiling_10k_w2_volume=put_ceiling_10k_w2_volume,
            gamma_primary=gamma_primary,
            gamma_secondary=gamma_secondary,
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
