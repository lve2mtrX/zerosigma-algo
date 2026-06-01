"""Phase 1 stub StructureProvider.

Returns a hand-crafted SPX snapshot so the scanner / strategies / UI can
be exercised end-to-end without a network call.

The dataset is intentionally rich enough to drive the Vertical Wing v1
strategy through every code path:

  - PUT_CEILING (2K)  at 5810  → cheapest CALL_CREDIT candidate
  - PUT_CEILING (5K)  at 5815  → wider CALL_CREDIT candidate (deeper OTM)
  - CALL_FLOOR (2K)   at 5790  → cheapest PUT_CREDIT candidate
  - CALL_FLOOR (5K)   at 5785  → wider PUT_CREDIT candidate (deeper OTM)
  - one near-the-money strike (5805) with thin volume & a wide bid/ask
    so the bid/ask + distance hard filters have something to reject.

Deterministic: same call → same numbers (no randomness, no clock-derived
inputs). The quote timestamp is taken at call time so freshness checks
work in the UI.
"""

from __future__ import annotations

from datetime import datetime

from src.providers.structure.types import (
    ChainRow,
    ExposureContext,
    StructureSnapshot,
)
from src.utils.time import now_et

SPOT = 5800.0
EXPIRY_LOOKBACK = "2026-05-31"   # default — overridden at call time


def _row(
    strike: int,
    *,
    c_vol: float,
    p_vol: float,
    c_mid: float | None,
    p_mid: float | None,
    bid_ask_width: float = 0.10,
) -> ChainRow:
    """Build one chain row. Mids are explicit; bid/ask derived symmetrically."""
    def b_a(mid: float | None) -> tuple[float | None, float | None]:
        if mid is None:
            return (None, None)
        half = bid_ask_width / 2.0
        return (max(0.05, mid - half), mid + half)

    cb, ca = b_a(c_mid)
    pb, pa = b_a(p_mid)
    return ChainRow(
        strike=float(strike),
        # call side
        c_bid=cb, c_ask=ca, c_mid=c_mid,
        c_iv=0.16, c_delta=0.30, c_gamma=0.01,
        c_oi=1000.0, c_volume=c_vol,
        # put side
        p_bid=pb, p_ask=pa, p_mid=p_mid,
        p_iv=0.18, p_delta=-0.30, p_gamma=0.01,
        p_oi=1100.0, p_volume=p_vol,
        # populated exposures (illustrative only — match the brand)
        c_da_gex_1pct=0.05, p_da_gex_1pct=-0.05,
        c_dex_1pct=0.02,    p_dex_1pct=-0.02,
        c_vex_1vol=0.01,    p_vex_1vol=0.01,
    )


def _build_chain() -> list[ChainRow]:
    """Deterministic 5-pt grid from 5780 to 5830 around spot 5800.

    Volumes are wired so:
      - PUT_CEILING(2K) = 5815, PUT_CEILING(5K) = 5810 (5810 also satisfies 2K
        but is below 5815 → highest qualifying wins).
      - CALL_FLOOR(2K)  = 5785, CALL_FLOOR(5K)  = 5790 (5785 also satisfies 5K
        on the absolute-low side; the named "5K" floor is conceptually the
        more constrained / closer-to-spot level — surfaced via exposures).
    Mid prices are tuned so the natural picks clear $0.30 min_credit and
    the 10% planned / 30% theoretical caps under aggressive_paper_10k.
    """
    return [
        # ── below spot — CALL_FLOOR territory ──
        _row(5780, c_vol=300,  p_vol=200,  c_mid=18.50, p_mid=0.85),
        _row(5785, c_vol=2200, p_vol=300,  c_mid=14.20, p_mid=1.60),   # 2K CALL_FLOOR (lowest @2K)
        _row(5790, c_vol=5400, p_vol=400,  c_mid=10.40, p_mid=2.50),   # also 5K-qualifying
        _row(5795, c_vol=600,  p_vol=500,  c_mid=6.80,  p_mid=3.40),
        # ── at-the-money — thin, wide bid/ask (would trip filters if picked) ──
        _row(5800, c_vol=120,  p_vol=120,  c_mid=3.60,  p_mid=3.60, bid_ask_width=0.50),
        _row(5805, c_vol=200,  p_vol=180,  c_mid=1.95,  p_mid=4.95, bid_ask_width=0.40),
        # ── above spot — PUT_CEILING territory ──
        _row(5810, c_vol=400,  p_vol=5500, c_mid=1.80,  p_mid=8.80),   # 5K PUT_CEILING (also 2K)
        _row(5815, c_vol=350,  p_vol=4500, c_mid=1.10,  p_mid=13.40),  # 2K PUT_CEILING (highest @2K)
        _row(5820, c_vol=250,  p_vol=400,  c_mid=0.50,  p_mid=18.20),
        _row(5825, c_vol=180,  p_vol=300,  c_mid=0.20,  p_mid=23.10),
        _row(5830, c_vol=150,  p_vol=200,  c_mid=0.10,  p_mid=28.05),
    ]


def _exposures(chain: list[ChainRow]) -> ExposureContext:
    """Derive the Vertical-Wing level fields from the deterministic chain."""
    put_ceiling_2k = _highest_strike(chain, lambda r: (r.p_volume or 0) >= 2000)
    put_ceiling_5k = _highest_strike(chain, lambda r: (r.p_volume or 0) >= 5000)
    call_floor_2k  = _lowest_strike(chain,  lambda r: (r.c_volume or 0) >= 2000)
    call_floor_5k  = _lowest_strike(chain,  lambda r: (r.c_volume or 0) >= 5000)

    # MaxVol = strike with greatest combined volume
    max_row = max(chain, key=lambda r: (r.c_volume or 0) + (r.p_volume or 0))
    return ExposureContext(
        total_gex_bn=4.2,
        total_vex_bn=-1.1,
        gamma_flip=5795.0,
        call_wall=5825.0,
        put_wall=5780.0,
        maxvol=max_row.strike,
        gamma_regime="positive",
        da_gex_signed=1.8,
        put_ceiling_2k=put_ceiling_2k,
        put_ceiling_5k=put_ceiling_5k,
        call_floor_2k=call_floor_2k,
        call_floor_5k=call_floor_5k,
        ddoi_pin=5800.0,
    )


def _highest_strike(chain: list[ChainRow], pred) -> float | None:
    candidates = [r.strike for r in chain if pred(r)]
    return max(candidates) if candidates else None


def _lowest_strike(chain: list[ChainRow], pred) -> float | None:
    candidates = [r.strike for r in chain if pred(r)]
    return min(candidates) if candidates else None


class StubStructureProvider:
    name = "stub"

    def __init__(self, **_: object) -> None:
        self._last_refresh: dict[str, float] = {}

    def get_snapshot(self, symbol: str) -> StructureSnapshot:
        now = now_et()
        self._last_refresh[symbol] = now.timestamp()
        chain = _build_chain()
        return StructureSnapshot(
            symbol=symbol,
            spot=SPOT,
            quote_ts=now,
            chain=chain,
            exposures=_exposures(chain),
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
