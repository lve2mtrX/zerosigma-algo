"""Mock quote provider — synthesizes spot + per-option marks deterministically.

For Phase 1 the cockpit has no broker. The mock provider gives the UI and
scanner something to display without requiring manual entry. Underlying
prices are computed by linear interpolation around a fixed spot.
"""

from __future__ import annotations

from datetime import datetime

from src.providers.quotes.base import OptionQuote, Right, SpotQuote
from src.utils.time import now_et

MOCK_SPOT = 5800.0
DEFAULT_HALF_SPREAD = 0.05


class MockQuoteProvider:
    """Deterministic spot + option quotes — no broker required."""

    name = "mock"

    def __init__(self, spot: float = MOCK_SPOT, half_spread: float = DEFAULT_HALF_SPREAD,
                 **_: object) -> None:
        self.spot = float(spot)
        self.half_spread = float(half_spread)
        self._last_ts: datetime | None = None

    def get_spot(self, symbol: str) -> SpotQuote | None:
        ts = now_et()
        self._last_ts = ts
        return SpotQuote(
            symbol=symbol,
            last=self.spot,
            bid=self.spot - 0.10,
            ask=self.spot + 0.10,
            ts=ts,
        )

    def get_option_quote(
        self,
        symbol: str,
        expiry: str,
        strike: float,
        right: Right,
    ) -> OptionQuote | None:
        """Crude intrinsic-plus-time price; matches the stub chain shape."""
        intrinsic = max(self.spot - strike, 0.0) if right == "C" else max(strike - self.spot, 0.0)
        # toy time value tapering with distance
        time_value = max(0.05, 2.0 - 0.05 * abs(strike - self.spot))
        mid = intrinsic + time_value
        ts = now_et()
        self._last_ts = ts
        return OptionQuote(
            symbol=symbol,
            expiry=expiry,
            strike=float(strike),
            right=right,
            bid=max(0.05, mid - self.half_spread),
            ask=mid + self.half_spread,
            mid=mid,
            ts=ts,
        )

    def quote_timestamp(self) -> datetime | None:
        return self._last_ts
