"""Phase 1 stub StructureProvider.

Returns a hand-crafted SPX snapshot so the scanner / strategies / UI can be
exercised end-to-end without a network call.
"""

from __future__ import annotations

from datetime import datetime

from src.providers.structure.types import (
    ChainRow,
    ExposureContext,
    StructureSnapshot,
)
from src.utils.time import now_et


class StubStructureProvider:
    name = "stub"

    def __init__(self, **_: object) -> None:
        self._last_refresh: dict[str, float] = {}

    def get_snapshot(self, symbol: str) -> StructureSnapshot:
        now = now_et()
        self._last_refresh[symbol] = now.timestamp()
        spot = 5800.0

        # Toy 5-point grid around spot with concentrated volume at 5810 (puts)
        # so PUT_CEILING_CALL_CREDIT has something to bite on.
        strikes = list(range(5780, 5836, 5))
        rows: list[ChainRow] = []
        for k in strikes:
            put_vol = 2500 if k == 5810 else (300 if abs(k - spot) <= 15 else 0)
            call_vol = 2200 if k == 5790 else (250 if abs(k - spot) <= 15 else 0)
            rows.append(
                ChainRow(
                    strike=float(k),
                    c_bid=max(0.05, spot + 5 - k) if k < spot else 0.50,
                    c_ask=max(0.10, spot + 5 - k) if k < spot else 0.65,
                    c_mid=max(0.08, spot + 5 - k) if k < spot else 0.57,
                    c_iv=0.16,
                    c_delta=0.30,
                    c_gamma=0.01,
                    c_oi=1000,
                    c_volume=float(call_vol),
                    p_bid=max(0.05, k - spot + 5) if k > spot else 0.50,
                    p_ask=max(0.10, k - spot + 5) if k > spot else 0.65,
                    p_mid=max(0.08, k - spot + 5) if k > spot else 0.57,
                    p_iv=0.18,
                    p_delta=-0.30,
                    p_gamma=0.01,
                    p_oi=1100,
                    p_volume=float(put_vol),
                )
            )
        return StructureSnapshot(
            symbol=symbol,
            spot=spot,
            quote_ts=now,
            chain=rows,
            exposures=ExposureContext(
                total_gex_bn=4.2, total_vex_bn=-1.1, gamma_flip=5795.0,
                call_wall=5825.0, put_wall=5780.0,
                maxvol=5810.0, gamma_regime="positive", da_gex_signed=1.8,
            ),
            expiry=now.strftime("%Y-%m-%d"),
            dte=0,
            source="stub",
        )

    def is_fresh(self, symbol: str, max_age_seconds: int) -> bool:
        last = self._last_refresh.get(symbol)
        if last is None:
            return False
        return (datetime.now().timestamp() - last) <= max_age_seconds

    def last_refresh_ts(self, symbol: str) -> float | None:
        return self._last_refresh.get(symbol)
