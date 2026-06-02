"""Production REST QuoteProvider for Tastytrade — Phase 4.

Implements the `QuoteProvider` protocol (see `src/providers/quotes/base.py`).
COMPOSES a `TastyProbeClient` internally for:
  - OAuth refresh / legacy session auth
  - SPX → SPXW root auto-resolution (Phase 3.1)
  - REST quote fetch via /market-data/by-type
  - OCC option-symbol construction
  - sanitized status reporting

Hard guarantees inherited from the probe:
  - NO order submission (probe stubs raise SafetyGateError; the production
    provider never even imports those names).
  - NO order preview / dry-run.
  - NO DXLink WebSocket — REST polling only in Phase 4. DXLink is a
    later phase if needed (current `has_dxlink=false` in Dan's account).
  - NO snapshot worker. The provider only fetches what the scanner asks for.
  - NEVER prints / logs tokens, passwords, client_secret, refresh_token,
    Authorization headers, or full account numbers.

What the production provider adds on top of the probe:
  - Implements the full `QuoteProvider` protocol (get_spot, get_option_quote,
    get_option_chain, quote_timestamp, status).
  - Applies broker-side `QuoteValidation` per quote (stale / wide-spread /
    zero-bid / crossed-market).
  - Returns a `OptionChainSnapshot` shaped exactly like the mock provider
    so the scanner can swap them without strategy changes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from src.providers.quotes.base import Right
from src.providers.quotes.tasty_probe import (
    TastyProbeClient,
    TastyProbeConfig,
    _build_occ_option_symbol,
    _safe_float,
)
from src.providers.quotes.tasty_probe import (
    config_from_env as _probe_config_from_env,
)
from src.providers.quotes.types import (
    OptionChainSnapshot,
    OptionQuote,
    OptionType,
    QuoteProviderStatus,
    QuoteRequest,
    QuoteValidation,
    SpotQuote,
)
from src.utils.logging import get_logger
from src.utils.time import now_et

log = get_logger("provider.tastytrade_quote")


class TastytradeConfigurationError(RuntimeError):
    """Raised at provider construction when Tasty config is unusable.

    Distinguishes "user picked tastytrade but didn't configure it" from a
    runtime auth/transport error. Caller (the factory or scanner) should
    surface this as a clean message and exit non-zero — NOT fall back to
    mock silently unless the operator has explicitly requested that.
    """


# ──────────────────────────────────────────────────────────────────────
# config helpers (env-driven)
# ──────────────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"true", "1", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def validation_from_env() -> QuoteValidation:
    """Build a `QuoteValidation` from TASTY_QUOTE_* env vars."""
    return QuoteValidation(
        max_age_seconds=       _env_float("TASTY_QUOTE_MAX_AGE_SECONDS", 10.0),
        max_spread_pct=        _env_float("TASTY_QUOTE_MAX_SPREAD_PCT",  0.50),
        max_spread_abs=        _env_float("TASTY_QUOTE_MAX_SPREAD_ABS",  5.00),
        reject_zero_bid=       _env_bool("TASTY_REJECT_ZERO_BID",        True),
        reject_crossed_market= _env_bool("TASTY_REJECT_CROSSED_MARKET",  True),
    )


# ──────────────────────────────────────────────────────────────────────
# Provider
# ──────────────────────────────────────────────────────────────────────

@dataclass
class _ProviderState:
    last_spot_ts:  datetime | None = None
    last_chain_ts: datetime | None = None
    last_error:    str | None = None
    chain_pulls:   int = 0
    quote_pulls:   int = 0


class TastytradeQuoteProvider:
    """Production REST quote provider for Tastytrade.

    Implements the `QuoteProvider` protocol. Construct via
    `from_env()` for the normal path; pass `tasty_config` + `validation`
    explicitly for tests."""

    name = "tastytrade"

    def __init__(
        self,
        *,
        tasty_config: TastyProbeConfig,
        validation: QuoteValidation | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
        strict: bool = True,
    ) -> None:
        """Initialize.

        Args:
            tasty_config:   reuses `TastyProbeConfig` shape (env, OAuth/legacy creds, ...)
            validation:     per-quote validation thresholds; defaults to env-driven
            client_factory: optional httpx.Client factory — TESTS inject MockTransport here
            strict:         if True (default), raise `TastytradeConfigurationError`
                            immediately when Tasty config is incomplete. If False,
                            defer the failure to the first network call.
        """
        if strict and not tasty_config.is_configured():
            mf = tasty_config.missing_fields()
            raise TastytradeConfigurationError(
                "TastytradeQuoteProvider: Tasty config is incomplete. "
                f"Usable auth modes: {mf['usable_auth_modes']!r}. "
                f"OAuth missing: {mf['oauth_missing_fields']!r}. "
                f"Legacy missing: {mf['legacy_missing_fields']!r}. "
                "Add the relevant TASTY_* vars to .env, OR set "
                "QUOTE_PROVIDER=mock to use the synthesized chain instead."
            )
        self._cfg = tasty_config
        self._validation = validation or QuoteValidation()
        # Composition: the production provider HAS-A probe client. The
        # probe handles auth + REST quote fetch + root resolution.
        self._probe = TastyProbeClient(tasty_config, client_factory=client_factory)
        self._state = _ProviderState()
        self._authed = False

    # ── classmethods for the factory ─────────────────────────────────

    @classmethod
    def from_env(
        cls,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
        strict: bool = True,
    ) -> TastytradeQuoteProvider:
        return cls(
            tasty_config=_probe_config_from_env(),
            validation=validation_from_env(),
            client_factory=client_factory,
            strict=strict,
        )

    # ── auth (lazy on first call) ────────────────────────────────────

    def _ensure_authed(self) -> bool:
        if self._authed:
            return True
        out = self._probe.login()
        if not out.get("auth_success"):
            # Sanitized failure — login output never contains tokens.
            reason = out.get("reason") or out.get("error_type") or f"http={out.get('http_status')}"
            self._state.last_error = f"auth_failed:{reason}"
            log.warning(
                "TastytradeQuoteProvider auth failed (mode=%s, http=%s)",
                out.get("auth_mode"), out.get("http_status"),
            )
            return False
        self._authed = True
        return True

    # ── QuoteProvider protocol: spot ─────────────────────────────────

    def get_spot(self, symbol: str) -> SpotQuote | None:
        if not self._ensure_authed():
            return None
        # Tasty's /market-data/by-type with `index` param returns spot for
        # SPX/SPXW/NDX/RUT/XSP — but the probe doesn't have this helper.
        # For Phase 4 we don't actually need broker spot for VW (the scanner
        # uses the structure spot). Return None as documented.
        return None

    # ── QuoteProvider protocol: per-strike quote (back-compat) ───────

    def get_option_quote(
        self,
        symbol: str,
        expiry: str,
        strike: float,
        right: Right,
    ) -> OptionQuote | None:
        """Single-strike lookup. Auto-resolves root, applies validation."""
        if not self._ensure_authed():
            return None
        side = OptionType.CALL if right == "C" else OptionType.PUT
        chain = self.get_option_chain(symbol, expiry=expiry, request=QuoteRequest(
            symbol=symbol, expiry=expiry, required_strikes=(float(strike),),
        ))
        if chain is None:
            return None
        return chain.find(float(strike), side)

    # ── QuoteProvider protocol: full chain (REST) ────────────────────

    def get_option_chain(
        self,
        symbol: str,
        expiry: str | None = None,
        request: QuoteRequest | None = None,
    ) -> OptionChainSnapshot | None:
        """Build an `OptionChainSnapshot` from Tasty REST quotes.

        Phase 4 contract:
          - Requires `request.required_strikes` to be non-empty. (REST has
            no efficient whole-chain pull — the scanner always knows which
            anchors it needs.)
          - Auto-resolves SPX → SPXW for daily/weekly/0DTE expirations.
          - Fetches BOTH sides (C + P) for every required strike via
            /market-data/by-type (capped at 100 symbols per call).
          - Applies `QuoteValidation` per quote; the chain still contains
            failed quotes but each carries `validation_passed=False` +
            `validation_rejection_reason='...'` so the scanner can render
            them and decide whether to skip-on-validation.
        """
        if not self._ensure_authed():
            return None

        eff_expiry = (request.expiry if request else None) or expiry
        if not eff_expiry:
            log.warning("TastytradeQuoteProvider.get_option_chain: no expiry supplied")
            return None
        if request is None or not request.required_strikes:
            # Phase 4 doesn't pull whole chains — REST cost is per-symbol.
            # The scanner always provides required_strikes via QuoteRequest
            # (see Phase 2.6). If we ever wire a non-scanner caller, they
            # must supply required_strikes too.
            log.warning(
                "TastytradeQuoteProvider.get_option_chain: no required_strikes "
                "in QuoteRequest — production provider does not pull whole chains"
            )
            return None

        # 1) resolve root (auto SPX→SPXW unless caller wrote it directly)
        root_meta = self._probe.resolve_root_for(symbol, eff_expiry)
        if not root_meta.get("ok"):
            self._state.last_error = (
                f"chain_unresolved:{root_meta.get('reason')}"
            )
            log.warning(
                "Could not resolve root for %s @ %s — %s. Available: %s",
                symbol, eff_expiry,
                root_meta.get("reason"),
                root_meta.get("available_roots"),
            )
            return None
        resolved_root = root_meta["root_symbol"]
        resolution_source = root_meta.get("source") or "auto_chain"

        # 2) build OCC symbols for BOTH sides at each required strike
        strikes = sorted({float(s) for s in request.required_strikes})
        occ_symbols: list[str] = []
        for k in strikes:
            occ_symbols.append(_build_occ_option_symbol(resolved_root, eff_expiry, k, "C"))
            occ_symbols.append(_build_occ_option_symbol(resolved_root, eff_expiry, k, "P"))
        if len(occ_symbols) > 100:
            # Tasty's /market-data/by-type combined cap. VW typically needs
            # 4 strikes × 2 sides = 8 symbols — well under. We just warn.
            log.warning("TastytradeQuoteProvider: %d symbols requested, capping at 100",
                        len(occ_symbols))
            occ_symbols = occ_symbols[:100]

        # 3) fetch quotes (probe handles the HTTP + auth header)
        result = self._probe.get_option_quotes(occ_symbols)
        if not result.get("ok"):
            self._state.last_error = f"quote_fetch_failed:http={result.get('http_status')}"
            return None
        self._state.quote_pulls += 1

        # 4) parse + validate
        now = _now_utc()
        chain_ts = now
        quotes: list[OptionQuote] = []
        for raw in (result.get("quotes") or []):
            occ = raw.get("symbol") or ""
            # Recover strike + right from OCC symbol shape ROOT(6) YYMMDD R STRIKE(8)
            try:
                right_letter = occ[12]
                strike_int = int(occ[13:21])
                strike_val = strike_int / 1000.0
            except (IndexError, ValueError):
                continue
            side = OptionType.CALL if right_letter == "C" else OptionType.PUT
            quote_time = _parse_iso(raw.get("ts")) or now
            bid  = _safe_float(raw.get("bid"))
            ask  = _safe_float(raw.get("ask"))
            mid  = _safe_float(raw.get("mid"))
            mark = _safe_float(raw.get("mark"))
            # `last` is captured here for future use; intentionally unused
            # by Phase 4 (mid-or-mark drives strategy pricing).
            _ = _safe_float(raw.get("last"))
            # Mid derivation: prefer broker-reported, else (bid+ask)/2, else mark
            if mid is None and bid is not None and ask is not None:
                mid = (bid + ask) / 2.0
            if mid is None and mark is not None:
                mid = mark

            tentative = OptionQuote(
                underlying=symbol.upper(),
                expiry=eff_expiry,
                option_type=side,
                strike=strike_val,
                bid=bid, ask=ask, mid=mid,
                volume=_safe_float(raw.get("volume")),
                open_interest=_safe_float(raw.get("open_interest")),
                quote_time=quote_time,
                vendor_symbol=occ,
                # Greeks are not in the by-type REST response.
            )
            passed, reason = self._validation.validate(tentative, now=now)
            # Re-build the quote with validation fields (frozen dataclass)
            quotes.append(OptionQuote(
                underlying=tentative.underlying,
                expiry=tentative.expiry,
                option_type=tentative.option_type,
                strike=tentative.strike,
                bid=tentative.bid, ask=tentative.ask, mid=tentative.mid,
                volume=tentative.volume,
                open_interest=tentative.open_interest,
                quote_time=tentative.quote_time,
                vendor_symbol=tentative.vendor_symbol,
                iv=None, delta=None, gamma=None, vega=None, theta=None,
                validation_passed=passed,
                validation_rejection_reason=reason,
            ))

        # 5) wrap in OptionChainSnapshot. Use the scanner's spot_hint
        # (= structure spot) when present; real broker spot isn't needed
        # by VW today. Root-resolution metadata rides on the snapshot.
        spot = (request.spot_hint if request and request.spot_hint else 0.0)
        snap = OptionChainSnapshot(
            underlying=symbol.upper(),
            spot=float(spot),
            expiry=eff_expiry,
            quotes=quotes,
            quote_ts=chain_ts,
            provider_name=self.name,
            resolved_root_symbol=resolved_root,
            root_resolution_source=resolution_source,
        )
        self._state.last_chain_ts = chain_ts
        self._state.chain_pulls += 1
        log.info(
            "Tasty chain: symbol=%s expiry=%s resolved_root=%s strikes=%d quotes=%d (%d failed validation)",
            symbol, eff_expiry, resolved_root, len(strikes), len(quotes),
            sum(1 for q in quotes if q.validation_passed is False),
        )
        return snap

    # ── QuoteProvider protocol: metadata ─────────────────────────────

    def quote_timestamp(self) -> datetime | None:
        return self._state.last_chain_ts or self._state.last_spot_ts

    def status(self) -> QuoteProviderStatus:
        return QuoteProviderStatus(
            provider_name=self.name,
            connected=self._authed and self._state.last_error is None,
            last_spot_ts=self._state.last_spot_ts,
            last_chain_ts=self._state.last_chain_ts,
            last_error=self._state.last_error,
            notes=(
                f"auth_mode={self._cfg.auth_mode()}; "
                f"chain_pulls={self._state.chain_pulls}; "
                f"quote_pulls={self._state.quote_pulls}; "
                "execution_blocked=true; probe_exposes_submit_path=false"
            ),
        )


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    """Wall-clock 'now' in UTC (timezone-aware)."""
    # Use a local helper to make testing deterministic via monkeypatch.
    try:
        return now_et().astimezone(UTC)
    except Exception:
        return datetime.now(UTC)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        # Make tz-aware if naive so age math works
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            v = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


# ──────────────────────────────────────────────────────────────────────
# Defensive non-export — no order paths
# ──────────────────────────────────────────────────────────────────────

# The production provider does NOT define any order-related methods.
# These names exist only as a defensive check in tests:
#   assert not hasattr(TastytradeQuoteProvider, "submit_order")
# (See tests/test_phase4_tastytrade_provider.py)
__order_methods_exposed__: tuple[str, ...] = ()


# Convenience module-level alias so tests + scanner can import either name.
__all__ = [
    "TastytradeConfigurationError",
    "TastytradeQuoteProvider",
    "validation_from_env",
]


# Avoid the unused-import warning while keeping `field` available for
# subclasses that might extend `_ProviderState` later.
_ = field
