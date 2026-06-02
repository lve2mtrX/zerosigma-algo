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


def _ceiling_for_threshold(
    structure: StructureSnapshot, threshold: float,
) -> tuple[float | None, str | None, float | None]:
    """Pick PUT_CEILING (2K or 5K) AND its anchor source + structure volume.

    Returns (strike, anchor_source, anchor_volume_from_structure).
    `anchor_source` is "put_ceiling_5k" | "put_ceiling_2k" | None.
    `anchor_volume_from_structure` may be None when the structure source
    didn't carry per-strike volume (e.g. wings-only fallback).
    """
    exp = structure.exposures
    if threshold >= 5000 and exp.put_ceiling_5k is not None:
        return (exp.put_ceiling_5k, "put_ceiling_5k", exp.put_ceiling_5k_volume)
    if exp.put_ceiling_2k is not None:
        return (exp.put_ceiling_2k, "put_ceiling_2k", exp.put_ceiling_2k_volume)
    return (None, None, None)


def _floor_for_threshold(
    structure: StructureSnapshot, threshold: float,
) -> tuple[float | None, str | None, float | None]:
    exp = structure.exposures
    if threshold >= 5000 and exp.call_floor_5k is not None:
        return (exp.call_floor_5k, "call_floor_5k", exp.call_floor_5k_volume)
    if exp.call_floor_2k is not None:
        return (exp.call_floor_2k, "call_floor_2k", exp.call_floor_2k_volume)
    return (None, None, None)


def _safe_mid(q: OptionQuote) -> float | None:
    if q.mid is not None:
        return q.mid
    if q.bid is None or q.ask is None:
        return None
    return (q.bid + q.ask) / 2.0


def _bid_ask_quality_score(short: OptionQuote, long_: OptionQuote, cap: float) -> float:
    """1.0 = tight quotes; 0.0 = wider than `cap`. Linear in between.

    Phase 4.1 NOTE: `max_bid_ask_width` reaches this function through
    `params` via `SessionConfig.to_filter_params()` → strategy default
    params merge → `build_*_credit(..., max_bid_ask_width=...)`. The cap
    today is an absolute-dollar value (default 0.20). A LATER phase (4.2)
    may switch this to a relative cap (% of mid) — the bucket field added
    in Phase 4.1 (`quote_quality_bucket`) classifies regardless of the
    underlying calibration so today's hard 0.0 floor stays legible.
    """
    if cap <= 0:
        return 0.5
    spreads: list[float] = []
    for q in (short, long_):
        if q.bid_ask_spread is None:
            return 0.0
        spreads.append(q.bid_ask_spread)
    worst = max(spreads)
    return max(0.0, min(1.0, 1.0 - (worst / cap)))


def _worst_leg_bid_ask_metrics(
    short_q: OptionQuote, long_q: OptionQuote,
) -> tuple[float | None, float | None]:
    """Return (worst_abs, worst_pct_of_mid) for the WIDER of the two legs.

    Useful for the Phase 4.1 quote_quality_bucket — the strategy already
    rejects on the worst leg, so observability surfaces that same leg.
    Returns (None, None) when either leg's bid_ask is unknown.
    """
    pairs: list[tuple[OptionQuote, float]] = []
    for q in (short_q, long_q):
        ba = q.bid_ask_spread
        if ba is None:
            return None, None
        pairs.append((q, ba))
    worst_q, worst_abs = max(pairs, key=lambda p: p[1])
    mid = _safe_mid(worst_q)
    if mid is None or mid <= 0:
        return worst_abs, None
    return worst_abs, worst_abs / mid


def _stamp_spread_meta(
    meta: dict[str, object],
    short_q: OptionQuote,
    long_q: OptionQuote,
    spread: SpreadQuote,
) -> None:
    """Phase 4.1 — surface spread bid/ask/mid + width metrics as top-level meta.

    Keeps the existing `spread_quote` sub-dict for back-compat. New top-level
    keys are consumed by readiness / CSV / Streamlit; same numbers, easier
    to grep.
    """
    meta["spread_bid"]   = spread.credit_bid
    meta["spread_ask"]   = spread.credit_ask
    meta["spread_mid"]   = spread.credit_mid
    meta["spread_width"] = spread.width
    worst_abs, worst_pct = _worst_leg_bid_ask_metrics(short_q, long_q)
    meta["worst_leg_bid_ask_abs"]         = worst_abs
    meta["worst_leg_bid_ask_pct_of_mid"]  = worst_pct
    # spread_width_pct_of_mid — width over spread mid (credit_mid). Useful
    # for the Phase 5 selector that may want to compare across spread sizes.
    if (
        spread.credit_mid is not None
        and spread.credit_mid > 0
        and worst_abs is not None
    ):
        meta["spread_width_pct_of_mid"] = worst_abs / spread.credit_mid
    else:
        meta["spread_width_pct_of_mid"] = None


def build_put_ceiling_call_credit(
    structure: StructureSnapshot,
    chain: OptionChainSnapshot,
    threshold: float,
    spread_width: float,
    strategy_id: str,
    max_bid_ask_width: float | None = None,
) -> Candidate | None:
    """SELL Call@K / BUY Call@(K + width) where K = StructureProvider's PUT_CEILING."""
    short_k, anchor_source, structure_volume = _ceiling_for_threshold(structure, threshold)
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

    # PUT_CEILING is defined by PUT volume at K. Prefer the volume the
    # StructureProvider actually used to qualify the level; fall back to
    # the QuoteProvider's put-side quote only when structure didn't carry it.
    if structure_volume is not None:
        anchor_volume = structure_volume
        anchor_volume_source = "zs_exposure_series"
    else:
        anchor_put = chain.find(short_k, OptionType.PUT)
        anchor_volume = anchor_put.volume if anchor_put else None
        anchor_volume_source = (
            "quote_provider_fallback" if anchor_volume is not None else None
        )

    spread = SpreadQuote.from_legs(short_q, long_q)
    credit = short_mid - long_mid
    max_risk = max(0.0, spread_width - credit)
    rr = (credit / max_risk) if max_risk > 0 else 0.0
    breakeven = short_k + credit

    meta: dict[str, object] = {
        "construction":         "PUT_CEILING_CALL_CREDIT",
        "anchor_source":        anchor_source,         # e.g. "put_ceiling_2k"
        "anchor_volume":        anchor_volume,
        "anchor_volume_source": anchor_volume_source,
        "short_leg":            _quote_dict(short_q),
        "long_leg":             _quote_dict(long_q),
        "spread_quote":         _spread_dict(spread),
        "bid_ask_quality":      _bid_ask_quality_score(
            short_q, long_q, max_bid_ask_width or 0.20,
        ),
        "threshold":            threshold,
    }
    _stamp_spread_meta(meta, short_q, long_q, spread)

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
        meta=meta,
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
    short_k, anchor_source, structure_volume = _floor_for_threshold(structure, threshold)
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

    # CALL_FLOOR is defined by CALL volume at K. Prefer the structure-
    # reported volume; fall back to the QuoteProvider's call-side quote
    # only when structure didn't carry it.
    if structure_volume is not None:
        anchor_volume = structure_volume
        anchor_volume_source = "zs_exposure_series"
    else:
        anchor_call = chain.find(short_k, OptionType.CALL)
        anchor_volume = anchor_call.volume if anchor_call else None
        anchor_volume_source = (
            "quote_provider_fallback" if anchor_volume is not None else None
        )

    spread = SpreadQuote.from_legs(short_q, long_q)
    credit = short_mid - long_mid
    max_risk = max(0.0, spread_width - credit)
    rr = (credit / max_risk) if max_risk > 0 else 0.0
    breakeven = short_k - credit

    meta: dict[str, object] = {
        "construction":         "CALL_FLOOR_PUT_CREDIT",
        "anchor_source":        anchor_source,         # e.g. "call_floor_2k"
        "anchor_volume":        anchor_volume,
        "anchor_volume_source": anchor_volume_source,
        "short_leg":            _quote_dict(short_q),
        "long_leg":             _quote_dict(long_q),
        "spread_quote":         _spread_dict(spread),
        "bid_ask_quality":      _bid_ask_quality_score(
            short_q, long_q, max_bid_ask_width or 0.20,
        ),
        "threshold":            threshold,
    }
    _stamp_spread_meta(meta, short_q, long_q, spread)

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
        meta=meta,
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
        # Phase 4 — broker-provider validation result (None = not validated)
        "validation_passed":           q.validation_passed,
        "validation_rejection_reason": q.validation_rejection_reason,
        "quote_time": q.quote_time.isoformat() if q.quote_time else None,
    }


def _spread_dict(s: SpreadQuote) -> dict:
    return {
        "width": s.width,
        "credit_mid": s.credit_mid,
        "credit_bid": s.credit_bid,
        "credit_ask": s.credit_ask,
    }
