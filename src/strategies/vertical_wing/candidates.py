"""Vertical Wing v1 — candidate generation.

PUT_CEILING_CALL_CREDIT:
    Sell call at the StructureProvider's PUT_CEILING level; buy call at
    PUT_CEILING + spread_width. Quote lookup against the chain snapshot.

CALL_FLOOR_PUT_CREDIT:
    Sell put at the StructureProvider's CALL_FLOOR level; buy put at
    CALL_FLOOR - spread_width.

This module knows about STRUCTURE LEVELS only via the typed
`ExposureContext` fields. It knows about PRICING only via
`OptionChainSnapshot.find(...)`. No reach-through into either provider's
internals.
"""

from __future__ import annotations

from src.providers.quotes.types import (
    OptionChainSnapshot,
    OptionQuote,
    OptionType,
    SpreadQuote,
)
from src.providers.structure.types import StructureSnapshot
from src.strategies.base import Candidate


def _ceiling_for_threshold(structure: StructureSnapshot, threshold: float) -> float | None:
    """Pick PUT_CEILING (2K or 5K) based on the configured volume threshold."""
    if threshold >= 5000 and structure.exposures.put_ceiling_5k is not None:
        return structure.exposures.put_ceiling_5k
    return structure.exposures.put_ceiling_2k


def _floor_for_threshold(structure: StructureSnapshot, threshold: float) -> float | None:
    """Pick CALL_FLOOR (2K or 5K) based on the configured volume threshold."""
    if threshold >= 5000 and structure.exposures.call_floor_5k is not None:
        return structure.exposures.call_floor_5k
    return structure.exposures.call_floor_2k


def _safe_mid(q: OptionQuote) -> float | None:
    if q.mid is not None:
        return q.mid
    if q.bid is None or q.ask is None:
        return None
    return (q.bid + q.ask) / 2.0


def _bid_ask_quality_score(short: OptionQuote, long_: OptionQuote, cap: float) -> float:
    """1.0 = tight quotes; 0.0 = wider than `cap`. Linear in between."""
    if cap <= 0:
        return 0.5
    spreads: list[float] = []
    for q in (short, long_):
        if q.bid_ask_spread is None:
            return 0.0
        spreads.append(q.bid_ask_spread)
    worst = max(spreads)
    return max(0.0, min(1.0, 1.0 - (worst / cap)))


def build_put_ceiling_call_credit(
    structure: StructureSnapshot,
    chain: OptionChainSnapshot,
    threshold: float,
    spread_width: float,
    strategy_id: str,
    max_bid_ask_width: float | None = None,
) -> Candidate | None:
    """SELL Call@K / BUY Call@(K + width) where K = StructureProvider's PUT_CEILING."""
    short_k = _ceiling_for_threshold(structure, threshold)
    if short_k is None:
        return None
    long_k = short_k + spread_width

    short_q = chain.find(short_k, OptionType.CALL)
    long_q  = chain.find(long_k,  OptionType.CALL)
    if short_q is None or long_q is None:
        return None

    short_mid = _safe_mid(short_q)
    long_mid  = _safe_mid(long_q)
    if short_mid is None or long_mid is None:
        return None

    # PUT_CEILING is defined by PUT volume at K, so the structure "strength"
    # signal is the put-side quote's volume — not the call leg's.
    anchor_put = chain.find(short_k, OptionType.PUT)
    anchor_volume = anchor_put.volume if anchor_put else None

    spread = SpreadQuote.from_legs(short_q, long_q)
    credit = short_mid - long_mid
    max_risk = max(0.0, spread_width - credit)
    rr = (credit / max_risk) if max_risk > 0 else 0.0
    breakeven = short_k + credit

    return Candidate(
        strategy_id=strategy_id,
        side="CALL_CREDIT",
        symbol=chain.underlying,
        expiry=chain.expiry,
        short_strike=short_k,
        long_strike=long_k,
        credit=credit,
        max_risk=max_risk,
        reward_risk=rr,
        breakeven=breakeven,
        distance_from_spot=short_k - chain.spot,
        meta={
            "construction": "PUT_CEILING_CALL_CREDIT",
            "anchor_volume": anchor_volume,         # put_volume at the ceiling
            "short_leg":     _quote_dict(short_q),
            "long_leg":      _quote_dict(long_q),
            "spread_quote":  _spread_dict(spread),
            "bid_ask_quality": _bid_ask_quality_score(
                short_q, long_q, max_bid_ask_width or 0.20,
            ),
            "threshold":     threshold,
        },
    )


def build_call_floor_put_credit(
    structure: StructureSnapshot,
    chain: OptionChainSnapshot,
    threshold: float,
    spread_width: float,
    strategy_id: str,
    max_bid_ask_width: float | None = None,
) -> Candidate | None:
    """SELL Put@K / BUY Put@(K - width) where K = StructureProvider's CALL_FLOOR."""
    short_k = _floor_for_threshold(structure, threshold)
    if short_k is None:
        return None
    long_k = short_k - spread_width

    short_q = chain.find(short_k, OptionType.PUT)
    long_q  = chain.find(long_k,  OptionType.PUT)
    if short_q is None or long_q is None:
        return None

    short_mid = _safe_mid(short_q)
    long_mid  = _safe_mid(long_q)
    if short_mid is None or long_mid is None:
        return None

    # CALL_FLOOR is defined by CALL volume at K, so the structure "strength"
    # signal is the call-side quote's volume — not the put leg's.
    anchor_call = chain.find(short_k, OptionType.CALL)
    anchor_volume = anchor_call.volume if anchor_call else None

    spread = SpreadQuote.from_legs(short_q, long_q)
    credit = short_mid - long_mid
    max_risk = max(0.0, spread_width - credit)
    rr = (credit / max_risk) if max_risk > 0 else 0.0
    breakeven = short_k - credit

    return Candidate(
        strategy_id=strategy_id,
        side="PUT_CREDIT",
        symbol=chain.underlying,
        expiry=chain.expiry,
        short_strike=short_k,
        long_strike=long_k,
        credit=credit,
        max_risk=max_risk,
        reward_risk=rr,
        breakeven=breakeven,
        distance_from_spot=chain.spot - short_k,
        meta={
            "construction": "CALL_FLOOR_PUT_CREDIT",
            "anchor_volume": anchor_volume,         # call_volume at the floor
            "short_leg":     _quote_dict(short_q),
            "long_leg":      _quote_dict(long_q),
            "spread_quote":  _spread_dict(spread),
            "bid_ask_quality": _bid_ask_quality_score(
                short_q, long_q, max_bid_ask_width or 0.20,
            ),
            "threshold":     threshold,
        },
    )


# ──────────────────────────────────────────────────────────────────────
# meta serializers — keep the candidate dict-safe for decision_log JSONL
# ──────────────────────────────────────────────────────────────────────

def _quote_dict(q: OptionQuote) -> dict:
    return {
        "strike": q.strike,
        "option_type": str(q.option_type),
        "bid": q.bid, "ask": q.ask, "mid": q.mid,
        "volume": q.volume, "open_interest": q.open_interest,
        "vendor_symbol": q.vendor_symbol,
    }


def _spread_dict(s: SpreadQuote) -> dict:
    return {
        "width": s.width,
        "credit_mid": s.credit_mid,
        "credit_bid": s.credit_bid,
        "credit_ask": s.credit_ask,
    }
