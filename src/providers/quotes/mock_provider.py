"""Mock quote provider — synthesizes spot + a full option chain deterministically.

Reads from `src.providers._mock_data.MOCK_CHAIN`, the same canonical dataset
that `StubStructureProvider` uses to compute structure context. The two
providers stay in agreement WITHOUT either importing the other.

The mock is used by the Phase 1 cockpit / scanner / tests. Replace with a
real broker provider when the capability probe (Phase 4) picks one.
"""

from __future__ import annotations

from datetime import datetime

from src.providers._mock_data import MOCK_CHAIN, SPOT, MockStrikeRow
from src.providers.quotes.base import Right
from src.providers.quotes.types import (
    OptionChainSnapshot,
    OptionQuote,
    OptionType,
    QuoteProviderStatus,
    QuoteRequest,
    SpotQuote,
)
from src.utils.time import now_et

DEFAULT_HALF_SPREAD = 0.05
DEFAULT_GRID_STEP   = 5.0   # 5pt SPX-style grid for synthesized chains
DEFAULT_GRID_HALFWIDTH = 25.0  # +/- N points around spot_hint for the chain


def _build_quote(
    row: MockStrikeRow,
    *,
    side: OptionType,
    underlying: str,
    expiry: str,
    ts: datetime,
) -> OptionQuote:
    if side == OptionType.CALL:
        mid, vol, oi = row.c_mid, row.c_volume, row.c_open_interest
        iv, delta, gamma = row.c_iv, row.c_delta, row.c_gamma
    else:
        mid, vol, oi = row.p_mid, row.p_volume, row.p_open_interest
        iv, delta, gamma = row.p_iv, row.p_delta, row.p_gamma
    half = row.bid_ask_width / 2.0
    bid = max(0.05, mid - half) if mid is not None else None
    ask = mid + half if mid is not None else None
    side_letter = "C" if side == OptionType.CALL else "P"
    return OptionQuote(
        underlying=underlying,
        expiry=expiry,
        option_type=side,
        strike=row.strike,
        bid=bid, ask=ask, mid=mid,
        volume=vol,
        open_interest=oi,
        quote_time=ts,
        vendor_symbol=f".{underlying}{expiry.replace('-', '')}{side_letter}{int(row.strike)}",
        iv=iv, delta=delta, gamma=gamma,
    )


def _synth_quote(
    strike: float,
    spot_hint: float,
    *,
    side: OptionType,
    underlying: str,
    expiry: str,
    ts: datetime,
    half_spread: float = DEFAULT_HALF_SPREAD,
) -> OptionQuote:
    """Build a synthesized OptionQuote at any strike around `spot_hint`.

    Pricing is intentionally simple — intrinsic value + a flat time
    premium that tapers with distance from the hint.

    Volume / OI: if this strike happens to match a row in `MOCK_CHAIN`
    (i.e. we're synthesizing at a spot_hint near the static chain's 5800
    center), inherit the canonical volume/OI so Phase 1.5 behavior is
    preserved. Otherwise emit token values; a real broker would supply
    the actual numbers.
    """
    side_letter = "C" if side == OptionType.CALL else "P"

    # Prefer the canonical mock-chain row when this strike matches one. That
    # keeps Phase 1.5 default-mode behavior identical: same mids, same
    # volumes, same spreads at the 5780–5830 grid.
    match = next((r for r in MOCK_CHAIN if r.strike == float(strike)), None)
    if match is not None:
        if side == OptionType.CALL:
            mid_static, vol, oi = match.c_mid, match.c_volume, match.c_open_interest
        else:
            mid_static, vol, oi = match.p_mid, match.p_volume, match.p_open_interest
        half = match.bid_ask_width / 2.0
        bid = round(max(0.05, (mid_static or 0.0) - half), 2)
        ask = round((mid_static or 0.0) + half, 2)
        mid = round(mid_static or 0.0, 2)
    else:
        # No matching canonical row → synthesize.
        if side == OptionType.CALL:
            intrinsic = max(spot_hint - strike, 0.0)
        else:
            intrinsic = max(strike - spot_hint, 0.0)
        distance = abs(strike - spot_hint)
        # Intercept of 4.00 keeps the per-leg time-value differential
        # at deep OTM (>20pt) large enough that the 5-pt-wide VW spread
        # still clears the $0.30 min_credit floor.
        time_value = max(0.10, 4.00 - 0.10 * distance)
        mid = round(intrinsic + time_value, 2)
        bid = round(max(0.05, mid - half_spread), 2)
        ask = round(mid + half_spread, 2)
        vol, oi = 100.0, 500.0

    return OptionQuote(
        underlying=underlying,
        expiry=expiry,
        option_type=side,
        strike=float(strike),
        bid=bid, ask=ask, mid=mid,
        volume=vol,
        open_interest=oi,
        quote_time=ts,
        vendor_symbol=f".{underlying}{expiry.replace('-', '')}{side_letter}{int(strike)}",
        iv=0.18 if side == OptionType.CALL else 0.20,
        delta=0.30 if side == OptionType.CALL else -0.30,
        gamma=0.01,
    )


class MockQuoteProvider:
    """Deterministic spot + option chain — no broker required.

    Modes:
      - DEFAULT (request=None): returns the hardcoded `MOCK_CHAIN` centered
        on 5800. Used by Phase 1 stub tests + the basic scanner smoke.
      - ALIGNED (request.spot_hint set OR required_strikes given): synthesizes
        a chain re-centered on `spot_hint` (or the median of the required
        strikes when no hint is given), guaranteed to include every
        `required_strikes` value with both sides. Used by Phase 2.6 when the
        live ZS structure puts levels at e.g. 7580 — the static 5800 chain
        would otherwise have zero overlapping strikes.
    """

    name = "mock"

    def __init__(self, spot: float = SPOT, **_: object) -> None:
        self.spot = float(spot)
        self._last_spot_ts: datetime | None = None
        self._last_chain_ts: datetime | None = None

    # ── spot ──────────────────────────────────────────────────────────

    def get_spot(self, symbol: str) -> SpotQuote | None:
        ts = now_et()
        self._last_spot_ts = ts
        return SpotQuote(
            symbol=symbol,
            last=self.spot,
            bid=self.spot - 0.10,
            ask=self.spot + 0.10,
            ts=ts,
        )

    # ── per-strike quote (back-compat for older callers) ──────────────

    def get_option_quote(
        self,
        symbol: str,
        expiry: str,
        strike: float,
        right: Right,
    ) -> OptionQuote | None:
        for row in MOCK_CHAIN:
            if row.strike == float(strike):
                side = OptionType.CALL if right == "C" else OptionType.PUT
                ts = now_et()
                self._last_chain_ts = ts
                return _build_quote(row, side=side, underlying=symbol, expiry=expiry, ts=ts)
        return None

    # ── full chain ────────────────────────────────────────────────────

    def get_option_chain(
        self,
        symbol: str,
        expiry: str | None = None,
        request: QuoteRequest | None = None,
    ) -> OptionChainSnapshot | None:
        ts = now_et()
        self._last_chain_ts = ts
        eff_expiry = (request.expiry if request else None) or expiry or ts.strftime("%Y-%m-%d")

        # Decide whether to use the static MOCK_CHAIN (default) or to
        # synthesize a re-centered chain.
        use_alignment = request is not None and (
            request.spot_hint is not None or bool(request.required_strikes)
        )
        if not use_alignment:
            quotes: list[OptionQuote] = []
            for row in MOCK_CHAIN:
                quotes.append(_build_quote(row, side=OptionType.CALL,
                                           underlying=symbol, expiry=eff_expiry, ts=ts))
                quotes.append(_build_quote(row, side=OptionType.PUT,
                                           underlying=symbol, expiry=eff_expiry, ts=ts))
            return OptionChainSnapshot(
                underlying=symbol,
                spot=self.spot,
                expiry=eff_expiry,
                quotes=quotes,
                quote_ts=ts,
                provider_name=self.name,
            )

        # ── ALIGNED mode ──
        # Determine the center: explicit hint > median of required > default.
        if request.spot_hint is not None:
            center = float(request.spot_hint)
        elif request.required_strikes:
            xs = sorted(float(s) for s in request.required_strikes)
            center = xs[len(xs) // 2]
        else:
            center = self.spot

        # Build the grid bounds.
        lo = request.strike_min if request.strike_min is not None else center - DEFAULT_GRID_HALFWIDTH
        hi = request.strike_max if request.strike_max is not None else center + DEFAULT_GRID_HALFWIDTH
        if request.required_strikes:
            lo = min(lo, min(request.required_strikes))
            hi = max(hi, max(request.required_strikes))

        # Snap grid to 5-pt steps from a centered base, then UNION in all
        # required strikes (they may not be on the grid).
        step = DEFAULT_GRID_STEP
        n_below = int((center - lo) / step) + 1
        n_above = int((hi - center) / step) + 1
        grid: set[float] = {round(center + step * k, 4)
                            for k in range(-n_below, n_above + 1)}
        for s in request.required_strikes:
            grid.add(float(s))
        strikes_sorted = sorted(grid)

        # Synthesize both sides at every strike on the grid.
        quotes: list[OptionQuote] = []
        for k in strikes_sorted:
            quotes.append(_synth_quote(k, center, side=OptionType.CALL,
                                       underlying=symbol, expiry=eff_expiry, ts=ts))
            quotes.append(_synth_quote(k, center, side=OptionType.PUT,
                                       underlying=symbol, expiry=eff_expiry, ts=ts))
        return OptionChainSnapshot(
            underlying=symbol,
            spot=center,
            expiry=eff_expiry,
            quotes=quotes,
            quote_ts=ts,
            provider_name=self.name,
        )

    # ── metadata ──────────────────────────────────────────────────────

    def quote_timestamp(self) -> datetime | None:
        return self._last_chain_ts or self._last_spot_ts

    def status(self) -> QuoteProviderStatus:
        return QuoteProviderStatus(
            provider_name=self.name,
            connected=True,
            last_spot_ts=self._last_spot_ts,
            last_chain_ts=self._last_chain_ts,
            notes="deterministic mock — no broker connected",
        )
