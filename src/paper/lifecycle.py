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
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.paper.manual_tracker import (
    OPTION_MULTIPLIER,
    realized_pnl_dollars,
    spread_width_from_strikes,
    unrealized_pnl_dollars,
)
from src.paper.models import PaperLifecycleConfig, PaperMark, PaperTrade, PaperTradeTicket
from src.regime.types import RegimeLabel, RegimeSnapshot
from src.strategy_engine.candidates import build_credit_spread
from src.strategy_engine.risk_quality import evaluate_risk_quality
from src.strategy_engine.types import (
    LegAction,
    OptionRight,
    StrategyArchetype,
    StrategyCandidate,
    StrategyLeg,
)
from src.utils.time import parse_hhmm


@dataclass(frozen=True)
class ExitDecision:
    decision: str
    exit_reason: str | None
    exit_mark: float | None
    reason_codes: tuple[str, ...]
    explanation: str
    checks: dict[str, bool]

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


def _ticket_id(identity: str) -> str:
    return "ticket_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]


def _leg_row(leg: Any) -> dict[str, Any]:
    return {
        "option_symbol": leg.option_symbol,
        "strike": float(leg.strike),
        "right": str(leg.right),
        "action": str(leg.action),
        "bid": leg.bid,
        "ask": leg.ask,
        "mid": leg.mid,
        "quantity": int(leg.quantity),
    }


def _assessment_label(assessment: Any) -> str:
    label = getattr(assessment, "label", assessment)
    return str(getattr(label, "value", label) or "UNASSESSED")


def create_paper_ticket(
    candidate: StrategyCandidate,
    *,
    profile_id: str,
    profile_hash: str,
    risk_quality: Any = "UNASSESSED",
    regime_snapshot: RegimeSnapshot | None = None,
    reason_codes: tuple[str, ...] = (),
    slippage_points: float = 0.0,
    target_mark: float | None = None,
    stop_mark: float | None = None,
) -> PaperTradeTicket:
    """Create a local-only ticket using current candidate leg mids."""
    legs = tuple(_leg_row(leg) for leg in candidate.legs)
    slip = max(0.0, float(slippage_points))
    entry_credit: float | None = None
    entry_debit: float | None = None
    if candidate.is_credit_spread:
        short = next((leg for leg in candidate.legs if str(leg.action) == "SELL"), None)
        long = next((leg for leg in candidate.legs if str(leg.action) == "BUY"), None)
        if short is None or long is None or short.mid is None or long.mid is None:
            raise ValueError("credit-spread local paper ticket requires both leg mids")
        entry_credit = round(max(0.0, short.mid - long.mid - slip), 4)
    elif candidate.is_long_premium:
        leg = candidate.legs[0] if candidate.legs else None
        if leg is None or leg.mid is None:
            raise ValueError("long-premium local paper ticket requires a leg mid")
        entry_debit = round(max(0.0, leg.mid + slip), 4)
    else:
        raise ValueError(f"unsupported local paper archetype: {candidate.archetype}")

    contracts = max(1, int(candidate.contracts))
    width = candidate.width or 0.0
    max_profit = candidate.max_profit
    max_loss = candidate.max_loss
    if entry_credit is not None:
        max_profit = round(entry_credit * OPTION_MULTIPLIER * contracts, 2)
        max_loss = round(max(0.0, width - entry_credit) * OPTION_MULTIPLIER * contracts, 2)
    elif entry_debit is not None:
        max_loss = round(entry_debit * OPTION_MULTIPLIER * contracts, 2)
    risk_reward = (
        round(max_profit / max_loss, 4)
        if max_profit is not None and max_loss not in {None, 0}
        else candidate.risk_reward
    )
    if target_mark is None and entry_debit is not None and candidate.minimum_target_multiple:
        target_mark = round(entry_debit * candidate.minimum_target_multiple, 4)
    identity = (
        f"{profile_hash}|{candidate.candidate_id}|{candidate.symbol}|"
        f"{candidate.expiry}|{candidate.timestamp.isoformat()}"
    )
    assessment_reasons = tuple(getattr(risk_quality, "reason_codes", ()) or ())
    combined_reasons = tuple(
        dict.fromkeys(
            (*candidate.reason_codes, *assessment_reasons, *reason_codes, "local_chain_mid_fill")
        )
    )
    return PaperTradeTicket(
        ticket_id=_ticket_id(identity),
        source_candidate_id=candidate.candidate_id,
        profile_id=profile_id,
        profile_hash=profile_hash,
        symbol=candidate.symbol,
        archetype=candidate.archetype.value,
        contracts=contracts,
        dte=int(candidate.dte),
        expiry=candidate.expiry,
        legs=legs,
        entry_credit=entry_credit,
        entry_debit=entry_debit,
        max_profit=max_profit,
        max_loss=max_loss,
        risk_reward=risk_reward,
        risk_quality_label=_assessment_label(risk_quality),
        regime_snapshot_at_entry=(regime_snapshot.to_dict() if regime_snapshot else None),
        entry_reason_codes=combined_reasons,
        plain_english_thesis=candidate.thesis or "Accepted local paper research candidate.",
        target_mark=target_mark,
        stop_mark=stop_mark,
        invalidation_level=candidate.invalidation_level,
    )


def open_trade_from_ticket(
    ticket: PaperTradeTicket,
    *,
    run_id: str,
    strategy_id: str,
    now_iso: str,
    trade_identity: str | None = None,
    config: PaperLifecycleConfig | None = None,
) -> PaperTrade:
    """Convert a local-only ticket into the existing durable PaperTrade record."""
    config = config or PaperLifecycleConfig()
    short_leg = next((leg for leg in ticket.legs if leg.get("action") == "SELL"), None)
    long_leg = next((leg for leg in ticket.legs if leg.get("action") == "BUY"), None)
    side = {
        StrategyArchetype.CALL_CREDIT_SPREAD.value: "CALL_CREDIT",
        StrategyArchetype.PUT_CREDIT_SPREAD.value: "PUT_CREDIT",
        StrategyArchetype.LONG_CALL.value: "LONG_CALL",
        StrategyArchetype.LONG_PUT.value: "LONG_PUT",
    }.get(ticket.archetype, ticket.archetype)
    identity = trade_identity or (
        f"{ticket.profile_hash}|{ticket.source_candidate_id}|{ticket.symbol}|{ticket.expiry}"
    )
    entry_mark = ticket.entry_credit if ticket.entry_credit is not None else ticket.entry_debit
    width = (
        abs(float(long_leg["strike"]) - float(short_leg["strike"]))
        if short_leg is not None and long_leg is not None
        else None
    )
    regime_json = (
        json.dumps(ticket.regime_snapshot_at_entry, sort_keys=True)
        if ticket.regime_snapshot_at_entry is not None
        else None
    )
    return PaperTrade(
        paper_trade_id=_paper_trade_id(identity),
        run_id=run_id,
        profile_id=ticket.profile_id,
        profile_hash=ticket.profile_hash,
        strategy_id=strategy_id,
        symbol=ticket.symbol,
        side=side,
        selected_expiry=ticket.expiry,
        target_dte=ticket.dte,
        opened_at=now_iso,
        status="open",
        short_strike=float(short_leg["strike"]) if short_leg is not None else None,
        long_strike=float(long_leg["strike"]) if long_leg is not None else None,
        spread_width=width,
        contracts=ticket.contracts,
        entry_credit=ticket.entry_credit,
        entry_debit=ticket.entry_debit,
        entry_mid=entry_mark,
        entry_quote_timestamp=now_iso,
        current_mark=entry_mark,
        current_quote_timestamp=now_iso,
        unrealized_pnl=0.0,
        max_profit=ticket.max_profit,
        max_loss=ticket.max_loss,
        risk_reward=ticket.risk_reward,
        tp_rule=config.tp_rule_str(),
        sl_rule=config.sl_rule_str(),
        exit_rule=config.exit_rule_str(),
        mae=0.0,
        mfe=0.0,
        trade_identity=identity,
        source_candidate_id=ticket.source_candidate_id,
        archetype=ticket.archetype,
        legs_json=json.dumps(list(ticket.legs), sort_keys=True),
        entry_price_type="credit" if ticket.entry_credit is not None else "debit",
        risk_quality_label=ticket.risk_quality_label,
        entry_regime_json=regime_json,
        current_regime_json=regime_json,
        entry_reason_codes="; ".join(ticket.entry_reason_codes),
        latest_reason_codes="local_chain_mid_fill",
        thesis=ticket.plain_english_thesis,
        target_mark=ticket.target_mark,
        stop_mark=ticket.stop_mark,
        invalidation_level=ticket.invalidation_level,
        latest_decision="HOLD",
        latest_explanation="Local paper ticket entered from current chain mids.",
        local_paper_only=True,
        no_broker_order_sent=True,
    )


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


def create_paper_ticket_from_signal(
    row: dict[str, Any],
    *,
    profile_id: str,
    profile_hash: str,
    symbol: str,
    target_dte: int | None,
    config: PaperLifecycleConfig,
    now_iso: str,
) -> PaperTradeTicket:
    """Adapt an accepted legacy scanner row into the shared ticket contract."""
    q = spread_quote_from_row(row)
    if not q["available"] or q["mid"] is None:
        raise ValueError("selected signal lacks usable chain mids")
    side = str(row.get("side") or "")
    if side not in {"CALL_CREDIT", "PUT_CREDIT"}:
        raise ValueError(f"unsupported scanner side: {side}")
    short_strike = _f(row, "short_strike")
    long_strike = _f(row, "long_strike")
    if short_strike is None or long_strike is None:
        raise ValueError("selected signal lacks spread strikes")
    expiry = str(row.get("selected_expiry") or row.get("expiry") or "")
    right = OptionRight.CALL if side == "CALL_CREDIT" else OptionRight.PUT
    archetype = (
        StrategyArchetype.CALL_CREDIT_SPREAD
        if side == "CALL_CREDIT"
        else StrategyArchetype.PUT_CREDIT_SPREAD
    )
    short_leg = StrategyLeg(
        option_symbol=f"{symbol}:{expiry}:{short_strike:g}:{right.value}",
        strike=short_strike,
        right=right,
        action=LegAction.SELL,
        bid=_f(row, "short_bid"),
        ask=_f(row, "short_ask"),
        mid=_f(row, "short_mid"),
    )
    long_leg = StrategyLeg(
        option_symbol=f"{symbol}:{expiry}:{long_strike:g}:{right.value}",
        strike=long_strike,
        right=right,
        action=LegAction.BUY,
        bid=_f(row, "long_bid"),
        ask=_f(row, "long_ask"),
        mid=_f(row, "long_mid"),
    )
    candidate = build_credit_spread(
        timestamp=datetime.fromisoformat(now_iso),
        symbol=symbol,
        dte=int(target_dte or 0),
        expiry=expiry,
        archetype=archetype,
        short_leg=short_leg,
        long_leg=long_leg,
        credit=float(q["mid"]),
        contracts=config.contracts,
        distance_to_short_strike=_f(row, "distance_from_spot"),
        regime_label=str(row.get("regime_label") or "") or None,
        quote_quality=str(row.get("quote_quality_bucket") or "unknown"),
        thesis=str(row.get("selector_reason") or "Accepted scanner selection."),
    )
    regime_snapshot = None
    raw_regime = row.get("regime_snapshot_json")
    if raw_regime:
        try:
            regime_snapshot = RegimeSnapshot.from_dict(json.loads(str(raw_regime)))
        except (TypeError, ValueError, KeyError):
            regime_snapshot = None
    risk_assessment = evaluate_risk_quality(candidate)
    return create_paper_ticket(
        candidate,
        profile_id=profile_id,
        profile_hash=profile_hash,
        risk_quality=risk_assessment,
        regime_snapshot=regime_snapshot,
        reason_codes=("selected_signal",),
        slippage_points=config.slippage_points,
        target_mark=round(float(q["mid"]) * config.take_profit_pct, 4),
        stop_mark=round(float(q["mid"]) * config.stop_loss_pct, 4),
    )


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

    if q["available"]:
        ticket = create_paper_ticket_from_signal(
            row,
            profile_id=profile_id,
            profile_hash=profile_hash,
            symbol=symbol,
            target_dte=target_dte,
            config=config,
            now_iso=now_iso,
        )
        trade = open_trade_from_ticket(
            ticket,
            run_id=run_id,
            strategy_id=strategy_id,
            now_iso=now_iso,
            trade_identity=identity,
            config=config,
        )
        trade.entry_bid = q["bid"]
        trade.entry_ask = q["ask"]
        trade.entry_quote_timestamp = row.get("quote_timestamp") or now_iso
        trade.current_bid = q["bid"]
        trade.current_ask = q["ask"]
        trade.current_quote_timestamp = row.get("quote_timestamp") or now_iso
        trade.planned_stop_risk_dollars = _f(row, "planned_stop_risk_dollars")
        trade.theoretical_max_loss_dollars = _f(row, "theoretical_max_loss_dollars")
        trade.distance_to_short_strike = _f(row, "distance_from_spot")
        return trade

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
        source_candidate_id=_ticket_id(identity),
        archetype=(
            StrategyArchetype.CALL_CREDIT_SPREAD.value
            if side == "CALL_CREDIT"
            else StrategyArchetype.PUT_CREDIT_SPREAD.value
        ),
        entry_price_type="credit",
        risk_quality_label="QUOTE_UNAVAILABLE_LEGACY_RECORD",
        entry_reason_codes="selected_signal; quote_unavailable_at_entry",
        latest_reason_codes="quote_unavailable_at_entry",
        thesis=str(row.get("selector_reason") or "Legacy local paper scanner record."),
        missing_quote_marks=1,
        latest_decision="ALERT_ONLY",
        latest_explanation="Selected signal was recorded without a usable chain mark.",
        local_paper_only=True,
        no_broker_order_sent=True,
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
        trade.missing_quote_marks += 1
        trade.latest_decision = "ALERT_ONLY"
        trade.latest_reason_codes = "mark_quote_unavailable"
        trade.latest_explanation = "Current chain quotes could not reprice the paper position."
        return "quote_unavailable"

    mark = spread_quote["mid"]
    trade.missing_quote_marks = 0
    trade.current_mark = mark
    trade.current_bid = spread_quote.get("bid")
    trade.current_ask = spread_quote.get("ask")
    trade.current_quote_timestamp = now_iso
    if trade.entry_credit is not None and mark is not None:
        unrl = round(unrealized_pnl_dollars(trade.entry_credit, mark, trade.contracts), 2)
        trade.unrealized_pnl = unrl
        if trade.entry_credit > 0:
            trade.credit_kept_pct = round(
                ((trade.entry_credit - mark) / trade.entry_credit) * 100.0, 2
            )
        trade.mfe = unrl if trade.mfe is None else max(trade.mfe, unrl)
        trade.mae = unrl if trade.mae is None else min(trade.mae, unrl)
    elif trade.entry_debit is not None and mark is not None:
        unrl = round((mark - trade.entry_debit) * OPTION_MULTIPLIER * trade.contracts, 2)
        trade.unrealized_pnl = unrl
        trade.mfe = unrl if trade.mfe is None else max(trade.mfe, unrl)
        trade.mae = unrl if trade.mae is None else min(trade.mae, unrl)
    spot = spread_quote.get("spot")
    if spot is not None and trade.short_strike is not None:
        trade.distance_to_short_strike = round(abs(float(spot) - trade.short_strike), 4)
    trade.latest_decision = "HOLD"
    trade.latest_reason_codes = "mark_updated_from_chain_mid"
    trade.latest_explanation = "Paper position repriced from current chain mids."
    return "update"


def quote_mark_from_legs(
    trade: PaperTrade,
    leg_quotes: dict[str, dict[str, Any]],
    *,
    slippage_points: float = 0.0,
) -> dict[str, Any]:
    """Compute a current mark from exact option-symbol mids only."""
    try:
        legs = json.loads(trade.legs_json or "[]")
    except (TypeError, ValueError):
        legs = []
    resolved: list[dict[str, Any]] = []
    for leg in legs:
        symbol = str(leg.get("option_symbol") or "")
        quote = leg_quotes.get(symbol) or {}
        mid = _f(quote, "mid")
        resolved.append({**leg, "bid": _f(quote, "bid"), "ask": _f(quote, "ask"), "mid": mid})
    slip = max(0.0, float(slippage_points))
    if trade.entry_price_type == "credit":
        short = next((leg for leg in resolved if leg.get("action") == "SELL"), None)
        long = next((leg for leg in resolved if leg.get("action") == "BUY"), None)
        if short is None or long is None or short.get("mid") is None or long.get("mid") is None:
            return {"available": False, "mid": None, "legs": resolved}
        mark = max(0.0, float(short["mid"]) - float(long["mid"]) + slip)
    else:
        long = next((leg for leg in resolved if leg.get("action") == "BUY"), None)
        if long is None or long.get("mid") is None:
            return {"available": False, "mid": None, "legs": resolved}
        mark = max(0.0, float(long["mid"]) - slip)
    return {"available": True, "mid": round(mark, 4), "legs": resolved}


def _snapshot_from_json(value: str | None) -> RegimeSnapshot | None:
    if not value:
        return None
    try:
        return RegimeSnapshot.from_dict(json.loads(value))
    except (TypeError, ValueError, KeyError):
        return None


def _acceleration_direction(snapshot: RegimeSnapshot) -> str | None:
    if snapshot.final_regime_label != RegimeLabel.ACCELERATION:
        return None
    if (
        snapshot.spot is not None
        and snapshot.put_wing_10k is not None
        and snapshot.spot >= snapshot.put_wing_10k
    ):
        return "UPSIDE"
    if (
        snapshot.spot is not None
        and snapshot.call_wing_10k is not None
        and snapshot.spot <= snapshot.call_wing_10k
    ):
        return "DOWNSIDE"
    if snapshot.maxvol_migration is not None:
        if snapshot.maxvol_migration > 0:
            return "UPSIDE"
        if snapshot.maxvol_migration < 0:
            return "DOWNSIDE"
    return None


def evaluate_entry_regime_gate(
    archetype: str,
    snapshot: RegimeSnapshot | None,
) -> ExitDecision:
    checks = {"quote_usable": True, "hostile_acceleration": False}
    if snapshot is None:
        return ExitDecision(
            "HOLD", None, None, ("regime_snapshot_unavailable",),
            "Regime snapshot is unavailable; local paper entry remains observation-only.", checks,
        )
    if snapshot.quote_quality_status in {"unusable", "invalid", "rejected", "stale"}:
        checks["quote_usable"] = False
        return ExitDecision(
            "BLOCK_NEW_TRADES", None, None, ("entry_quote_quality_unusable",),
            "New local paper entries are blocked because current quotes are unusable.", checks,
        )
    direction = _acceleration_direction(snapshot)
    hostile = (
        (archetype == StrategyArchetype.CALL_CREDIT_SPREAD.value and direction == "UPSIDE")
        or (archetype == StrategyArchetype.PUT_CREDIT_SPREAD.value and direction == "DOWNSIDE")
        or (archetype == StrategyArchetype.LONG_CALL.value and direction == "DOWNSIDE")
        or (archetype == StrategyArchetype.LONG_PUT.value and direction == "UPSIDE")
    )
    checks["hostile_acceleration"] = hostile
    if hostile:
        return ExitDecision(
            "BLOCK_NEW_TRADES", None, None, ("entry_regime_hostile_acceleration",),
            "New local paper entry is blocked because acceleration conflicts with its thesis.",
            checks,
        )
    return ExitDecision(
        "HOLD", None, None, ("entry_regime_not_hostile",),
        "Regime does not provide a mandatory local paper entry block.", checks,
    )


def _regime_exit_decision(
    trade: PaperTrade,
    current: RegimeSnapshot | None,
) -> ExitDecision | None:
    if current is None:
        return None
    entry = _snapshot_from_json(trade.entry_regime_json)
    direction = _acceleration_direction(current)
    archetype = trade.archetype
    hostile = (
        (archetype == StrategyArchetype.CALL_CREDIT_SPREAD.value and direction == "UPSIDE")
        or (archetype == StrategyArchetype.PUT_CREDIT_SPREAD.value and direction == "DOWNSIDE")
        or (archetype == StrategyArchetype.LONG_CALL.value and direction == "DOWNSIDE")
        or (archetype == StrategyArchetype.LONG_PUT.value and direction == "UPSIDE")
    )
    supportive = (
        (archetype == StrategyArchetype.LONG_CALL.value and direction == "UPSIDE")
        or (archetype == StrategyArchetype.LONG_PUT.value and direction == "DOWNSIDE")
    )
    checks = {
        "regime_hostile": hostile,
        "regime_supportive": supportive,
        "corridor_broke": bool(entry and entry.corridor_valid is True and current.corridor_valid is False),
        "gamma_flip_against_thesis": False,
        "maxvol_migrated_against_thesis": False,
        "wds_collapsed": bool(
            entry
            and entry.wds_tier in {1, 2}
            and current.wds_tier not in {1, 2}
        ),
    }
    if entry and entry.distance_to_gamma_flip is not None and current.distance_to_gamma_flip is not None:
        crossed = entry.distance_to_gamma_flip * current.distance_to_gamma_flip < 0
        checks["gamma_flip_against_thesis"] = bool(
            crossed
            and (
                (archetype in {StrategyArchetype.CALL_CREDIT_SPREAD.value, StrategyArchetype.LONG_PUT.value}
                 and current.distance_to_gamma_flip > 0)
                or (archetype in {StrategyArchetype.PUT_CREDIT_SPREAD.value, StrategyArchetype.LONG_CALL.value}
                    and current.distance_to_gamma_flip < 0)
            )
        )
    migration = current.maxvol_migration
    checks["maxvol_migrated_against_thesis"] = bool(
        migration is not None
        and (
            (archetype in {StrategyArchetype.CALL_CREDIT_SPREAD.value, StrategyArchetype.LONG_PUT.value}
             and migration > 0)
            or (archetype in {StrategyArchetype.PUT_CREDIT_SPREAD.value, StrategyArchetype.LONG_CALL.value}
                and migration < 0)
        )
    )
    if hostile or checks["gamma_flip_against_thesis"]:
        reasons = ["regime_hostile_to_entry_thesis"]
        if hostile:
            reasons.append("directional_acceleration_against_position")
        if checks["gamma_flip_against_thesis"]:
            reasons.append("gamma_flip_crossed_against_position")
        return ExitDecision(
            "EXIT", "regime_thesis_invalidated", trade.current_mark,
            tuple(reasons), "Current regime invalidates the position's directional thesis.", checks,
        )
    if (
        current.final_regime_label == RegimeLabel.NO_EDGE
        and (trade.unrealized_pnl or 0.0) < 0
        and trade.entry_price_type == "credit"
    ):
        return ExitDecision(
            "EXIT", "regime_thesis_invalidated", trade.current_mark,
            ("regime_no_edge_with_worsening_credit_risk",),
            "The credit position is worsening while the regime has no usable edge.", checks,
        )
    if supportive:
        return ExitDecision(
            "HOLD", None, None, ("regime_supports_long_premium_thesis",),
            "Directional acceleration supports the long-premium thesis.", checks,
        )
    alerts: list[str] = []
    if checks["corridor_broke"]:
        alerts.append("active_corridor_broke")
    if checks["maxvol_migrated_against_thesis"]:
        alerts.append("maxvol_migrated_against_thesis")
    if checks["wds_collapsed"]:
        alerts.append("wds_collapsed_against_thesis")
    if current.final_regime_label == RegimeLabel.NO_EDGE and (trade.unrealized_pnl or 0.0) < 0:
        alerts.append("regime_no_edge_with_worsening_mark")
    if alerts:
        return ExitDecision(
            "ALERT_ONLY", None, None, tuple(alerts),
            "Structure weakened, but the evidence does not require a mandatory exit.", checks,
        )
    return None


def evaluate_exit_decision(
    trade: PaperTrade,
    config: PaperLifecycleConfig,
    now_et_dt: datetime,
    *,
    regime_snapshot: RegimeSnapshot | None = None,
    quote_status: str | None = None,
) -> ExitDecision:
    """Evaluate hard exits first, then strategy-specific regime/thesis evidence."""
    mark = trade.current_mark
    checks = {
        "quote_invalid": False,
        "quote_failure_limit": False,
        "take_profit": False,
        "stop_loss": False,
        "eod": False,
    }
    normalized_quote = str(quote_status or "").lower()
    if normalized_quote in {"invalid", "stale", "rejected"}:
        checks["quote_invalid"] = True
        return ExitDecision(
            "EXIT", "quote_invalid", mark, ("mark_quote_invalid_or_stale",),
            "Current quote quality is invalid or stale beyond the allowed threshold.", checks,
        )
    if trade.missing_quote_marks >= config.max_missing_quote_marks:
        checks["quote_failure_limit"] = True
        return ExitDecision(
            "EXIT", "quote_failure_limit", mark,
            ("missing_repricing_quotes_limit_reached",),
            "The position exceeded the allowed consecutive missing-mark limit.", checks,
        )

    if trade.entry_credit is not None and mark is not None:
        tp_mark = trade.target_mark
        sl_mark = trade.stop_mark
        tp_hit = mark <= (tp_mark if tp_mark is not None else trade.entry_credit * config.take_profit_pct)
        sl_hit = mark >= (sl_mark if sl_mark is not None else trade.entry_credit * config.stop_loss_pct)
    elif trade.entry_debit is not None and mark is not None:
        tp_hit = trade.target_mark is not None and mark >= trade.target_mark
        sl_hit = trade.stop_mark is not None and mark <= trade.stop_mark
    else:
        tp_hit = sl_hit = False
    if tp_hit:
        checks["take_profit"] = True
        return ExitDecision(
            "EXIT", "take_profit", mark, ("take_profit_threshold_hit",),
            "The configured take-profit threshold was reached.", checks,
        )
    if sl_hit:
        checks["stop_loss"] = True
        return ExitDecision(
            "EXIT", "stop_loss", mark, ("stop_loss_threshold_hit",),
            "The configured stop-loss threshold was reached.", checks,
        )
    if config.exit_on_eod:
        tod = now_et_dt.timetz().replace(tzinfo=None)
        if tod >= parse_hhmm(config.eod_exit_time):
            checks["eod"] = True
            fallback = mark if mark is not None else trade.entry_credit or trade.entry_debit
            return ExitDecision(
                "EXIT", "eod_exit", fallback, ("eod_exit_time_reached",),
                "The configured local-paper EOD exit time was reached.", checks,
            )
    if config.regime_exit_enabled:
        regime_decision = _regime_exit_decision(trade, regime_snapshot)
        if regime_decision is not None:
            return regime_decision
    return ExitDecision(
        "HOLD", None, None, ("no_exit_condition_met",),
        "No mandatory local-paper exit condition was met.", checks,
    )


def evaluate_exit(
    trade: PaperTrade, config: PaperLifecycleConfig, now_et_dt: datetime,
) -> tuple[str | None, float | None]:
    """Return ``(exit_reason, exit_debit)`` or ``(None, None)`` to hold.

    TP/SL fire only when a current mark exists. EOD fires regardless of quote
    availability (closing at the last-known mark, or entry_credit if never
    priced). TP is checked before SL before EOD."""
    decision = evaluate_exit_decision(trade, config, now_et_dt)
    return decision.exit_reason, decision.exit_mark


def close_trade(
    trade: PaperTrade, *, exit_reason: str, exit_debit: float | None, now_iso: str,
    reason_codes: tuple[str, ...] = (), explanation: str | None = None,
) -> PaperTrade:
    """Close an open local-paper trade and finalize realized P&L."""
    trade.status = "closed"
    trade.closed_at = now_iso
    trade.exit_reason = exit_reason
    trade.exit_credit_or_debit = round(exit_debit, 4) if exit_debit is not None else None
    if trade.entry_credit is not None and exit_debit is not None:
        trade.realized_pnl = round(
            realized_pnl_dollars(trade.entry_credit, exit_debit, trade.contracts), 2)
        trade.current_mark = exit_debit
        trade.unrealized_pnl = 0.0
    elif trade.entry_debit is not None and exit_debit is not None:
        trade.realized_pnl = round(
            (exit_debit - trade.entry_debit) * OPTION_MULTIPLIER * trade.contracts, 2
        )
        trade.current_mark = exit_debit
        trade.unrealized_pnl = 0.0
    else:
        trade.realized_pnl = None
        trade.notes = (trade.notes or "") + "closed_without_quote;"
    trade.latest_decision = "EXIT"
    trade.exit_reason_codes = "; ".join(reason_codes) if reason_codes else exit_reason
    trade.latest_reason_codes = trade.exit_reason_codes
    trade.latest_explanation = explanation or f"Local paper position exited: {exit_reason}."
    return trade


def build_paper_mark(
    trade: PaperTrade,
    *,
    timestamp: str,
    leg_quote_values: tuple[dict[str, Any], ...] = (),
    regime_snapshot: RegimeSnapshot | None = None,
    decision: ExitDecision | None = None,
) -> PaperMark:
    resolved = decision or ExitDecision(
        trade.latest_decision,
        None,
        None,
        tuple(code.strip() for code in (trade.latest_reason_codes or "").split(";") if code.strip()),
        trade.latest_explanation or "Paper position marked.",
        {},
    )
    return PaperMark(
        timestamp=timestamp,
        paper_trade_id=trade.paper_trade_id,
        current_leg_quote_values=leg_quote_values,
        current_mark=trade.current_mark,
        unrealized_pnl=trade.unrealized_pnl,
        credit_kept_pct=trade.credit_kept_pct,
        distance_to_short_strike=trade.distance_to_short_strike,
        current_regime_snapshot=(regime_snapshot.to_dict() if regime_snapshot else None),
        exit_checks=resolved.checks,
        decision=resolved.decision,
        reason_codes=resolved.reason_codes,
        plain_english_reason=resolved.explanation,
    )
