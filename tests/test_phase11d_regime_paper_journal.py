from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from scripts import paper_execution_smoke, review_portfolio_forward
from src.paper import ledger, lifecycle
from src.paper.models import ExecutionJournalEvent, PaperLifecycleConfig
from src.providers.structure.types import ExposureContext, StructureSnapshot
from src.regime.events import RegimeEventDebouncer
from src.regime.snapshot import build_regime_snapshot
from src.regime.types import RegimeLabel
from src.strategy_engine.candidates import build_credit_spread, build_long_option
from src.strategy_engine.types import (
    LegAction,
    OptionRight,
    StrategyArchetype,
    StrategyLeg,
)

NOW = datetime.fromisoformat("2026-06-22T11:00:00-04:00")


def _structure(
    *,
    spot: float = 5800.0,
    gamma: str = "positive",
    flip: float = 5775.0,
    quote_ts: datetime = NOW,
) -> StructureSnapshot:
    return StructureSnapshot(
        symbol="SPX",
        spot=spot,
        quote_ts=quote_ts,
        exposures=ExposureContext(
            total_gex_bn=4.0 if gamma == "positive" else -2.0,
            total_vex_bn=-1.0,
            gamma_flip=flip,
            call_wall=5825.0,
            put_wall=5775.0,
            maxvol=5800.0,
            gamma_regime=gamma,
            da_gex_signed=2.0 if gamma == "positive" else -2.0,
            call_floor_2k=5785.0,
            call_floor_5k=5790.0,
            call_floor_10k=5790.0,
            put_ceiling_2k=5815.0,
            put_ceiling_5k=5825.0,
            put_ceiling_10k=5830.0,
            call_floor_10k_volume=12000.0,
            call_floor_10k_w2_strike=5785.0,
            call_floor_10k_w2_volume=2400.0,
            put_ceiling_10k_volume=12000.0,
            put_ceiling_10k_w2_strike=5835.0,
            put_ceiling_10k_w2_volume=6000.0,
            gamma_primary=5800.0,
            gamma_secondary=5825.0,
        ),
        expiry="2026-06-22",
        dte=0,
        source="fixture",
    )


def _regime(*, spot: float = 5800.0, gamma: str = "positive", flip: float = 5775.0):
    return build_regime_snapshot(
        _structure(spot=spot, gamma=gamma, flip=flip),
        timestamp=NOW,
        quote_quality_status="usable",
    )


def _credit_candidate(side: str = "CALL", credit: float = 1.0):
    right = OptionRight.CALL if side == "CALL" else OptionRight.PUT
    archetype = (
        StrategyArchetype.CALL_CREDIT_SPREAD
        if side == "CALL"
        else StrategyArchetype.PUT_CREDIT_SPREAD
    )
    short_strike, long_strike = ((5810.0, 5815.0) if side == "CALL" else (5790.0, 5785.0))
    return build_credit_spread(
        timestamp=NOW,
        symbol="SPX",
        dte=0,
        expiry="2026-06-22",
        archetype=archetype,
        short_leg=StrategyLeg("SHORT", short_strike, right, LegAction.SELL, 1.9, 2.1, 2.0),
        long_leg=StrategyLeg("LONG", long_strike, right, LegAction.BUY, 0.9, 1.1, 1.0),
        credit=credit,
        thesis=f"Fixture {side.lower()} credit thesis.",
    )


def _long_candidate(archetype: StrategyArchetype):
    right = OptionRight.CALL if archetype == StrategyArchetype.LONG_CALL else OptionRight.PUT
    return build_long_option(
        timestamp=NOW,
        symbol="SPX",
        dte=0,
        expiry="2026-06-22",
        archetype=archetype,
        leg=StrategyLeg("LONG", 5800.0, right, LegAction.BUY, 1.9, 2.1, 2.0),
        debit=2.0,
        thesis="Fixture long premium thesis.",
        minimum_target_multiple=1.5,
    )


def _trade(candidate, regime=None):  # type: ignore[no-untyped-def]
    ticket = lifecycle.create_paper_ticket(
        candidate,
        profile_id="fixture",
        profile_hash="hash",
        regime_snapshot=regime or _regime(),
        target_mark=(0.5 if candidate.is_credit_spread else 3.0),
        stop_mark=(1.5 if candidate.is_credit_spread else 1.0),
    )
    return lifecycle.open_trade_from_ticket(
        ticket,
        run_id="R1",
        strategy_id="fixture",
        now_iso=NOW.isoformat(),
        config=PaperLifecycleConfig(exit_on_eod=False),
    )


def test_regime_snapshot_conservative_labels_and_existing_fields_only():
    absorption = _regime()
    assert absorption.final_regime_label == RegimeLabel.ABSORPTION
    assert absorption.corridor_valid is True
    assert absorption.wds_tier == 1 and absorption.dominant_wing_side == "CALL"
    transition = _regime(spot=5802.0, gamma="negative", flip=5800.0)
    assert transition.final_regime_label == RegimeLabel.TRANSITION
    acceleration = _regime(spot=5840.0, gamma="negative", flip=5800.0)
    assert acceleration.final_regime_label == RegimeLabel.ACCELERATION
    no_edge = build_regime_snapshot(_structure(), quote_quality_status="stale")
    assert no_edge.final_regime_label == RegimeLabel.NO_EDGE


def test_regime_snapshot_does_not_invent_unavailable_inputs():
    snapshot = _regime()
    assert set(snapshot.deferred_fields) == {
        "charm", "vanna", "theta_adjusted_charm", "vix", "iv_surface",
        "dom", "news", "per_strike_vex_skew",
    }
    row = snapshot.to_dict()
    assert "charm" not in row and "vix" not in row
    sparse = build_regime_snapshot(
        StructureSnapshot("SPX", 5800.0, NOW), quote_quality_status="unknown"
    )
    assert sparse.final_regime_label == RegimeLabel.UNKNOWN


def test_regime_change_event_debounce_and_cooldown():
    old = _regime()
    new = _regime(spot=5840.0, gamma="negative", flip=5800.0)
    debouncer = RegimeEventDebouncer(cooldown_seconds=300)
    first = debouncer.evaluate(old, new, affects_open_positions=True)
    assert first is not None
    assert first.severity.value == "CRITICAL" and first.suggested_action.value == "EXIT"
    assert debouncer.evaluate(old, new, affects_open_positions=True) is None


def test_credit_ticket_uses_leg_mids_not_requested_credit():
    ticket = lifecycle.create_paper_ticket(
        _credit_candidate(credit=1.25),
        profile_id="P",
        profile_hash="H",
        regime_snapshot=_regime(),
    )
    assert ticket.entry_credit == 1.0
    assert ticket.max_profit == 100.0 and ticket.max_loss == 400.0
    assert ticket.local_paper_only is True and ticket.no_broker_order_sent is True


def test_scanner_ticket_carries_phase11c_risk_quality_rejection():
    regime = _regime()
    row = {
        "side": "CALL_CREDIT",
        "short_strike": 5810.0,
        "long_strike": 5815.0,
        "short_bid": 0.60,
        "short_ask": 0.70,
        "short_mid": 0.65,
        "long_bid": 0.45,
        "long_ask": 0.55,
        "long_mid": 0.50,
        "selected_expiry": "2026-06-22",
        "quote_quality_bucket": "usable",
        "distance_from_spot": 40.0,
        "regime_snapshot_json": json.dumps(regime.to_dict()),
    }
    ticket = lifecycle.create_paper_ticket_from_signal(
        row,
        profile_id="P",
        profile_hash="H",
        symbol="SPX",
        target_dte=0,
        config=PaperLifecycleConfig(),
        now_iso=NOW.isoformat(),
    )
    assert ticket.entry_credit == 0.15
    assert ticket.risk_quality_label == "TOO_CHEAP_FOR_RISK"
    assert "credit_pct_of_width_too_low" in ticket.entry_reason_codes


@pytest.mark.parametrize("archetype", [StrategyArchetype.LONG_CALL, StrategyArchetype.LONG_PUT])
def test_long_call_put_ticket_uses_leg_mid(archetype):  # type: ignore[no-untyped-def]
    ticket = lifecycle.create_paper_ticket(
        _long_candidate(archetype), profile_id="P", profile_hash="H", regime_snapshot=_regime()
    )
    assert ticket.entry_debit == 2.0 and ticket.max_loss == 200.0
    assert ticket.archetype == archetype.value


def test_credit_spread_mark_and_pnl_math():
    trade = _trade(_credit_candidate())
    lifecycle.update_trade_mark(
        trade, {"available": True, "mid": 0.7, "bid": 0.65, "ask": 0.75}, NOW.isoformat()
    )
    assert trade.unrealized_pnl == 30.0 and trade.credit_kept_pct == 30.0
    lifecycle.close_trade(
        trade, exit_reason="take_profit", exit_debit=0.4, now_iso=NOW.isoformat()
    )
    assert trade.realized_pnl == 60.0


@pytest.mark.parametrize("archetype", [StrategyArchetype.LONG_CALL, StrategyArchetype.LONG_PUT])
def test_long_premium_mark_and_pnl_math(archetype):  # type: ignore[no-untyped-def]
    trade = _trade(_long_candidate(archetype))
    lifecycle.update_trade_mark(
        trade, {"available": True, "mid": 2.5, "bid": 2.4, "ask": 2.6}, NOW.isoformat()
    )
    assert trade.unrealized_pnl == 50.0
    lifecycle.close_trade(
        trade, exit_reason="take_profit", exit_debit=3.0, now_iso=NOW.isoformat()
    )
    assert trade.realized_pnl == 100.0


def test_tp_sl_eod_and_quote_failure_exits():
    cfg = PaperLifecycleConfig(exit_on_eod=False, max_missing_quote_marks=2)
    tp = _trade(_credit_candidate())
    lifecycle.update_trade_mark(tp, {"available": True, "mid": 0.4}, NOW.isoformat())
    assert lifecycle.evaluate_exit_decision(tp, cfg, NOW).exit_reason == "take_profit"
    sl = _trade(_credit_candidate())
    lifecycle.update_trade_mark(sl, {"available": True, "mid": 1.6}, NOW.isoformat())
    assert lifecycle.evaluate_exit_decision(sl, cfg, NOW).exit_reason == "stop_loss"
    eod = _trade(_credit_candidate())
    eod_cfg = PaperLifecycleConfig(eod_exit_time="15:55")
    assert lifecycle.evaluate_exit_decision(
        eod, eod_cfg, datetime.fromisoformat("2026-06-22T15:56:00-04:00")
    ).exit_reason == "eod_exit"
    invalid = _trade(_credit_candidate())
    assert lifecycle.evaluate_exit_decision(
        invalid, cfg, NOW, quote_status="stale"
    ).exit_reason == "quote_invalid"
    missing = _trade(_credit_candidate())
    lifecycle.update_trade_mark(missing, None, NOW.isoformat())
    lifecycle.update_trade_mark(missing, None, NOW.isoformat())
    assert lifecycle.evaluate_exit_decision(missing, cfg, NOW).exit_reason == "quote_failure_limit"


def test_hostile_regime_exits_are_archetype_specific():
    call = _trade(_credit_candidate("CALL"))
    upside = _regime(spot=5840.0, gamma="negative", flip=5800.0)
    call_decision = lifecycle.evaluate_exit_decision(
        call, PaperLifecycleConfig(exit_on_eod=False), NOW, regime_snapshot=upside
    )
    assert call_decision.decision == "EXIT"
    put = _trade(_credit_candidate("PUT"))
    downside = _regime(spot=5780.0, gamma="negative", flip=5800.0)
    put_decision = lifecycle.evaluate_exit_decision(
        put, PaperLifecycleConfig(exit_on_eod=False), NOW, regime_snapshot=downside
    )
    assert put_decision.decision == "EXIT"


@pytest.mark.parametrize(
    ("archetype", "regime"),
    [
        (StrategyArchetype.LONG_CALL, _regime(spot=5840.0, gamma="negative", flip=5800.0)),
        (StrategyArchetype.LONG_PUT, _regime(spot=5780.0, gamma="negative", flip=5800.0)),
    ],
)
def test_supportive_acceleration_holds_long_premium(archetype, regime):  # type: ignore[no-untyped-def]
    trade = _trade(_long_candidate(archetype))
    decision = lifecycle.evaluate_exit_decision(
        trade, PaperLifecycleConfig(exit_on_eod=False), NOW, regime_snapshot=regime
    )
    assert decision.decision == "HOLD"
    assert "regime_supports_long_premium_thesis" in decision.reason_codes


def test_alert_only_is_distinct_from_mandatory_exit():
    entry = _regime()
    weakened = replace(entry, wds_value=0.2, wds_tier=4)
    trade = _trade(_long_candidate(StrategyArchetype.LONG_CALL), entry)
    decision = lifecycle.evaluate_exit_decision(
        trade, PaperLifecycleConfig(exit_on_eod=False), NOW, regime_snapshot=weakened
    )
    assert decision.decision == "ALERT_ONLY" and decision.exit_reason is None


def test_journal_marks_regimes_and_review_compatibility(tmp_path, capsys):
    root = tmp_path / "portfolio"
    run_dir, latest_dir = root / "runs" / "R1", root / "latest"
    trade = _trade(_credit_candidate())
    event = ExecutionJournalEvent(
        timestamp=NOW.isoformat(), action="ENTERED", paper_trade_id=trade.paper_trade_id,
        profile_id="fixture", quote_values_used={"entry_credit": 1.0},
        regime_snapshot_summary=_regime().plain_english_summary,
        risk_quality_summary="ACCEPTABLE", reason_codes=("local_chain_mid_fill",),
        plain_english_explanation="Entered locally.", pnl_impact=0.0,
    )
    mark = lifecycle.build_paper_mark(
        trade, timestamp=NOW.isoformat(), regime_snapshot=_regime()
    )
    change = RegimeEventDebouncer().evaluate(
        _regime(), _regime(spot=5840.0, gamma="negative", flip=5800.0)
    )
    ledger.write_manifest(run_dir, latest_dir, {
        "portfolio_run_id": "R1", "profiles": ["fixture"], "status": "completed",
    })
    ledger.write_open_trades(run_dir, latest_dir, [trade])
    ledger.write_closed_trades(run_dir, latest_dir, [])
    ledger.write_summary(run_dir, latest_dir, ledger.compute_summary([trade], []))
    ledger.write_execution_journal(run_dir, latest_dir, [event])
    ledger.write_paper_marks(run_dir, latest_dir, [mark])
    ledger.write_regime_events(run_dir, latest_dir, [change] if change else [])
    ledger.write_latest_open_positions(run_dir, latest_dir, [trade])
    ledger.append_event(run_dir, ledger.make_event(
        event_type="open", timestamp=NOW.isoformat(), paper_trade_id=trade.paper_trade_id,
        profile_id="fixture", reason="local_chain_mid_fill", trade=trade,
    ))
    ledger.reconcile_run("R1", root=root)
    assert "local_chain_mid_fill" in (run_dir / "paper_execution_journal.jsonl").read_text()
    assert "Reason codes" in (run_dir / "paper_execution_journal.md").read_text()
    assert "reason_codes" in (run_dir / "paper_marks.csv").read_text().splitlines()[0]
    assert ledger.load_latest_open_positions("latest", root)[0]["local_paper_only"] is True
    assert review_portfolio_forward.main(["--output-dir", str(root), "--latest"]) == 0
    assert "portfolio run" in capsys.readouterr().out


def test_deterministic_paper_execution_smoke_writes_required_outputs(tmp_path):
    root = tmp_path / "paper"
    assert paper_execution_smoke.main([
        "--symbol", "SPX", "--profile", "morning_5k_call_tp75_control",
        "--dte", "0", "--contracts", "1", "--output-dir", str(root),
    ]) == 0
    expected = {
        "paper_execution_journal.jsonl", "paper_execution_journal.md",
        "paper_marks.csv", "paper_regime_events.jsonl", "latest_open_positions.json",
        "paper_trades_closed.csv",
    }
    assert expected <= {path.name for path in (root / "latest").iterdir()}
    closed = ledger.load_closed_trades("latest", root)
    assert closed[0]["exit_reason"] == "take_profit"
    assert float(closed[0]["realized_pnl"]) == 60.0


def test_ui_and_scanner_expose_phase11d_surfaces_without_execution_paths():
    repo = Path(__file__).resolve().parents[1]
    ui_source = (repo / "src/app/streamlit_main.py").read_text(encoding="utf-8")
    scanner_source = (repo / "scripts/run_scanner.py").read_text(encoding="utf-8")
    for label in (
        "LOCAL PAPER ONLY — NO BROKER ORDER SENT",
        "Execution journal",
        "Regime change events",
        "Rejected candidates",
    ):
        assert label in ui_source
    assert "regime_snapshot_json" in scanner_source
    files = (
        "src/regime/snapshot.py", "src/regime/events.py", "src/paper/lifecycle.py",
        "src/paper/ledger.py", "scripts/paper_execution_smoke.py",
    )
    forbidden = (
        "submit_order", "place_order", "preview_order", "create_order",
        "execute_trade", "order_preview", "enable_order_submission",
    )
    for relative in files:
        text = (repo / relative).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{relative} contains forbidden token {token}"
