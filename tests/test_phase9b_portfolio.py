"""Phase 9B — local paper-trade lifecycle + portfolio runner. NO network, NO
creds, NO execution.

Engine/ledger cases use synthetic signal rows and EXPLICIT ET datetimes so they
are deterministic and wall-clock independent. Runner cases drive the real scanner
on the mock provider (offline) into tmp dirs, and pass --no-exit-on-eod so open
trades persist regardless of the time of day the suite runs.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from src.paper import ledger, lifecycle
from src.paper.models import PaperLifecycleConfig, PaperTrade
from src.utils.time import now_et

run_portfolio = importlib.import_module("scripts.run_portfolio_forward")
review_portfolio = importlib.import_module("scripts.review_portfolio_forward")

SCORE_BEST = "vertical_wing_score_best_1dte"
NO_TRADE = "vertical_wing_no_trade"


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(*, side="CALL_CREDIT", short=5815.0, long=5820.0, expiry="2026-06-02",
         credit=0.60, short_bid=0.60, short_ask=0.70, short_mid=0.65,
         long_bid=0.02, long_ask=0.10, long_mid=0.05, **extra):
    """Synthetic ranked_candidates row. Default legs → spread_mid = 0.60 = credit
    (entry mark ≈ credit, so unrealized starts ~0)."""
    r = {
        "side": side, "short_strike": short, "long_strike": long,
        "selected_expiry": expiry, "credit": credit,
        "short_bid": short_bid, "short_ask": short_ask, "short_mid": short_mid,
        "long_bid": long_bid, "long_ask": long_ask, "long_mid": long_mid,
        "quote_timestamp": "2026-06-02T10:00:00-04:00",
        "planned_stop_risk_dollars": 100.0, "theoretical_max_loss_dollars": 440.0,
        "selected_trade": "true",
    }
    r.update(extra)
    return r


def _open(row, *, config=None, profile_id="prof_a", profile_hash="hashA",
          target_dte=1, now_iso="2026-06-02T10:00:00-04:00", trade_date="2026-06-02"):
    return lifecycle.open_trade_from_signal(
        row, run_id="run_x", profile_id=profile_id, profile_hash=profile_hash,
        strategy_id="vertical_wing_v1", symbol="SPX", target_dte=target_dte,
        config=config or PaperLifecycleConfig(), now_iso=now_iso, trade_date=trade_date,
    )


def _at(hour, minute):
    """An ET-aware datetime today at HH:MM (keeps tz, only the time-of-day matters)."""
    return now_et().replace(hour=hour, minute=minute, second=0, microsecond=0)


# ── 1. open from selected signal ─────────────────────────────────────────────

def test_paper_trade_opens_from_selected_signal():
    t = _open(_row())
    assert t.status == "open"
    assert t.side == "CALL_CREDIT"
    assert t.entry_credit == 0.60
    assert t.contracts == 1
    assert t.spread_width == 5.0
    assert t.max_profit == 60.0          # 0.60 * 100 * 1
    assert t.max_loss == 440.0           # (5.0 - 0.60) * 100 * 1
    assert t.paper_trade_id.startswith("pt_")
    assert t.trade_identity and "CALL_CREDIT" in t.trade_identity
    # entry mark = spread mid = 0.65 - 0.05 = 0.60 → unrealized ~0
    assert t.current_mark == 0.60
    assert t.unrealized_pnl == 0.0


# ── 2. duplicate signal does not open a duplicate trade ──────────────────────

def test_duplicate_signal_does_not_open_duplicate():
    cfg = PaperLifecycleConfig()
    t = _open(_row(), config=cfg)
    ok, reason, is_dup = lifecycle.can_open(
        identity=t.trade_identity, side="CALL_CREDIT", short_strike=5815.0,
        long_strike=5820.0, selected_expiry="2026-06-02", profile_id="prof_a",
        open_trades=[t], config=cfg,
    )
    assert ok is False and is_dup is True and reason is None


# ── 3. multiple profiles run in one portfolio tick ───────────────────────────

def test_multiple_profiles_one_tick(tmp_path):
    rc = run_portfolio.main([
        "--profiles", f"{SCORE_BEST},{NO_TRADE}", "--once", "--interval-seconds", "0",
        "--output-dir", str(tmp_path / "pf"), "--no-exit-on-eod",
    ])
    assert rc == 0
    man = json.loads((tmp_path / "pf" / "latest" / "portfolio_manifest.json").read_text())
    assert man["profiles"] == [SCORE_BEST, NO_TRADE]
    # both profiles produced a profile_tick record for tick 1
    rd = tmp_path / "pf" / "runs" / man["portfolio_run_id"]
    pticks = [json.loads(x) for x in (rd / "profile_tick_log.jsonl").read_text().splitlines() if x.strip()]
    profs = {p["profile_id"] for p in pticks if p["tick_id"] == 1}
    assert profs == {SCORE_BEST, NO_TRADE}


# ── 4 + 5 + 6. portfolio limits + duplicate-strike block ─────────────────────

def test_portfolio_max_open_total_blocks():
    cfg = PaperLifecycleConfig(max_open_trades_total=1, max_open_trades_per_profile=99,
                               allow_multiple_open_per_profile=True, allow_duplicate_strikes=True)
    t1 = _open(_row(short=5815.0, long=5820.0), config=cfg, profile_id="A")
    ok, reason, is_dup = lifecycle.can_open(
        identity="other", side="CALL_CREDIT", short_strike=5830.0, long_strike=5835.0,
        selected_expiry="2026-06-02", profile_id="B", open_trades=[t1], config=cfg)
    assert ok is False and reason == "total_max_open_reached" and is_dup is False


def test_per_profile_max_open_blocks():
    cfg = PaperLifecycleConfig(max_open_trades_total=99, max_open_trades_per_profile=1,
                               allow_multiple_open_per_profile=True, allow_duplicate_strikes=True)
    t1 = _open(_row(short=5815.0, long=5820.0), config=cfg, profile_id="A")
    ok, reason, _ = lifecycle.can_open(
        identity="other", side="CALL_CREDIT", short_strike=5830.0, long_strike=5835.0,
        selected_expiry="2026-06-02", profile_id="A", open_trades=[t1], config=cfg)
    assert ok is False and reason == "per_profile_max_open_reached"


def test_duplicate_strikes_block_when_disallowed():
    cfg = PaperLifecycleConfig(allow_duplicate_strikes=False, allow_multiple_open_per_profile=True,
                               max_open_trades_total=99, max_open_trades_per_profile=99)
    t1 = _open(_row(short=5815.0, long=5820.0), config=cfg, profile_id="A", profile_hash="hA")
    # different profile (different identity) but SAME strikes/side/expiry
    ok, reason, is_dup = lifecycle.can_open(
        identity="diff_profile_same_strikes", side="CALL_CREDIT", short_strike=5815.0,
        long_strike=5820.0, selected_expiry="2026-06-02", profile_id="B",
        open_trades=[t1], config=cfg)
    assert ok is False and reason == "duplicate_strikes_disallowed" and is_dup is False


# ── 7. TP exit closes + records realized P&L ─────────────────────────────────

def test_tp_exit_closes_and_records_pnl():
    cfg = PaperLifecycleConfig(take_profit_pct=0.50, exit_on_eod=False)
    t = _open(_row(), config=cfg)
    # re-price down to a 0.20 debit (<= 0.60 * 0.50 = 0.30)
    lifecycle.update_trade_mark(t, {"bid": 0.15, "ask": 0.25, "mid": 0.20, "available": True},
                                "2026-06-02T11:00:00-04:00")
    reason, debit = lifecycle.evaluate_exit(t, cfg, _at(11, 0))
    assert reason == "take_profit" and debit == 0.20
    lifecycle.close_trade(t, exit_reason=reason, exit_debit=debit, now_iso="2026-06-02T11:00:00-04:00")
    assert t.status == "closed"
    assert t.realized_pnl == 40.0        # (0.60 - 0.20) * 100 * 1


# ── 8. SL exit closes + records realized P&L ─────────────────────────────────

def test_sl_exit_closes_and_records_pnl():
    cfg = PaperLifecycleConfig(stop_loss_pct=1.50, exit_on_eod=False)
    t = _open(_row(), config=cfg)
    # re-price up to a 0.95 debit (>= 0.60 * 1.50 = 0.90)
    lifecycle.update_trade_mark(t, {"bid": 0.90, "ask": 1.00, "mid": 0.95, "available": True},
                                "2026-06-02T11:00:00-04:00")
    reason, debit = lifecycle.evaluate_exit(t, cfg, _at(11, 0))
    assert reason == "stop_loss" and debit == 0.95
    lifecycle.close_trade(t, exit_reason=reason, exit_debit=debit, now_iso="2026-06-02T11:00:00-04:00")
    assert t.status == "closed"
    assert t.realized_pnl == -35.0       # (0.60 - 0.95) * 100 * 1


# ── 9. EOD exit closes ───────────────────────────────────────────────────────

def test_eod_exit_closes():
    cfg = PaperLifecycleConfig(exit_on_eod=True, eod_exit_time="15:55")
    t = _open(_row(), config=cfg)
    reason, debit = lifecycle.evaluate_exit(t, cfg, _at(15, 56))
    assert reason == "eod_exit"
    lifecycle.close_trade(t, exit_reason=reason, exit_debit=debit, now_iso="2026-06-02T15:56:00-04:00")
    assert t.status == "closed" and t.exit_reason == "eod_exit"


# ── 10. quote unavailable updates without a false exit ───────────────────────

def test_quote_unavailable_updates_without_false_exit():
    cfg = PaperLifecycleConfig(exit_on_eod=False)
    # no leg quotes → entry mark is None
    t = _open(_row(short_bid="", short_ask="", short_mid="",
                   long_bid="", long_ask="", long_mid=""), config=cfg)
    assert t.current_mark is None
    reason = lifecycle.update_trade_mark(t, None, "2026-06-02T11:00:00-04:00")
    assert reason == "quote_unavailable"
    assert t.ticks_held == 1
    exit_reason, _ = lifecycle.evaluate_exit(t, cfg, _at(11, 0))
    assert exit_reason is None           # no mark + not EOD → hold, no false exit


# ── 11. open/closed ledgers write + read back ────────────────────────────────

def test_open_closed_ledgers_write(tmp_path):
    rd = tmp_path / "runs" / "R1"
    ld = tmp_path / "latest"
    t_open = _open(_row(), profile_id="A")
    t_closed = _open(_row(short=5830.0, long=5835.0), profile_id="B")
    t_closed.status = "closed"
    t_closed.realized_pnl = 12.5
    ledger.write_open_trades(rd, ld, [t_open])
    ledger.write_closed_trades(rd, ld, [t_closed])
    back_open = ledger._read_csv(rd / "paper_trades_open.csv")
    back_closed = ledger._read_csv(rd / "paper_trades_closed.csv")
    assert len(back_open) == 1 and back_open[0]["status"] == "open"
    assert len(back_closed) == 1 and back_closed[0]["realized_pnl"] == "12.5"


# ── 12. portfolio summary P&L math ───────────────────────────────────────────

def test_portfolio_summary_pnl():
    win = _open(_row(), profile_id="A")
    win.status = "closed"
    win.realized_pnl = 40.0
    loss = _open(_row(short=5830.0, long=5835.0), profile_id="B")
    loss.status = "closed"
    loss.realized_pnl = -35.0
    live = _open(_row(short=5840.0, long=5845.0), profile_id="C")
    live.unrealized_pnl = 5.0
    summ = ledger.compute_summary([live], [win, loss], max_open_trades_seen=3,
                                  duplicate_skipped_count=2, blocked_by_limits_count=1)
    assert summ["realized_pnl"] == 5.0           # 40 - 35
    assert summ["unrealized_pnl"] == 5.0
    assert summ["total_pnl"] == 10.0
    assert summ["wins"] == 1 and summ["losses"] == 1 and summ["win_rate"] == 0.5
    assert summ["open_trade_count"] == 1 and summ["closed_trade_count"] == 2
    assert summ["max_open_trades_seen"] == 3
    assert summ["duplicate_skipped_count"] == 2 and summ["blocked_by_limits_count"] == 1
    assert summ["no_execution"] is True


# ── 13. local reconciliation detects duplicates / missing / invalid ──────────

def test_reconcile_detects_issues(tmp_path):
    rd = tmp_path / "runs" / "RBAD"
    ld = tmp_path / "latest"
    # two OPEN trades sharing one identity (duplicate open identity)
    a = _open(_row(), profile_id="A")
    a.trade_identity = "IDENT_DUP"
    b = _open(_row(short=5830.0, long=5835.0), profile_id="A")
    b.trade_identity = "IDENT_DUP"
    ledger.write_open_trades(rd, ld, [a, b])
    # 'a' ALSO sits in the closed file (closed trade still in open file)
    a_closed = PaperTrade.from_row(a.to_row())
    a_closed.status = "closed"
    ledger.write_closed_trades(rd, ld, [a_closed])
    # events: open for 'a' only (so 'b' has no open event); a close for an un-opened id
    ledger.append_event(rd, ledger.make_event(event_type="open", timestamp="t", paper_trade_id=a.paper_trade_id, profile_id="A", reason="x", trade=None))
    ledger.append_event(rd, ledger.make_event(event_type="close", timestamp="t", paper_trade_id="pt_never_opened", profile_id="A", reason="x", trade=None))

    report = ledger.reconcile_run("RBAD", root=tmp_path)
    types = {i["type"] for i in report["issues"]}
    assert report["ok"] is False
    assert "duplicate_open_identity" in types
    assert "closed_trade_still_in_open_file" in types
    assert "open_trade_missing_open_event" in types
    assert "close_without_open" in types
    assert report["broker_position_reconciliation"] == "deferred"


def test_reconcile_clean_run_ok(tmp_path):
    rd = tmp_path / "runs" / "ROK"
    ld = tmp_path / "latest"
    a = _open(_row(), profile_id="A")
    ledger.write_open_trades(rd, ld, [a])
    ledger.write_closed_trades(rd, ld, [])
    ledger.append_event(rd, ledger.make_event(event_type="open", timestamp="t", paper_trade_id=a.paper_trade_id, profile_id="A", reason="x", trade=None))
    report = ledger.reconcile_run("ROK", root=tmp_path)
    assert report["ok"] is True and report["issues"] == []


# ── 14. run_portfolio_forward --once creates expected files ──────────────────

def test_run_portfolio_forward_once_creates_files(tmp_path):
    rc = run_portfolio.main([
        "--profiles", SCORE_BEST, "--once", "--interval-seconds", "0",
        "--output-dir", str(tmp_path / "pf"),
    ])
    assert rc == 0
    latest = tmp_path / "pf" / "latest"
    man = json.loads((latest / "portfolio_manifest.json").read_text())
    rd = tmp_path / "pf" / "runs" / man["portfolio_run_id"]
    for name in ("portfolio_manifest.json", "portfolio_tick_log.jsonl",
                 "profile_tick_log.jsonl", "paper_trades_open.csv",
                 "paper_trades_closed.csv", "paper_trade_events.jsonl",
                 "portfolio_summary.json", "heartbeat.json", "reconciliation_report.json"):
        assert (rd / name).is_file(), f"missing {name}"
    assert man["no_execution"] is True
    assert man["execution_mode"] == "local_paper_lifecycle_only"
    # score_best opens a CALL_CREDIT signal → at least one open event recorded
    events = [json.loads(x) for x in (rd / "paper_trade_events.jsonl").read_text().splitlines() if x.strip()]
    assert any(e["event_type"] == "open" for e in events)


# ── 15. --max-ticks 2 updates an existing open trade ─────────────────────────

def test_run_portfolio_forward_max_ticks_2_updates_open_trade(tmp_path):
    rc = run_portfolio.main([
        "--profiles", SCORE_BEST, "--max-ticks", "2", "--interval-seconds", "0",
        "--output-dir", str(tmp_path / "pf"), "--no-exit-on-eod",
    ])
    assert rc == 0
    man = json.loads((tmp_path / "pf" / "latest" / "portfolio_manifest.json").read_text())
    rd = tmp_path / "pf" / "runs" / man["portfolio_run_id"]
    open_rows = ledger._read_csv(rd / "paper_trades_open.csv")
    assert len(open_rows) == 1                      # opened tick 1, NOT re-opened tick 2
    assert int(open_rows[0]["ticks_held"]) == 2     # re-priced on both ticks
    summ = json.loads((rd / "portfolio_summary.json").read_text())
    assert summ["duplicate_skipped_count"] >= 1     # tick-2 re-selection was deduped


# ── 16. review_portfolio_forward commands ────────────────────────────────────

def test_review_portfolio_forward_commands(tmp_path, capsys):
    run_portfolio.main([
        "--profiles", SCORE_BEST, "--once", "--interval-seconds", "0",
        "--output-dir", str(tmp_path / "pf"), "--no-exit-on-eod",
    ])
    root = str(tmp_path / "pf")
    capsys.readouterr()
    assert review_portfolio.main(["--output-dir", root, "--latest"]) == 0
    assert "portfolio run" in capsys.readouterr().out
    assert review_portfolio.main(["--output-dir", root, "--list"]) == 0
    assert review_portfolio.main(["--output-dir", root, "--open", "latest"]) == 0
    assert review_portfolio.main(["--output-dir", root, "--closed", "latest"]) == 0
    assert review_portfolio.main(["--output-dir", root, "--events", "latest", "--limit", "5"]) == 0
    assert review_portfolio.main(["--output-dir", root, "--reconcile", "latest"]) == 0
    # missing run → exit 1
    assert review_portfolio.main(["--output-dir", root, "--run", "nope_zzz"]) == 1


# ── 17. no execution / order / preview surface ───────────────────────────────

def test_no_execution_surface():
    repo = Path(__file__).resolve().parents[1]
    files = (
        "src/paper/models.py", "src/paper/ledger.py", "src/paper/lifecycle.py",
        "scripts/run_portfolio_forward.py", "scripts/review_portfolio_forward.py",
    )
    forbidden = ("submit_order", "place_order", "preview_order", "create_order",
                 "broker.", "execute_trade", "order_preview")
    for rel in files:
        src = (repo / rel).read_text(encoding="utf-8")
        for tok in forbidden:
            assert tok not in src, f"{rel} must not reference {tok!r}"
