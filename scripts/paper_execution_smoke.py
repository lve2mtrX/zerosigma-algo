"""Deterministic Phase 11D local-paper lifecycle smoke.

Uses in-memory structure and option-quote fixtures only. It does not contact a
market-data provider or brokerage and cannot create or send an order.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from src.paper import ledger, lifecycle
from src.paper.models import ExecutionJournalEvent, PaperLifecycleConfig
from src.providers.structure.types import ExposureContext, StructureSnapshot
from src.regime.events import RegimeEventDebouncer
from src.regime.snapshot import build_regime_snapshot
from src.strategy_engine.candidates import build_credit_spread
from src.strategy_engine.risk_quality import evaluate_risk_quality
from src.strategy_engine.types import (
    LegAction,
    OptionRight,
    StrategyArchetype,
    StrategyLeg,
)
from src.utils.time import now_et

REPO_ROOT = Path(__file__).resolve().parents[1]


def _structure(symbol: str, timestamp: datetime, *, transition: bool = False) -> StructureSnapshot:
    return StructureSnapshot(
        symbol=symbol,
        spot=5802.0 if transition else 5800.0,
        quote_ts=timestamp,
        exposures=ExposureContext(
            total_gex_bn=-1.0 if transition else 4.0,
            total_vex_bn=-0.8,
            gamma_flip=5800.0 if transition else 5775.0,
            call_wall=5825.0,
            put_wall=5775.0,
            maxvol=5805.0 if transition else 5800.0,
            gamma_regime="negative" if transition else "positive",
            da_gex_signed=-1.0 if transition else 2.0,
            call_floor_2k=5785.0,
            call_floor_5k=5790.0,
            call_floor_10k=5790.0,
            put_ceiling_2k=5815.0,
            put_ceiling_5k=5825.0,
            put_ceiling_10k=5830.0,
            call_floor_10k_volume=12000.0,
            call_floor_10k_w2_strike=5785.0,
            call_floor_10k_w2_volume=3000.0,
            put_ceiling_10k_volume=13000.0,
            put_ceiling_10k_w2_strike=5835.0,
            put_ceiling_10k_w2_volume=5000.0,
            gamma_primary=5800.0,
            gamma_secondary=5825.0,
        ),
        expiry=timestamp.date().isoformat(),
        dte=0,
        source="phase11d_fixture",
    )


def _journal(
    *,
    timestamp: str,
    action: str,
    trade,
    regime,
    reasons: tuple[str, ...],
    explanation: str,
    pnl: float | None,
    quote_values: dict,
) -> ExecutionJournalEvent:
    return ExecutionJournalEvent(
        timestamp=timestamp,
        action=action,
        paper_trade_id=trade.paper_trade_id,
        profile_id=trade.profile_id,
        quote_values_used=quote_values,
        regime_snapshot_summary=regime.plain_english_summary,
        risk_quality_summary=trade.risk_quality_label,
        reason_codes=reasons,
        plain_english_explanation=explanation,
        pnl_impact=pnl,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic local-paper ticket/mark/exit smoke (no brokerage)."
    )
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--profile", default="morning_5k_call_tp75_control")
    parser.add_argument("--dte", type=int, default=0)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--output-dir", default="outputs/portfolio_forward")
    parser.add_argument("--run-label", default="phase11d_paper_smoke")
    args = parser.parse_args(argv)

    started = now_et().replace(microsecond=0)
    run_id = f"{started.strftime('%Y%m%d_%H%M%S')}_{args.run_label}"
    root = Path(args.output_dir)
    if not root.is_absolute():
        root = REPO_ROOT / root
    run_dir = root / "runs" / run_id
    latest_dir = root / "latest"
    config = PaperLifecycleConfig(
        contracts=max(1, args.contracts),
        take_profit_pct=0.50,
        stop_loss_pct=1.50,
        exit_on_eod=False,
    )
    entry_regime = build_regime_snapshot(
        _structure(args.symbol, started),
        timestamp=started,
        quote_quality_status="usable",
        spot_history=(5798.0, 5800.0),
    )
    short = StrategyLeg(
        f"{args.symbol}:FIXTURE:5810:C", 5810.0, OptionRight.CALL, LegAction.SELL,
        1.95, 2.05, 2.00,
    )
    long = StrategyLeg(
        f"{args.symbol}:FIXTURE:5815:C", 5815.0, OptionRight.CALL, LegAction.BUY,
        0.95, 1.05, 1.00,
    )
    candidate = build_credit_spread(
        timestamp=started,
        symbol=args.symbol,
        dte=args.dte,
        expiry=started.date().isoformat(),
        archetype=StrategyArchetype.CALL_CREDIT_SPREAD,
        short_leg=short,
        long_leg=long,
        credit=1.00,
        contracts=max(1, args.contracts),
        distance_to_short_strike=10.0,
        regime_label=entry_regime.final_regime_label.value,
        quote_quality="usable",
        thesis="Call credit remains below the short strike inside contained structure.",
        stop_loss_multiple=1.50,
    )
    risk = evaluate_risk_quality(candidate)
    ticket = lifecycle.create_paper_ticket(
        candidate,
        profile_id=args.profile,
        profile_hash="phase11d_fixture_profile_hash",
        risk_quality=risk,
        regime_snapshot=entry_regime,
        reason_codes=("deterministic_smoke_candidate",),
        target_mark=0.50,
        stop_mark=1.50,
    )
    trade = lifecycle.open_trade_from_ticket(
        ticket,
        run_id=run_id,
        strategy_id="vertical_wing",
        now_iso=started.isoformat(),
        config=config,
    )
    journal = [_journal(
        timestamp=started.isoformat(),
        action="ENTERED",
        trade=trade,
        regime=entry_regime,
        reasons=ticket.entry_reason_codes,
        explanation="Fixture candidate entered locally at a 1.00 mid credit.",
        pnl=0.0,
        quote_values={"short_mid": 2.00, "long_mid": 1.00, "entry_credit": 1.00},
    )]
    marks = []
    regime_events = []
    ledger.append_event(run_dir, ledger.make_event(
        event_type="open",
        timestamp=started.isoformat(),
        paper_trade_id=trade.paper_trade_id,
        profile_id=trade.profile_id,
        reason="deterministic_smoke_candidate",
        trade=trade,
    ))

    hold_time = started + timedelta(minutes=1)
    hold_quote = {"bid": 0.65, "ask": 0.75, "mid": 0.70, "available": True, "spot": 5801.0}
    lifecycle.update_trade_mark(trade, hold_quote, hold_time.isoformat())
    hold_decision = lifecycle.evaluate_exit_decision(
        trade, config, hold_time, regime_snapshot=entry_regime, quote_status="usable"
    )
    marks.append(lifecycle.build_paper_mark(
        trade,
        timestamp=hold_time.isoformat(),
        leg_quote_values=(
            {"action": "SELL", "strike": 5810.0, "mid": 1.70},
            {"action": "BUY", "strike": 5815.0, "mid": 1.00},
        ),
        regime_snapshot=entry_regime,
        decision=hold_decision,
    ))
    journal.extend((
        _journal(
            timestamp=hold_time.isoformat(), action="MARKED", trade=trade,
            regime=entry_regime, reasons=("mark_updated_from_chain_mid",),
            explanation="Fixture position marked at a 0.70 closing debit.",
            pnl=trade.unrealized_pnl,
            quote_values={"short_mid": 1.70, "long_mid": 1.00, "mark": 0.70},
        ),
        _journal(
            timestamp=hold_time.isoformat(), action="HELD", trade=trade,
            regime=entry_regime, reasons=hold_decision.reason_codes,
            explanation=hold_decision.explanation, pnl=trade.unrealized_pnl,
            quote_values={"mark": 0.70},
        ),
    ))
    ledger.append_event(run_dir, ledger.make_event(
        event_type="update", timestamp=hold_time.isoformat(),
        paper_trade_id=trade.paper_trade_id, profile_id=trade.profile_id,
        reason="hold", trade=trade,
    ))

    exit_time = hold_time + timedelta(minutes=1)
    exit_regime = build_regime_snapshot(
        _structure(args.symbol, exit_time, transition=True),
        timestamp=exit_time,
        quote_quality_status="usable",
        spot_history=(5798.0, 5800.0, 5802.0),
        previous=entry_regime,
    )
    event = RegimeEventDebouncer(cooldown_seconds=300).evaluate(
        entry_regime, exit_regime, affects_open_positions=True
    )
    if event is not None:
        regime_events.append(event)
    exit_quote = {"bid": 0.35, "ask": 0.45, "mid": 0.40, "available": True, "spot": 5802.0}
    lifecycle.update_trade_mark(trade, exit_quote, exit_time.isoformat())
    exit_decision = lifecycle.evaluate_exit_decision(
        trade, config, exit_time, regime_snapshot=exit_regime, quote_status="usable"
    )
    marks.append(lifecycle.build_paper_mark(
        trade,
        timestamp=exit_time.isoformat(),
        leg_quote_values=(
            {"action": "SELL", "strike": 5810.0, "mid": 1.40},
            {"action": "BUY", "strike": 5815.0, "mid": 1.00},
        ),
        regime_snapshot=exit_regime,
        decision=exit_decision,
    ))
    lifecycle.close_trade(
        trade,
        exit_reason=exit_decision.exit_reason or "error",
        exit_debit=exit_decision.exit_mark,
        now_iso=exit_time.isoformat(),
        reason_codes=exit_decision.reason_codes,
        explanation=exit_decision.explanation,
    )
    journal.extend((
        _journal(
            timestamp=exit_time.isoformat(), action="MARKED", trade=trade,
            regime=exit_regime, reasons=("mark_updated_from_chain_mid",),
            explanation="Fixture position marked at a 0.40 closing debit.",
            pnl=60.0 * max(1, args.contracts),
            quote_values={"short_mid": 1.40, "long_mid": 1.00, "mark": 0.40},
        ),
        _journal(
            timestamp=exit_time.isoformat(), action="EXITED", trade=trade,
            regime=exit_regime, reasons=exit_decision.reason_codes,
            explanation=exit_decision.explanation, pnl=trade.realized_pnl,
            quote_values={"exit_mark": exit_decision.exit_mark},
        ),
    ))
    ledger.append_event(run_dir, ledger.make_event(
        event_type="close", timestamp=exit_time.isoformat(),
        paper_trade_id=trade.paper_trade_id, profile_id=trade.profile_id,
        reason=exit_decision.exit_reason, trade=trade,
    ))

    manifest = {
        "portfolio_run_id": run_id,
        "profiles": [args.profile],
        "started_at": started.isoformat(),
        "ended_at": exit_time.isoformat(),
        "status": "completed",
        "fixture_mode": True,
        "no_execution": True,
        "local_paper_only": True,
        "no_broker_order_sent": True,
        "execution_mode": "local_paper_lifecycle_only",
    }
    ledger.write_manifest(run_dir, latest_dir, manifest)
    ledger.write_open_trades(run_dir, latest_dir, [])
    ledger.write_closed_trades(run_dir, latest_dir, [trade])
    ledger.write_execution_journal(run_dir, latest_dir, journal)
    ledger.write_paper_marks(run_dir, latest_dir, marks)
    ledger.write_regime_events(run_dir, latest_dir, regime_events)
    ledger.write_latest_open_positions(run_dir, latest_dir, [])
    ledger.write_summary(run_dir, latest_dir, ledger.compute_summary([], [trade]))
    ledger.write_heartbeat(run_dir, latest_dir, {
        "portfolio_run_id": run_id,
        "status": "completed",
        "latest_tick_time": exit_time.isoformat(),
        "open_trade_count": 0,
        "closed_trade_count": 1,
        "total_pnl": trade.realized_pnl,
        "no_execution": True,
    })
    ledger.reconcile_run(run_id, root=root)

    print("Phase 11D deterministic local-paper smoke completed")
    print(f"  ticket: {ticket.ticket_id}")
    print(f"  decisions: {hold_decision.decision} -> {exit_decision.decision}")
    print(f"  exit: {trade.exit_reason} at {trade.exit_credit_or_debit}")
    print(f"  realized P&L: ${trade.realized_pnl:.2f}")
    print(f"  artifacts: {run_dir}")
    print("  safety: local paper only; no broker order sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
