"""Phase 10B — historical exit simulation (pure, deterministic).

Given a selected vertical credit spread and the day's post-entry snapshots,
simulate the exit the SAME way the reference ``vertical_wing_backtest`` does:

  * Re-price the spread mid-to-mid at each snapshot strictly after entry through
    the settlement snapshot:  ``debit = short_mid - long_mid`` (same option side).
  * TAKE PROFIT  fires when ``debit <= (1 - capture) * credit``
        capture 0.50 (TP50) -> debit <= 0.50 * credit
        capture 0.75 (TP75) -> debit <= 0.25 * credit
  * STOP LOSS    fires when ``debit >= (1 + loss) * credit``
        loss 1.50 (SL150) -> debit >= 2.50 * credit
        loss 2.00 (SL200) -> debit >= 3.00 * credit
  * First event wins. If TP and SL trigger on the SAME snapshot, ``event_conflict``
    is set and SL wins (conservative), matching the reference.
  * EOD / SETTLEMENT proxy: the first snapshot whose time is in
    ``[16:00:00, 16:00:00 + window]`` (default 20 min). The spread settles to its
    cash-settle INTRINSIC from that snapshot's spot (these are 0DTE cash-settled
    spreads). If no post-16:00 snapshot exists, the last post-entry snapshot is
    the fallback proxy.

This is HISTORICAL SIMULATION ONLY. There is no live paper-lifecycle mutation,
no broker, no order. Points -> dollars is ``points * 100 * contracts`` exactly
once per quantity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.backtesting import schemas

OPTION_MULTIPLIER = 100
_SETTLEMENT_WINDOW_MIN = 20
_EOD_SECS = 16 * 3600

_CALL = "CALL_CREDIT"


def _num(v: object) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _secs(ts: datetime) -> int:
    return ts.hour * 3600 + ts.minute * 60 + ts.second


@dataclass
class DayIndex:
    """Per-day price/spot index built ONCE and reused across profiles."""

    timestamps: list[datetime]                       # sorted, distinct (RTH)
    mids: dict[datetime, dict[float, tuple[float | None, float | None]]]  # ts->strike->(call_mid,put_mid)
    spots: dict[datetime, float | None]              # ts -> spot


def build_day_index(rows: list[dict], symbol: str) -> DayIndex:
    """Index raw rows by timestamp -> strike -> (call_mid, put_mid) + spot."""
    cfg = schemas.symbol_config(symbol)
    spot_col = cfg.spot_col
    mids: dict[datetime, dict[float, tuple[float | None, float | None]]] = {}
    spots: dict[datetime, float | None] = {}
    for r in rows:
        ts = r.get("_ts")
        if ts is None:
            continue
        k = _num(r.get("Strike") if r.get("Strike") not in (None, "") else r.get("strike"))
        if k is None:
            continue
        cb, ca = _num(r.get("CALL BID")), _num(r.get("CALL ASK"))
        pb, pa = _num(r.get("PUT BID")), _num(r.get("PUT ASK"))
        cmid = (cb + ca) / 2.0 if cb is not None and ca is not None else None
        pmid = (pb + pa) / 2.0 if pb is not None and pa is not None else None
        mids.setdefault(ts, {})[k] = (cmid, pmid)
        if ts not in spots:
            spots[ts] = _num(
                r.get(spot_col)
                if r.get(spot_col) not in (None, "")
                else (r.get("spot") or r.get("Spot"))
            )
    return DayIndex(timestamps=sorted(mids.keys()), mids=mids, spots=spots)


def _spread_debit(
    day: DayIndex, ts: datetime, side: str, short_k: float, long_k: float,
) -> float | None:
    table = day.mids.get(ts)
    if not table:
        return None
    short = table.get(short_k)
    long_ = table.get(long_k)
    if short is None or long_ is None:
        return None
    idx = 0 if side == _CALL else 1     # call_mid vs put_mid
    s, ln = short[idx], long_[idx]
    if s is None or ln is None:
        return None
    return s - ln


def find_settlement(
    day: DayIndex, *, window_min: int = _SETTLEMENT_WINDOW_MIN,
) -> tuple[datetime | None, str | None]:
    """First snapshot in [16:00:00, 16:00:00+window]; else (None, None)."""
    end = _EOD_SECS + window_min * 60
    for ts in day.timestamps:
        if _EOD_SECS <= _secs(ts) <= end:
            return ts, "post_1600_cash_settle_proxy"
    return None, None


def _settlement_intrinsic(
    side: str, short_k: float, long_k: float, final_spot: float | None,
) -> float | None:
    if final_spot is None:
        return None
    if side == _CALL:
        short_i = max(final_spot - short_k, 0.0)
        long_i = max(final_spot - long_k, 0.0)
    else:  # PUT_CREDIT
        short_i = max(short_k - final_spot, 0.0)
        long_i = max(long_k - final_spot, 0.0)
    return short_i - long_i


@dataclass
class ExitResult:
    exit_timestamp: datetime | None
    exit_reason: str                 # TP | SL | EOD | SKIPPED
    exit_debit_points: float | None
    exit_debit_dollars: float | None
    pnl_points: float | None
    pnl_dollars: float | None
    credit_kept_pct: float | None
    hold_minutes: float | None
    max_spot_after_entry: float | None
    min_spot_after_entry: float | None
    short_touched_after_entry: bool
    long_touched_after_entry: bool
    stop_triggered: bool
    tp_triggered: bool
    event_conflict: bool
    missing_price_count: int
    snapshots_checked: int
    settlement_method: str | None


def _spot_path(
    day: DayIndex, entry_ts: datetime, exit_ts: datetime, side: str,
    short_k: float, long_k: float,
) -> tuple[float | None, float | None, bool, bool]:
    spots = [
        day.spots[t]
        for t in day.timestamps
        if entry_ts < t <= exit_ts and day.spots.get(t) is not None
    ]
    if not spots:
        return None, None, False, False
    hi, lo = max(spots), min(spots)
    if side == _CALL:
        short_touched = hi >= short_k
        long_touched = hi >= long_k
    else:
        short_touched = lo <= short_k
        long_touched = lo <= long_k
    return hi, lo, short_touched, long_touched


def simulate_exit(
    day: DayIndex,
    *,
    entry_ts: datetime,
    side: str,
    short_strike: float,
    long_strike: float,
    entry_credit_points: float,
    take_profit_capture: float | None,
    stop_loss_loss: float | None,
    contracts: int = 1,
) -> ExitResult:
    """Simulate the exit for one selected spread. Returns an :class:`ExitResult`."""
    credit = float(entry_credit_points)
    tp_threshold = (1.0 - take_profit_capture) * credit if take_profit_capture is not None else None
    sl_threshold = (1.0 + stop_loss_loss) * credit if stop_loss_loss is not None else None

    settlement_ts, settle_method = find_settlement(day)
    if settlement_ts is None:
        after = [t for t in day.timestamps if t > entry_ts]
        settlement_ts = after[-1] if after else entry_ts
        settle_method = "last_snapshot_fallback"

    scan_ts = [t for t in day.timestamps if entry_ts < t <= settlement_ts]
    snapshots_checked = 0
    missing_price_count = 0
    tp_triggered = stop_triggered = event_conflict = False
    exit_reason: str | None = None
    exit_ts: datetime | None = None
    exit_debit: float | None = None

    for t in scan_ts:
        debit = _spread_debit(day, t, side, short_strike, long_strike)
        if debit is None:
            missing_price_count += 1
            continue
        snapshots_checked += 1
        tp_hit = tp_threshold is not None and debit <= tp_threshold
        sl_hit = sl_threshold is not None and debit >= sl_threshold
        if tp_hit and sl_hit:
            event_conflict = True
            stop_triggered = True
            exit_reason, exit_ts, exit_debit = "SL", t, debit
            break
        if tp_hit:
            tp_triggered = True
            exit_reason, exit_ts, exit_debit = "TP", t, debit
            break
        if sl_hit:
            stop_triggered = True
            exit_reason, exit_ts, exit_debit = "SL", t, debit
            break

    if exit_reason is None:
        # EOD / cash-settle proxy — intrinsic from the settlement spot.
        exit_ts = settlement_ts
        exit_reason = "EOD"
        final_spot = day.spots.get(settlement_ts)
        exit_debit = _settlement_intrinsic(side, short_strike, long_strike, final_spot)
        if exit_debit is None:
            md = _spread_debit(day, settlement_ts, side, short_strike, long_strike)
            exit_debit = md

    if exit_ts is None or exit_debit is None:
        # No usable post-entry pricing at all → cannot settle this trade.
        return ExitResult(
            exit_timestamp=exit_ts, exit_reason="SKIPPED",
            exit_debit_points=None, exit_debit_dollars=None,
            pnl_points=None, pnl_dollars=None, credit_kept_pct=None,
            hold_minutes=None, max_spot_after_entry=None, min_spot_after_entry=None,
            short_touched_after_entry=False, long_touched_after_entry=False,
            stop_triggered=stop_triggered, tp_triggered=tp_triggered,
            event_conflict=event_conflict, missing_price_count=missing_price_count,
            snapshots_checked=snapshots_checked, settlement_method=settle_method,
        )

    pnl_points = credit - exit_debit
    pnl_dollars = pnl_points * OPTION_MULTIPLIER * int(contracts)
    credit_kept_pct = (pnl_points / credit * 100.0) if credit else None
    hold_minutes = round((exit_ts - entry_ts).total_seconds() / 60.0, 2)
    hi, lo, short_touched, long_touched = _spot_path(
        day, entry_ts, exit_ts, side, short_strike, long_strike
    )
    return ExitResult(
        exit_timestamp=exit_ts,
        exit_reason=exit_reason,
        exit_debit_points=round(exit_debit, 4),
        exit_debit_dollars=round(exit_debit * OPTION_MULTIPLIER * int(contracts), 2),
        pnl_points=round(pnl_points, 4),
        pnl_dollars=round(pnl_dollars, 2),
        credit_kept_pct=round(credit_kept_pct, 2) if credit_kept_pct is not None else None,
        hold_minutes=hold_minutes,
        max_spot_after_entry=hi,
        min_spot_after_entry=lo,
        short_touched_after_entry=short_touched,
        long_touched_after_entry=long_touched,
        stop_triggered=stop_triggered,
        tp_triggered=tp_triggered,
        event_conflict=event_conflict,
        missing_price_count=missing_price_count,
        snapshots_checked=snapshots_checked,
        settlement_method=settle_method,
    )
