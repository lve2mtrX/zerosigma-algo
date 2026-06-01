"""Vertical Wing v1 — candidate generation.

PUT_CEILING_CALL_CREDIT:
    Sell call at the highest strike where put_volume >= threshold;
    buy call at strike + spread_width.

CALL_FLOOR_PUT_CREDIT:
    Sell put at the lowest strike where call_volume >= threshold;
    buy put at strike - spread_width.
"""

from __future__ import annotations

from src.providers.structure.types import ChainRow, StructureSnapshot
from src.strategies.base import Candidate


def _row_by_strike(chain: list[ChainRow], strike: float) -> ChainRow | None:
    for r in chain:
        if r.strike == strike:
            return r
    return None


def _find_put_ceiling(chain: list[ChainRow], threshold: float) -> ChainRow | None:
    """Highest strike whose put_volume >= threshold."""
    qualifying = [r for r in chain if (r.p_volume or 0) >= threshold]
    if not qualifying:
        return None
    return max(qualifying, key=lambda r: r.strike)


def _find_call_floor(chain: list[ChainRow], threshold: float) -> ChainRow | None:
    """Lowest strike whose call_volume >= threshold."""
    qualifying = [r for r in chain if (r.c_volume or 0) >= threshold]
    if not qualifying:
        return None
    return min(qualifying, key=lambda r: r.strike)


def _safe_mid(bid: float | None, ask: float | None, mid_hint: float | None = None) -> float | None:
    if mid_hint is not None:
        return mid_hint
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def build_put_ceiling_call_credit(
    snapshot: StructureSnapshot,
    threshold: float,
    spread_width: float,
    strategy_id: str,
) -> Candidate | None:
    """SELL Call@K / BUY Call@(K + width) where K = put ceiling."""
    ceiling = _find_put_ceiling(snapshot.chain, threshold)
    if ceiling is None:
        return None
    short_k = ceiling.strike
    long_k = short_k + spread_width
    long_row = _row_by_strike(snapshot.chain, long_k)
    if long_row is None:
        return None

    short_mid = _safe_mid(ceiling.c_bid, ceiling.c_ask, ceiling.c_mid)
    long_mid = _safe_mid(long_row.c_bid, long_row.c_ask, long_row.c_mid)
    if short_mid is None or long_mid is None:
        return None

    credit = short_mid - long_mid
    max_risk = max(0.0, spread_width - credit)
    rr = (credit / max_risk) if max_risk > 0 else 0.0
    breakeven = short_k + credit

    return Candidate(
        strategy_id=strategy_id,
        side="CALL_CREDIT",
        symbol=snapshot.symbol,
        expiry=snapshot.expiry or "",
        short_strike=short_k,
        long_strike=long_k,
        credit=credit,
        max_risk=max_risk,
        reward_risk=rr,
        breakeven=breakeven,
        distance_from_spot=short_k - snapshot.spot,
        meta={
            "construction": "PUT_CEILING_CALL_CREDIT",
            "put_volume_at_ceiling": ceiling.p_volume,
        },
    )


def build_call_floor_put_credit(
    snapshot: StructureSnapshot,
    threshold: float,
    spread_width: float,
    strategy_id: str,
) -> Candidate | None:
    """SELL Put@K / BUY Put@(K - width) where K = call floor."""
    floor = _find_call_floor(snapshot.chain, threshold)
    if floor is None:
        return None
    short_k = floor.strike
    long_k = short_k - spread_width
    long_row = _row_by_strike(snapshot.chain, long_k)
    if long_row is None:
        return None

    short_mid = _safe_mid(floor.p_bid, floor.p_ask, floor.p_mid)
    long_mid = _safe_mid(long_row.p_bid, long_row.p_ask, long_row.p_mid)
    if short_mid is None or long_mid is None:
        return None

    credit = short_mid - long_mid
    max_risk = max(0.0, spread_width - credit)
    rr = (credit / max_risk) if max_risk > 0 else 0.0
    breakeven = short_k - credit

    return Candidate(
        strategy_id=strategy_id,
        side="PUT_CREDIT",
        symbol=snapshot.symbol,
        expiry=snapshot.expiry or "",
        short_strike=short_k,
        long_strike=long_k,
        credit=credit,
        max_risk=max_risk,
        reward_risk=rr,
        breakeven=breakeven,
        distance_from_spot=snapshot.spot - short_k,
        meta={
            "construction": "CALL_FLOOR_PUT_CREDIT",
            "call_volume_at_floor": floor.c_volume,
        },
    )
