"""Manual trade P&L math + record builder."""

from __future__ import annotations

import math
from datetime import datetime

from src.paper.manual_tracker import (
    build_manual_trade_record,
    realized_pnl_dollars,
    spread_width_from_strikes,
    unrealized_pnl_dollars,
)


def test_unrealized_pnl_dollars():
    # 5 contracts, $0.80 credit, current mark $0.40 → +$200
    assert math.isclose(unrealized_pnl_dollars(0.80, 0.40, 5), 200.0)
    # If mark > credit, P&L is negative
    assert math.isclose(unrealized_pnl_dollars(0.80, 1.20, 5), -200.0)


def test_realized_pnl_dollars():
    # closed at $0.20 debit on $0.80 credit, 5 contracts → +$300
    assert math.isclose(realized_pnl_dollars(0.80, 0.20, 5), 300.0)
    # stopped out at $2.00 debit (SL_150 of $0.80) → -$600
    assert math.isclose(realized_pnl_dollars(0.80, 2.00, 5), -600.0)


def test_spread_width_from_strikes_is_symmetric():
    assert spread_width_from_strikes(5810, 5815) == 5.0
    assert spread_width_from_strikes(5815, 5810) == 5.0


def test_build_manual_trade_record_computes_planned_and_theoretical():
    rec = build_manual_trade_record(
        ts=datetime(2026, 6, 1, 14, 30),
        strategy_id="vertical_wing_v1",
        side="CALL_CREDIT",
        symbol="SPX", expiry="2026-06-01",
        short_strike=5815, long_strike=5820,
        credit=0.80, contracts=5,
        entry_spot=5800, stop_variant="SL_150_PERCENT_LOSS",
        profit_target=0.50, notes="test",
    )
    assert rec["spread_width"] == 5.0
    # planned = credit * (mult-1) * 100 * contracts = 0.80 * 1.5 * 100 * 5 = 600
    assert math.isclose(rec["planned_loss_dollars"], 600.0)
    # theoretical = (width - credit) * 100 * contracts = 4.20 * 100 * 5 = 2100
    assert math.isclose(rec["theoretical_max_loss_dollars"], 2100.0)
    assert rec["unrealized_pnl"] is None  # no current_mark supplied
    assert rec["realized_pnl"] is None    # no exit_debit supplied


def test_build_manual_trade_record_with_current_mark_computes_unrealized():
    rec = build_manual_trade_record(
        ts=datetime(2026, 6, 1, 14, 30),
        strategy_id="t", side="CALL_CREDIT",
        symbol="SPX", expiry="2026-06-01",
        short_strike=5815, long_strike=5820,
        credit=0.80, contracts=5,
        entry_spot=5800, stop_variant="SL_150_PERCENT_LOSS",
        profit_target=0.50, notes=None,
        current_mark=0.40,
    )
    assert math.isclose(rec["unrealized_pnl"], 200.0)
