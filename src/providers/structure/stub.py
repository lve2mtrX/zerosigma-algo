"""Phase 1 stub StructureProvider — STRUCTURE CONTEXT ONLY.

After Phase 1.5 this provider returns NO chain quotes. It supplies
exposure aggregates + Vertical-Wing levels derived from the shared mock
dataset (`src.providers._mock_data`). Bid/ask/mid live on the QuoteProvider.

Deterministic: same call → same numbers.
"""

from __future__ import annotations

from datetime import datetime

from src.providers._mock_data import (
    SPOT,
    call_volume_at,
    highest_strike_where_put_volume_ge,
    lowest_strike_where_call_volume_ge,
    maxvol_strike,
    maxvol_total_volume,
    put_volume_at,
)
from src.providers.structure.types import ExposureContext, StructureSnapshot
from src.utils.time import now_et


def _build_exposures() -> ExposureContext:
    pc2 = highest_strike_where_put_volume_ge(2000)
    pc5 = highest_strike_where_put_volume_ge(5000)
    pc10 = highest_strike_where_put_volume_ge(10000)   # Phase 9H (mock peaks ~5.5K → None)
    cf2 = lowest_strike_where_call_volume_ge(2000)
    cf5 = lowest_strike_where_call_volume_ge(5000)
    cf10 = lowest_strike_where_call_volume_ge(10000)   # Phase 9H (mock peaks ~5.4K → None)
    return ExposureContext(
        total_gex_bn=4.2,
        total_vex_bn=-1.1,
        gamma_flip=5795.0,
        call_wall=5825.0,
        put_wall=5780.0,
        maxvol=maxvol_strike(),
        gamma_regime="positive",
        da_gex_signed=1.8,
        put_ceiling_2k=pc2,
        put_ceiling_5k=pc5,
        put_ceiling_10k=pc10,
        call_floor_2k=cf2,
        call_floor_5k=cf5,
        call_floor_10k=cf10,
        # Phase 2.8/9H — actual volumes that qualified each anchor.
        put_ceiling_2k_volume=put_volume_at(pc2),
        put_ceiling_5k_volume=put_volume_at(pc5),
        put_ceiling_10k_volume=put_volume_at(pc10),
        call_floor_2k_volume=call_volume_at(cf2),
        call_floor_5k_volume=call_volume_at(cf5),
        call_floor_10k_volume=call_volume_at(cf10),
        maxvol_volume=maxvol_total_volume(),
        # Phase 9H — demo gamma clusters for the sandbox (stub is mock/demo data,
        # so these are illustrative; the live provider maps gamma.cluster_*).
        gamma_primary=5795.0,    # flip cluster
        gamma_secondary=5825.0,  # call-wall cluster
        # DDOI not surfaced in prime cockpit (Phase 9H). Kept here for Advanced /
        # raw diagnostics only.
        ddoi_pin=5800.0,
    )


class StubStructureProvider:
    name = "stub"

    def __init__(self, **_: object) -> None:
        self._last_refresh: dict[str, float] = {}

    def get_snapshot(self, symbol: str) -> StructureSnapshot:
        now = now_et()
        self._last_refresh[symbol] = now.timestamp()
        return StructureSnapshot(
            symbol=symbol,
            spot=SPOT,
            quote_ts=now,
            exposures=_build_exposures(),
            expiry=now.strftime("%Y-%m-%d"),
            dte=0,
            source=self.name,
        )

    def is_fresh(self, symbol: str, max_age_seconds: int) -> bool:
        last = self._last_refresh.get(symbol)
        if last is None:
            return False
        return (datetime.now().timestamp() - last) <= max_age_seconds

    def last_refresh_ts(self, symbol: str) -> float | None:
        return self._last_refresh.get(symbol)
