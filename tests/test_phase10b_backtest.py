"""Phase 10B — historical replay runner: candidate construction, selector reuse,
lifecycle exit simulation, reporting aggregation, CLI smoke, repo-local outputs,
and the no-broker/no-execution guarantee.

Uses a SYNTHETIC trading root + direct DayIndex fixtures — no dependence on
Dan's real disk data. The real committed profiles drive behavior so the tests
exercise the same field-derived knobs the live operator uses.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import scripts.backtest_run as cli
from src.backtesting import reports
from src.backtesting.lifecycle_sim import DayIndex, simulate_exit
from src.backtesting.profile_runtime import derive_run_settings, threshold_scheme
from src.backtesting.replay_runner import resolve_profiles, run_backtest
from src.config.strategy_profiles import load_profile_file

_ENTRY = datetime(2026, 6, 2, 11, 0, 0)
_REPO = Path(__file__).resolve().parents[1]


def _repo_latest_fingerprint() -> list[tuple[str, int, int]]:
    latest = _REPO / "outputs" / "backtests" / "latest"
    if not latest.exists():
        return []
    return sorted(
        (str(p.relative_to(latest)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in latest.rglob("*")
        if p.is_file()
    )


# ── synthetic raw data (valid corridor 7570 < 7585 < 7600; both wings 5k/10k) ─

def _csv(date: str = "2026-06-02") -> str:
    h = ("timestamp,session,SPX_Spot,Strike,CALL Volume,PUT Volume,"
         "CALL BID,CALL ASK,PUT BID,PUT ASK")
    rows = [h]

    def snap(ts: str, spot: float, grid: list[tuple]) -> None:
        for (k, cv, pv, cb, ca, pb, pa) in grid:
            rows.append(f"{ts},RTH,{spot},{k},{cv},{pv},{cb},{ca},{pb},{pa}")

    # 11:00 entry: call_floor 5k/10k = 7570, put_ceiling 5k/10k = 7600.
    # Premiums sized so the credit (1.2 on a 5-wide) clears the live 0.60 score
    # threshold AND stays under the planned-risk cap ($1000 = 10% of $10k at the
    # 5-contract aggressive_paper_10k profile: 1.2 × 1.5 × 100 × 5 = $900).
    snap(f"{date} 11:00:00", 7585, [
        (7565, 500, 400, 30.0, 30.4, 0.95, 1.05),     # PUT_CREDIT long put (mid 1.0)
        (7570, 15000, 500, 25.0, 25.4, 2.15, 2.25),   # call_floor; PUT short put (mid 2.2)
        (7600, 800, 12000, 2.15, 2.25, 9.0, 9.4),     # put_ceiling; CALL short call (mid 2.2)
        (7605, 700, 800, 0.95, 1.05, 14.0, 14.4),     # CALL_CREDIT long call (mid 1.0)
    ])
    # 11:30 post-entry reprice (debit ~1.2 → no TP/SL trigger).
    snap(f"{date} 11:30:00", 7585, [
        (7565, 500, 400, 28.0, 28.4, 0.75, 0.85),
        (7570, 15000, 500, 23.0, 23.4, 1.95, 2.05),
        (7600, 800, 12000, 1.95, 2.05, 9.0, 9.4),
        (7605, 700, 800, 0.75, 0.85, 14.0, 14.4),
    ])
    # 16:01 settlement (spot unchanged → both spreads expire worthless = win).
    snap(f"{date} 16:01:00", 7585, [
        (7565, 500, 400, 20.0, 20.4, 0.0, 0.1),
        (7570, 15000, 500, 15.0, 15.4, 0.0, 0.1),
        (7600, 800, 12000, 0.0, 0.1, 15.0, 15.4),
        (7605, 700, 800, 0.0, 0.1, 20.0, 20.4),
    ])
    return "\n".join(rows) + "\n"


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "Trading"
    d = root / "TOS Data" / "Daily Exposures" / "SPX"
    d.mkdir(parents=True, exist_ok=True)
    for date in ("2026-06-01", "2026-06-02"):
        (d / f"SPX_RAW_{date}.csv").write_text(_csv(date), encoding="utf-8")
    return root


def _di(points: list[tuple], *, spots_present: bool = True) -> DayIndex:
    """Build a DayIndex from (hh, mm, {strike:(call_mid,put_mid)}, spot) rows."""
    ts_list, mids, spots = [], {}, {}
    for (hh, mm, table, spot) in points:
        t = datetime(2026, 6, 2, hh, mm, 0)
        ts_list.append(t)
        mids[t] = table
        spots[t] = spot if spots_present else None
    return DayIndex(timestamps=sorted(ts_list), mids=mids, spots=spots)


# ── profile derivation (field-driven, not name-driven) ───────────────────────

def test_profile_derivation_from_fields():
    prof = load_profile_file("morning_5k_dynamic_tp75").profile
    s = derive_run_settings(prof)
    assert s.entry_target == "11:00"
    assert s.volume_threshold == 5000.0 and s.threshold_label == "5k"
    assert s.allow_call_credit and s.allow_put_credit
    assert s.selector_mode == "balanced_structure_premium_valid"
    assert s.take_profit_capture == 0.75 and s.take_profit_label == "TP75"
    assert s.stop_loss_loss == 1.50 and s.stop_loss_label == "SL150"
    assert s.target_dte == 0

    eod = derive_run_settings(load_profile_file("eod_5k_dynamic_sl200_no_tp").profile)
    assert eod.entry_target == "15:15" and eod.take_profit_capture is None
    assert eod.stop_loss_label == "SL200"


def test_threshold_scheme_flags_spy_qqq_provisional():
    assert threshold_scheme("SPX") == ("spx_2k5k10k_standard", None)
    scheme, warn = threshold_scheme("QQQ")
    assert scheme == "provisional_spx_2k5k10k" and warn and "PROVISIONAL" in warn


def test_resolve_profiles_cohorts():
    assert resolve_profiles("all-main") == [
        "morning_5k_dynamic_tp75", "morning_2k_dynamic_no_tp",
        "eod_5k_dynamic_sl150_no_tp", "eod_5k_dynamic_sl200_no_tp"]
    assert len(resolve_profiles("all-main", include_controls=True)) == 10
    assert resolve_profiles("regime_put_credit_test") == ["regime_put_credit_test"]


# ── candidate construction + selector reuse (synthetic root, real profiles) ──

def test_candidate_construction_both_sides_dynamic(tmp_path):
    root = _make_root(tmp_path)
    res = run_backtest(symbol="SPX", profile_ids=["morning_5k_dynamic_tp75"],
                       trading_root=str(root), dte=0)
    sides = {c["side"] for c in res.candidates}
    assert sides == {"CALL_CREDIT", "PUT_CREDIT"}      # dynamic evaluates BOTH sides
    call = next(c for c in res.candidates if c["side"] == "CALL_CREDIT")
    put = next(c for c in res.candidates if c["side"] == "PUT_CREDIT")
    # CALL_CREDIT short at PUT_CEILING (7600), long one strike higher (7605)
    assert call["short_strike"] == 7600.0 and call["long_strike"] == 7605.0
    assert call["entry_credit_points"] == 1.2
    # PUT_CREDIT short at CALL_FLOOR (7570), long one strike lower (7565)
    assert put["short_strike"] == 7570.0 and put["long_strike"] == 7565.0
    # selected trades are a subset, all flagged selected
    assert res.trades and all(t["selected_trade"] for t in res.trades)
    assert all(c["corridor_valid"] is True for c in res.candidates)


def test_call_only_control_excludes_puts(tmp_path):
    root = _make_root(tmp_path)
    res = run_backtest(symbol="SPX", profile_ids=["morning_5k_call_tp75_control"],
                       trading_root=str(root), dte=0)
    assert res.trades and all(t["side"] == "CALL_CREDIT" for t in res.trades)
    put = next(c for c in res.candidates if c["side"] == "PUT_CREDIT")
    assert put["selected_trade"] is False


def test_put_only_regime_excludes_calls(tmp_path):
    root = _make_root(tmp_path)
    res = run_backtest(symbol="SPX", profile_ids=["regime_put_credit_test"],
                       trading_root=str(root), dte=0)
    assert res.trades and all(t["side"] == "PUT_CREDIT" for t in res.trades)
    call = next(c for c in res.candidates if c["side"] == "CALL_CREDIT")
    assert call["selected_trade"] is False


def test_observe_profile_selects_no_trades(tmp_path):
    root = _make_root(tmp_path)
    res = run_backtest(symbol="SPX", profile_ids=["observe_dynamic_5k"],
                       trading_root=str(root), dte=0)
    assert res.candidates                       # candidates are still logged …
    assert res.trades == []                      # … but nothing is selected
    assert all(not c["selected_trade"] for c in res.candidates)
    assert res.no_trade_reasons
    row = res.no_trade_reasons[0]
    for key in ("entry_target", "candidate_count", "eligible_candidate_count",
                "selector_filtered_count", "first_blocker", "top_selector_reason"):
        assert key in row
    assert row["candidate_count"] > 0
    for c in res.candidates:
        assert "candidate_passes_risk_filters" in c
        assert "candidate_passes_quote_filters" in c
        assert "selector_blockers" in c


# ── lifecycle exit simulation (TP75 / TP50 / SL150 / SL200 / EOD / missing) ──

def test_tp75_exit():
    day = _di([(11, 5, {7600: (0.30, 0), 7605: (0.10, 0)}, 7588),    # debit 0.20
               (16, 1, {7600: (0.0, 0), 7605: (0.0, 0)}, 7585)])
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=1.0,
                      take_profit_capture=0.75, stop_loss_loss=1.50)
    assert r.exit_reason == "TP" and r.tp_triggered and not r.stop_triggered
    assert r.exit_debit_points == 0.20 and r.pnl_points == 0.80     # debit <= 0.25*credit


def test_tp50_exit():
    day = _di([(11, 5, {7600: (0.40, 0), 7605: (0.0, 0)}, 7588),     # debit 0.40
               (16, 1, {7600: (0.0, 0), 7605: (0.0, 0)}, 7585)])
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=1.0,
                      take_profit_capture=0.50, stop_loss_loss=1.50)
    assert r.exit_reason == "TP" and r.pnl_points == 0.60           # debit <= 0.50*credit


def test_sl150_exit():
    day = _di([(11, 5, {7600: (3.00, 0), 7605: (0.40, 0)}, 7595),    # debit 2.60
               (16, 1, {7600: (0.0, 0), 7605: (0.0, 0)}, 7585)])
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=1.0,
                      take_profit_capture=None, stop_loss_loss=1.50)
    assert r.exit_reason == "SL" and r.stop_triggered                # debit >= 2.5*credit
    assert r.exit_debit_points == 2.60 and r.pnl_points == -1.60


def test_sl200_exit():
    day = _di([(11, 5, {7600: (3.20, 0), 7605: (0.10, 0)}, 7596),    # debit 3.10
               (16, 1, {7600: (0.0, 0), 7605: (0.0, 0)}, 7585)])
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=1.0,
                      take_profit_capture=None, stop_loss_loss=2.00)
    assert r.exit_reason == "SL" and r.pnl_points == -2.10           # debit >= 3.0*credit


def test_eod_exit_cash_settle_intrinsic():
    # No TP/SL configured; settle at 16:01 to intrinsic. Spot 7585 < short 7600 →
    # spread expires worthless = full-credit win.
    day = _di([(11, 30, {7600: (0.80, 0), 7605: (0.40, 0)}, 7585),
               (16, 1, {7600: (0.0, 0), 7605: (0.0, 0)}, 7585)])
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=0.50,
                      take_profit_capture=None, stop_loss_loss=None)
    assert r.exit_reason == "EOD" and r.exit_debit_points == 0.0
    assert r.pnl_points == 0.50 and r.settlement_method == "post_1600_cash_settle_proxy"


def test_eod_exit_loss_when_itm():
    # Spot 7602 at settle → CALL spread 2 pts ITM → intrinsic debit 2.0 → loss.
    day = _di([(11, 30, {7600: (0.80, 0), 7605: (0.40, 0)}, 7585),
               (16, 1, {7600: (3.0, 0), 7605: (1.0, 0)}, 7602)])
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=0.50,
                      take_profit_capture=None, stop_loss_loss=None)
    assert r.exit_reason == "EOD" and r.exit_debit_points == 2.0 and r.pnl_points == -1.50


def test_missing_prices_counted():
    day = _di([(11, 5, {7600: (None, 0), 7605: (None, 0)}, 7588),    # call mids missing
               (12, 0, {}, 7588),                                     # no strikes at all
               (16, 1, {7600: (0.0, 0), 7605: (0.0, 0)}, 7585)])
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=1.0,
                      take_profit_capture=0.75, stop_loss_loss=1.50)
    assert r.missing_price_count == 2 and r.snapshots_checked == 1


def test_unpriceable_trade_is_skipped():
    day = _di([(11, 5, {}, None)], spots_present=False)    # nothing priceable, no spot
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=1.0,
                      take_profit_capture=0.75, stop_loss_loss=1.50)
    assert r.exit_reason == "SKIPPED" and r.pnl_points is None


def test_sl_wins_tp_conflict_on_same_snapshot():
    # debit 0.10 hits TP75 (<=0.25) AND a (contrived) SL with loss=-0.95 (>=0.05) → SL wins.
    day = _di([(11, 5, {7600: (0.10, 0), 7605: (0.0, 0)}, 7585),
               (16, 1, {7600: (0.0, 0), 7605: (0.0, 0)}, 7585)])
    r = simulate_exit(day, entry_ts=_ENTRY, side="CALL_CREDIT", short_strike=7600,
                      long_strike=7605, entry_credit_points=1.0,
                      take_profit_capture=0.75, stop_loss_loss=-0.95)
    assert r.event_conflict and r.exit_reason == "SL"


# ── reporting aggregation ────────────────────────────────────────────────────

def _trade(date, *, pid="p1", pnl=10.0, corridor=True, tier=1, side="CALL_CREDIT",
           reason="EOD", ts=None):
    return {"date": date, "profile_id": pid, "symbol": "SPX", "pnl_dollars": pnl,
            "entry_timestamp": ts or f"{date}T11:00:00", "corridor_valid": corridor,
            "wds_tier": tier, "side": side, "exit_reason": reason,
            "entry_credit_points": 0.5, "max_risk_points": 4.5,
            "distance_from_spot_to_short": 15.0, "hold_minutes": 60,
            "selected_trade": True}


def test_daily_pnl_aggregation():
    trades = [_trade("2026-06-01", pnl=40.0), _trade("2026-06-01", pnl=-85.0),
              _trade("2026-06-02", pnl=50.0)]
    rows = reports.daily_pnl(trades)
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-06-01"]["trades"] == 2
    assert by_date["2026-06-01"]["pnl_dollars"] == -45.0
    assert by_date["2026-06-02"]["cum_pnl_dollars"] == 5.0    # -45 + 50


def test_equity_curve_and_drawdown():
    trades = [_trade("2026-06-01", pnl=100.0, ts="2026-06-01T11:00:00"),
              _trade("2026-06-02", pnl=-50.0, ts="2026-06-02T11:00:00"),
              _trade("2026-06-03", pnl=-80.0, ts="2026-06-03T11:00:00"),
              _trade("2026-06-04", pnl=30.0, ts="2026-06-04T11:00:00")]
    m = reports.metrics(trades)
    assert m["total_pnl_dollars"] == 0.0
    assert m["max_drawdown_dollars"] == 130.0          # peak 100 → trough -30
    assert m["max_drawdown_duration_trades"] == 3       # 3 trades stay below the peak
    curve = reports.equity_curve(trades)
    assert curve[-1]["cum_pnl_dollars"] == 0.0 and curve[2]["drawdown_dollars"] == 130.0


def test_account_metrics_starting_balance_return_and_drawdown_pct():
    trades = [_trade("2026-06-01", pnl=100.0, ts="2026-06-01T11:00:00"),
              _trade("2026-06-02", pnl=-400.0, ts="2026-06-02T11:00:00"),
              _trade("2026-06-03", pnl=50.0, ts="2026-06-03T11:00:00")]
    m = reports.metrics(trades, starting_balance=1000.0, contracts=2)
    assert m["starting_balance"] == 1000.0
    assert m["contracts"] == 2
    assert m["ending_balance"] == 750.0
    assert m["return_pct"] == -25.0
    assert m["max_drawdown_dollars"] == 400.0
    assert m["max_drawdown_pct"] == 36.3636       # peak 1100 → trough 700
    curve = reports.equity_curve(trades, starting_balance=1000.0, contracts=2)
    assert curve[0]["account_equity"] == 1100.0
    assert curve[0]["peak_dollars"] == 1100.0
    assert curve[1]["drawdown_pct"] == 36.3636


def test_summary_by_corridor_valid_vs_invalid():
    trades = [_trade("2026-06-01", corridor=True, pnl=40.0),
              _trade("2026-06-02", corridor=False, pnl=-20.0)]
    cands = [*trades, _trade("2026-06-03", corridor=False, pnl=0.0)]
    rows = {r["corridor_valid"]: r for r in reports.summary_by_corridor(trades, cands)}
    assert rows[True]["selected_trades"] == 1 and rows[True]["total_pnl_dollars"] == 40.0
    assert rows[False]["selected_trades"] == 1 and rows[False]["candidates"] == 2


def test_summary_by_wds_tier():
    trades = [_trade("2026-06-01", tier=1, pnl=40.0), _trade("2026-06-02", tier=2, pnl=-20.0)]
    rows = {r["wds_tier"]: r for r in reports.summary_by_wds_tier(trades, trades)}
    assert rows[1]["total_pnl_dollars"] == 40.0 and rows[2]["total_pnl_dollars"] == -20.0


def test_metrics_profit_factor_and_winrate():
    trades = [_trade("d", pnl=100.0, reason="TP"), _trade("d", pnl=-50.0, reason="SL")]
    m = reports.metrics(trades)
    assert m["win_rate"] == 0.5 and m["profit_factor"] == 2.0
    assert m["tp_count"] == 1 and m["sl_count"] == 1


def test_metrics_include_explainability_breakdowns():
    trades = [
        _trade("2026-06-01", pnl=100.0, side="CALL_CREDIT", reason="TP", tier=1),
        _trade("2026-06-02", pnl=-50.0, side="PUT_CREDIT", reason="SL", tier=2,
               corridor=False),
        _trade("2026-06-03", pnl=-20.0, side="PUT_CREDIT", reason="EOD", tier=2,
               corridor=False),
    ]
    m = reports.metrics(trades, starting_balance=1000.0, contracts=1)
    assert m["avg_win_dollars"] == 100.0
    assert m["avg_loss_dollars"] == -35.0
    assert m["largest_win_dollars"] == 100.0
    assert m["largest_loss_dollars"] == -50.0
    assert m["avg_hold_minutes"] == 60.0
    assert m["max_consecutive_losses"] == 2
    assert m["best_day"] == "2026-06-01" and m["best_day_pnl_dollars"] == 100.0
    assert m["worst_day"] == "2026-06-02" and m["worst_day_pnl_dollars"] == -50.0
    assert m["call_pnl_dollars"] == 100.0
    assert m["put_pnl_dollars"] == -70.0
    assert m["inactive_corridor_pnl_dollars"] == -70.0
    assert m["wds_tier2_pnl_dollars"] == -70.0
    assert reports.summary_by_side(trades, trades)[0]["side"] in {"CALL_CREDIT", "PUT_CREDIT"}
    assert {r["exit_reason"] for r in reports.summary_by_exit_reason(trades)} == {"TP", "SL", "EOD"}


# ── CLI smoke + repo-local outputs ───────────────────────────────────────────

def test_cli_smoke_outputs_temp_latest_not_repo_latest(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    before = _repo_latest_fingerprint()
    root = _make_root(tmp_path)
    output_root = tmp_path / "isolated_outputs"
    rc = cli.main(["--symbol", "SPX", "--profile", "morning_5k_dynamic_tp75",
                   "--run-label", "pytest", "--trading-root", str(root),
                   "--output-root", str(output_root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "trades selected" in out and "no broker" in out.lower()
    assert "outputs" in out and "backtests" in out
    assert str(root) not in out and "TOS Data" not in out
    assert _repo_latest_fingerprint() == before
    latest = output_root / "backtests" / "latest"
    written = latest / "trades.csv"
    assert written.is_file()
    header = written.read_text(encoding="utf-8").splitlines()[0]
    for col in ("contracts", "pnl_dollars", "exit_reason", "corridor_valid", "wds_tier"):
        assert col in header
    for name in ("candidates", "daily_pnl", "equity_curve", "summary_by_profile",
                 "summary_by_side", "summary_by_exit_reason", "summary_by_corridor",
                 "summary_by_wds_tier", "summary_by_day", "no_trade_reasons"):
        assert (latest / f"{name}.csv").is_file()
    no_trade_header = (latest / "no_trade_reasons.csv").read_text(
        encoding="utf-8").splitlines()[0]
    for col in ("entry_target", "candidate_count", "first_blocker", "top_quote_reason"):
        assert col in no_trade_header
    cfg = json.loads((latest / "run_config.json").read_text(encoding="utf-8"))
    assert cfg["starting_balance"] == 10000.0
    assert cfg["contracts"] == 1
    assert cfg["overall"]["ending_balance"] == 10000.0 + cfg["overall"]["total_pnl_dollars"]


def test_cli_custom_sizing_written_to_run_config(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    root = _make_root(tmp_path)
    output_root = tmp_path / "custom_outputs"
    rc = cli.main(["--symbol", "SPX", "--profile", "morning_5k_dynamic_tp75",
                   "--run-label", "pytest_custom", "--trading-root", str(root),
                   "--output-root", str(output_root),
                   "--starting-balance", "2500", "--contracts", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "starting_balance=$2,500.00" in out and "contracts=5" in out
    cfg = json.loads(
        (output_root / "backtests" / "latest" / "run_config.json").read_text(encoding="utf-8"))
    assert cfg["starting_balance"] == 2500.0
    assert cfg["contracts"] == 5
    assert cfg["overall"]["contracts"] == 5
    assert cfg["overall"]["return_pct"] == round(
        cfg["overall"]["total_pnl_dollars"] / 2500.0 * 100.0, 4)


def test_contracts_scale_pnl_without_changing_selection(tmp_path):
    root = _make_root(tmp_path)
    one = run_backtest(symbol="SPX", profile_ids=["morning_5k_dynamic_tp75"],
                       trading_root=str(root), dte=0,
                       starting_balance=10000.0, contracts=1)
    five = run_backtest(symbol="SPX", profile_ids=["morning_5k_dynamic_tp75"],
                        trading_root=str(root), dte=0,
                        starting_balance=10000.0, contracts=5)
    one_key = [(t["date"], t["profile_id"], t["side"], t["short_strike"], t["long_strike"])
               for t in one.trades]
    five_key = [(t["date"], t["profile_id"], t["side"], t["short_strike"], t["long_strike"])
                for t in five.trades]
    assert one_key == five_key
    assert all(t["contracts"] == 1 for t in one.trades)
    assert all(t["contracts"] == 5 for t in five.trades)
    one_total = sum(t["pnl_dollars"] for t in one.trades)
    five_total = sum(t["pnl_dollars"] for t in five.trades)
    assert round(five_total, 2) == round(one_total * 5, 2)
    m = reports.metrics(five.trades, starting_balance=10000.0, contracts=5)
    assert m["ending_balance"] == round(10000.0 + five_total, 2)
    assert m["return_pct"] == round(five_total / 10000.0 * 100.0, 4)
    curve = reports.equity_curve(five.trades, starting_balance=10000.0, contracts=5)
    assert curve[0]["starting_balance"] == 10000.0
    assert curve[0]["account_equity"] == round(10000.0 + five.trades[0]["pnl_dollars"], 2)
    assert five.run_config["starting_balance"] == 10000.0
    assert five.run_config["contracts"] == 5


# ── no broker / no execution / no hardcoded user ─────────────────────────────

# Execution surfaces that must never appear in the backtesting code. (Provider
# NAMES like "tastytrade" appear only in "no tastytrade" prose, so they are not
# grep-forbidden; the structural guarantee is the import check below.)
_FORBIDDEN = (
    "submit_order", "place_order", "preview_order", "create_order", "order_preview",
    "execute_trade", "broker.",
)

_BACKTEST_SOURCES = (
    "src/backtesting/replay_runner.py", "src/backtesting/lifecycle_sim.py",
    "src/backtesting/reports.py", "src/backtesting/replay_providers.py",
    "src/backtesting/profile_runtime.py", "scripts/backtest_run.py",
)


def test_no_execution_or_live_calls():
    repo = Path(__file__).resolve().parents[1]
    for rel in _BACKTEST_SOURCES:
        text = (repo / rel).read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN:
            assert tok not in text, f"{rel} contains {tok!r}"
    # Structural: the runner never IMPORTS a live provider / broker / HTTP client.
    runner = (repo / "src/backtesting/replay_runner.py").read_text(encoding="utf-8")
    for banned in ("import httpx", "tastytrade_provider", "zerosigma_api",
                   "build_quote_provider", "build_structure_provider"):
        assert banned not in runner, f"replay_runner imports {banned!r}"


def test_no_hardcoded_windows_username():
    repo = Path(__file__).resolve().parents[1]
    for rel in ("src/backtesting/replay_runner.py", "src/backtesting/lifecycle_sim.py",
                "src/backtesting/reports.py", "src/backtesting/replay_providers.py",
                "src/backtesting/profile_runtime.py", "scripts/backtest_run.py"):
        low = (repo / rel).read_text(encoding="utf-8").lower()
        assert r"c:\users" not in low and "c:/users/" not in low and "danca" not in low
