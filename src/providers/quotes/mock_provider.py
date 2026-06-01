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
    SpotQuote,
)
from src.utils.time import now_et

DEFAULT_HALF_SPREAD = 0.05


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


class MockQuoteProvider:
    """Deterministic spot + option chain — no broker required."""

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
    ) -> OptionChainSnapshot | None:
        ts = now_et()
        self._last_chain_ts = ts
        eff_expiry = expiry or ts.strftime("%Y-%m-%d")
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
