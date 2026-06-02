"""Phase 9B — local paper-trade lifecycle engine (pure, deterministic).

Turns SELECTED scanner signals into local :class:`PaperTrade` records, re-prices
open spreads from later scanner ticks, and applies TP / SL / EOD exit rules.

LOCAL PAPER ACCOUNTING ONLY — there is NO brokerage here. Nothing in this module
places, previews, submits, or routes an order. P&L math is REUSED from
``src.paper.manual_tracker`` (one source of truth, never re-derived).

Re-pricing convention (credit spread → debit to close):
  spread_bid = short_bid - long_ask     (worst case to close)
  spread_ask = short_ask - long_bid
  spread_mid = short_mid - long_mid      (the mark; falls back to (bid+ask)/2)
The MARK used for unrealized P&L and TP/SL is ``spread_mid`` (the current debit).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from src.paper.manual_tracker import (
    OPTION_MULTIPLIER,
    realized_pnl_dollars,
    spread_width_from_strikes,
    unrealized_pnl_dollars,
)
from src.paper.models import PaperLifecycleConfig, PaperTrade
from src.utils.time import parse_hhmm

# ── small numeric parsing (CSV rows arrive as strings) ───────────────────────

def _f(row: dict[str, Any], key: str) -> float | None:
    v = row.get(key)
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── identity (mirrors run_forward._signal_identity ordering) ─────────────────

def make_trade_identity(
    *, profile_hash: str | None, symbol: str | None, selected_expiry: Any,
    side: Any, short_strike: Any, long_strike: Any, target_dte: Any, trade_date: str,
) -> str:
    return "|".join(str(x) for x in (
        profile_hash, symbol or "", selected_expiry, side,
        short_strike, long_strike, target_dte, trade_date,
    ))


def _paper_trade_id(identity: str) -> str:
    return "pt_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]


# ── re-pricing a spread from a scanner row ───────────────────────────────────

def spread_quote_from_row(row: dict[str, Any]) -> dict[str, float | None]:
    """Compute the current spread bid/ask/mid (debit to close) from a
    ranked_candidates.csv row's per-leg quotes. Returns a dict with bid/ask/mid;
    any value is None when the underlying legs lack a usable quote.

    ``mid`` is the mark. ``available`` is True iff a mark could be computed."""
    sb, sa, sm = _f(row, "short_bid"), _f(row, "short_ask"), _f(row, "short_mid")
    lb, la, lm = _f(row, "long_bid"), _f(row, "long_ask"), _f(row, "long_mid")

    bid = round(sb - la, 4) if (sb is not None and la is not None) else None
    ask = round(sa - lb, 4) if (sa is not None and lb is not None) else None
    mid = round(sm - lm, 4) if (sm is not None and lm is not None) else None
    if mid is None and bid is not None and ask is not None:
        mid = round((bid + ask) / 2.0, 4)

    return {"bid": bid, "ask": ask, "mid": mid, "available": mid is not None}


def find_repricing_row(
    trade: PaperTrade, all_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the scanner row matching an open trade by
    (side, short_strike, long_strike, selected_expiry). Returns None if absent."""
    for row in all_rows:
        if (
            str(row.get("side")) == str(trade.side)
            and _f(row, "short_strike") == trade.short_strike
            and _f(row, "long_strike") == trade.long_strike
            and str(row.get("selected_expiry")) == str(trade.selected_expiry)
        ):
            return row
    return None


# ── open / dup-limit gating ──────────────────────────────────────────────────

def can_open(
    *, identity: str, side: Any, short_strike: float | None, long_strike: float | None,
    selected_expiry: Any, profile_id: str, open_trades: list[PaperTrade],
    config: PaperLifecycleConfig,
) -> tuple[bool, str | None, bool]:
    """Decide whether a new paper trade may open.

    Returns ``(allowed, block_reason, is_duplicate)``. First match wins, in this
    documented order:
      1. duplicate identity (already open)        -> is_duplicate=True
      2. duplicate strikes disallowed             -> blocked_by_limits
      3. multiple-open-per-profile disabled       -> blocked_by_limits
      4. per-profile max open reached             -> blocked_by_limits
      5. total max open reached                   -> blocked_by_limits
    """
    # 1. exact identity already open → duplicate signal
    if any(t.trade_identity == identity for t in open_trades):
        return False, None, True

    # 2. duplicate strikes (same side+strikes+expiry already open, any profile)
    if not config.allow_duplicate_strikes:
        for t in open_trades:
            if (
                str(t.side) == str(side)
                and t.short_strike == short_strike
                and t.long_strike == long_strike
                and str(t.selected_expiry) == str(selected_expiry)
            ):
                return False, "duplicate_strikes_disallowed", False

    open_for_profile = sum(1 for t in open_trades if t.profile_id == profile_id)

    # 3. multiple open per profile disabled
    if not config.allow_multiple_open_per_profile and open_for_profile >= 1:
        return False, "multiple_open_per_profile_disabled", False

    # 4. per-profile cap
    if open_for_profile >= config.max_open_trades_per_profile:
        return False, "per_profile_max_open_reached", False

    # 5. total cap
    if len(open_trades) >= config.max_open_trades_total:
        return False, "total_max_open_reached", False

    return True, None, False


def open_trade_from_signal(
    row: dict[str, Any], *, run_id: str, profile_id: str, profile_hash: str,
    strategy_id: str, symbol: str, target_dte: int | None,
    config: PaperLifecycleConfig, now_iso: str, trade_date: str,
) -> PaperTrade:
    """Build an OPEN PaperTrade from a selected scanner row (no order is placed)."""
    side = row.get("side")
    short_strike = _f(row, "short_strike")
    long_strike = _f(row, "long_strike")
    selected_expiry = row.get("selected_expiry") or row.get("expiry")
    entry_credit = _f(row, "credit")
    contracts = int(config.contracts)

    spread_width = (
        spread_width_from_strikes(short_strike, long_strike)
        if short_strike is not None and long_strike is not None else None
    )
    q = spread_quote_from_row(row)

    max_profit = (
        round(entry_credit * OPTION_MULTIPLIER * contracts, 2)
        if entry_credit is not None else None
    )
    max_loss = None
    if entry_credit is not None and spread_width is not None:
        max_loss = round(max(spread_width - entry_credit, 0.0) * OPTION_MULTIPLIER * contracts, 2)

    identity = make_trade_identity(
        profile_hash=profile_hash, symbol=symbol, selected_expiry=selected_expiry,
        side=side, short_strike=short_strike, long_strike=long_strike,
        target_dte=target_dte, trade_date=trade_date,
    )

    # entry mark = current mid (debit). Unrealized starts at ~0 (mark ≈ credit).
    entry_mid = q["mid"]
    unrealized = (
        round(unrealized_pnl_dollars(entry_credit, entry_mid, contracts), 2)
        if entry_credit is not None and entry_mid is not None else None
    )

    return PaperTrade(
        paper_trade_id=_paper_trade_id(identity),
        run_id=run_id,
        profile_id=profile_id,
        profile_hash=profile_hash,
        strategy_id=strategy_id,
        symbol=symbol,
        side=str(side) if side is not None else "",
        selected_expiry=str(selected_expiry) if selected_expiry is not None else None,
        target_dte=target_dte,
        opened_at=now_iso,
        closed_at=None,
        status="open",
        short_strike=short_strike,
        long_strike=long_strike,
        spread_width=round(spread_width, 4) if spread_width is not None else None,
        contracts=contracts,
        entry_credit=round(entry_credit, 4) if entry_credit is not None else None,
        entry_bid=q["bid"],
        entry_ask=q["ask"],
        entry_mid=entry_mid,
        entry_quote_timestamp=row.get("quote_timestamp"),
        current_mark=entry_mid,
        current_bid=q["bid"],
        current_ask=q["ask"],
        unrealized_pnl=unrealized,
        realized_pnl=None,
        max_profit=max_profit,
        max_loss=max_loss,
        planned_stop_risk_dollars=_f(row, "planned_stop_risk_dollars"),
        theoretical_max_loss_dollars=_f(row, "theoretical_max_loss_dollars"),
        tp_rule=config.tp_rule_str(),
        sl_rule=config.sl_rule_str(),
        exit_rule=config.exit_rule_str(),
        exit_reason=None,
        exit_credit_or_debit=None,
        mae=unrealized if unrealized is not None else 0.0,
        mfe=unrealized if unrealized is not None else 0.0,
        ticks_held=0,
        notes=None,
        trade_identity=identity,
    )


# ── per-tick update + exit evaluation ────────────────────────────────────────

def update_trade_mark(
    trade: PaperTrade, spread_quote: dict[str, float | None] | None, now_iso: str,
) -> str:
    """Re-price an open trade. Returns ``"update"`` if a fresh mark was applied,
    or ``"quote_unavailable"`` if no current mark could be computed (the trade's
    last-known mark/P&L are preserved). Always increments ``ticks_held``."""
    trade.ticks_held += 1
    if not spread_quote or not spread_quote.get("available"):
        return "quote_unavailable"

    mark = spread_quote["mid"]
    trade.current_mark = mark
    trade.current_bid = spread_quote.get("bid")
    trade.current_ask = spread_quote.get("ask")
    if trade.entry_credit is not None and mark is not None:
        unrl = round(unrealized_pnl_dollars(trade.entry_credit, mark, trade.contracts), 2)
        trade.unrealized_pnl = unrl
        trade.mfe = unrl if trade.mfe is None else max(trade.mfe, unrl)
        trade.mae = unrl if trade.mae is None else min(trade.mae, unrl)
    return "update"


def evaluate_exit(
    trade: PaperTrade, config: PaperLifecycleConfig, now_et_dt: datetime,
) -> tuple[str | None, float | None]:
    """Return ``(exit_reason, exit_debit)`` or ``(None, None)`` to hold.

    TP/SL fire only when a current mark exists. EOD fires regardless of quote
    availability (closing at the last-known mark, or entry_credit if never
    priced). TP is checked before SL before EOD."""
    debit = trade.current_mark
    credit = trade.entry_credit

    if debit is not None and credit is not None:
        if debit <= credit * config.take_profit_pct:
            return "take_profit", debit
        if debit >= credit * config.stop_loss_pct:
            return "stop_loss", debit

    if config.exit_on_eod:
        tod = now_et_dt.timetz().replace(tzinfo=None)
        if tod >= parse_hhmm(config.eod_exit_time):
            close_debit = debit if debit is not None else credit
            return "eod_exit", close_debit

    return None, None


def close_trade(
    trade: PaperTrade, *, exit_reason: str, exit_debit: float | None, now_iso: str,
) -> PaperTrade:
    """Close an open trade at ``exit_debit`` and finalize realized P&L."""
    trade.status = "closed"
    trade.closed_at = now_iso
    trade.exit_reason = exit_reason
    trade.exit_credit_or_debit = round(exit_debit, 4) if exit_debit is not None else None
    if trade.entry_credit is not None and exit_debit is not None:
        trade.realized_pnl = round(
            realized_pnl_dollars(trade.entry_credit, exit_debit, trade.contracts), 2)
        trade.current_mark = exit_debit
        trade.unrealized_pnl = 0.0
    else:
        trade.realized_pnl = None
        trade.notes = (trade.notes or "") + "closed_without_quote;"
    return trade
