"""Phase 10E strategy comparison reports, CLI, and Backtests UI."""

from __future__ import annotations

import json
from pathlib import Path

import scripts.backtest_compare as cli
from src.app import cockpit_helpers as ch
from src.app import operator_mode as om
from src.backtesting import comparison as comp
from src.backtesting.replay_runner import BacktestResult
from tests.test_phase10b_backtest import _make_root

_REPO = Path(__file__).resolve().parents[1]
_UI_SOURCE = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def _trade(
    profile_id: str,
    date: str,
    pnl: float,
    *,
    side: str = "CALL_CREDIT",
    exit_reason: str = "EOD",
    corridor: bool = True,
    wds_tier: int = 2,
) -> dict:
    return {
        "profile_id": profile_id,
        "date": date,
        "symbol": "SPX",
        "dte": "0DTE",
        "entry_target": "11:00",
        "entry_timestamp": f"{date}T11:00:00",
        "pnl_dollars": pnl,
        "contracts": 1,
        "side": side,
        "exit_reason": exit_reason,
        "corridor_valid": corridor,
        "wds_tier": wds_tier,
        "entry_credit_points": 0.75,
        "hold_minutes": 45,
        "selected_trade": True,
    }


def _result() -> BacktestResult:
    dynamic = "morning_5k_dynamic_tp75"
    control = "morning_5k_call_tp75_control"
    trades = [
        *[_trade(dynamic, f"2026-05-{day:02d}", 100.0 if day % 3 else -50.0,
                 side="PUT_CREDIT" if day % 2 else "CALL_CREDIT",
                 exit_reason="TP" if day % 3 else "SL")
          for day in range(1, 13)],
        *[_trade(control, f"2026-05-{day:02d}", 40.0 if day % 2 else -60.0,
                 corridor=day % 2 == 1, wds_tier=1)
          for day in range(1, 13)],
    ]
    candidates = [{**trade, "selected_trade": True} for trade in trades]
    return BacktestResult(
        run_config={
            "symbol": "SPX",
            "dte": "0DTE",
            "profiles": [dynamic, control],
            "starting_balance": 10000.0,
            "contracts": 1,
            "run_label": "pytest_compare",
            "no_broker": True,
            "no_execution": True,
        },
        trades=trades,
        candidates=candidates,
        dates_evaluated=[f"2026-05-{day:02d}" for day in range(1, 13)],
        counters={"dates_evaluated": 12, "selected_trades": len(trades)},
    )


def _fingerprint(path: Path) -> list[tuple[str, int, int]]:
    if not path.exists():
        return []
    return sorted(
        (str(file.relative_to(path)), file.stat().st_size, file.stat().st_mtime_ns)
        for file in path.rglob("*")
        if file.is_file()
    )


def test_comparison_profile_groups_include_dynamic_controls_custom():
    all_main = comp.resolve_comparison_profiles("all-main")
    assert len(all_main) == 8
    assert all_main[:4] == list(comp.PRIMARY_PROFILES)
    assert all_main[4:] == list(comp.MAIN_CONTROL_PROFILES)
    assert comp.resolve_comparison_profiles("dynamic-only") == list(comp.PRIMARY_PROFILES)
    assert comp.resolve_comparison_profiles("controls-only") == list(comp.MAIN_CONTROL_PROFILES)
    assert comp.resolve_comparison_profiles(["morning_5k_dynamic_tp75,regime_put_credit_test"]) == [
        "morning_5k_dynamic_tp75", "regime_put_credit_test"
    ]


def test_ranking_score_and_promotion_labels_are_deterministic():
    row = {
        "starting_balance": 10000,
        "expectancy_dollars": 25,
        "profit_factor": 1.5,
        "return_pct": 8,
        "total_trades": 20,
        "max_drawdown_pct": 5,
        "max_consecutive_losses": 2,
    }
    assert comp.ranking_score(row) == comp.ranking_score(dict(row))
    parts = comp.ranking_components(row)
    assert set(parts) == {
        "rank_expectancy_component", "rank_profit_factor_component",
        "rank_return_component", "rank_trade_count_component",
        "rank_low_trade_penalty", "rank_drawdown_penalty", "rank_loss_streak_penalty",
    }
    assert comp.promotion_label(row, profile_kind="dynamic")[0] == (
        "Promote to Live Paper Candidate"
    )
    assert comp.promotion_label({**row, "total_trades": 3}, profile_kind="dynamic")[0] == (
        "Needs More Data"
    )
    assert comp.promotion_label(row, profile_kind="control")[0] == (
        "Control Positive / Comparison Only"
    )
    assert comp.promotion_label(
        {**row, "expectancy_dollars": -1}, profile_kind="dynamic"
    )[0] == "Watchlist / Needs Tuning"


def test_comparison_reports_include_rankings_breakdowns_and_narrative():
    tables = comp.build_comparison_reports(_result())
    assert len(tables["profile_rankings"]) == 2
    assert tables["profile_rankings"][0]["rank"] == 1
    assert {row["profile_group"] for row in tables["dynamic_vs_control"]} == {
        "dynamic", "control"
    }
    assert tables["by_side"] and tables["by_corridor"] and tables["by_wds_tier"]
    assert tables["by_entry_window"]
    assert "strongest expectancy" in tables["narrative"]
    assert "Dynamic profiles" in tables["narrative"]
    dynamic = next(
        row for row in tables["profile_rankings"]
        if row["profile_id"] == "morning_5k_dynamic_tp75"
    )
    for key in (
        "win_rate", "total_pnl_dollars", "return_pct", "max_drawdown_dollars",
        "max_drawdown_pct", "profit_factor", "expectancy_dollars", "avg_win_dollars",
        "avg_loss_dollars", "largest_win_dollars", "largest_loss_dollars",
        "avg_credit_points", "avg_hold_minutes", "tp_count", "sl_count", "eod_count",
        "call_selected", "call_pnl_dollars", "put_selected", "put_pnl_dollars",
        "active_corridor_trades", "active_corridor_pnl_dollars",
        "max_consecutive_losses", "best_day", "worst_day", "promotion_status",
    ):
        assert key in dynamic


def test_comparison_writer_and_reader(tmp_path):
    out = tmp_path / "comparison"
    comp.write_comparison_reports(_result(), [out], stamp="pytest")
    for name in (
        "comparison_summary.csv", "profile_rankings.csv", "dynamic_vs_control.csv",
        "by_profile.csv", "by_side.csv", "by_exit_reason.csv", "by_corridor.csv",
        "by_wds_tier.csv", "by_entry_window.csv", "run_config.json",
        "narrative_summary.md", "trades.csv",
    ):
        assert (out / name).is_file()
    cfg = json.loads((out / "run_config.json").read_text(encoding="utf-8"))
    assert cfg["comparison"] is True
    assert cfg["no_execution"] is True and cfg["no_order_preview"] is True
    read = ch.read_backtest_comparison(out)
    assert read["available"] is True
    assert len(read["rankings"]) == 2
    assert "strongest expectancy" in read["narrative"]


def test_comparison_cli_writes_temp_outputs_not_repo_latest(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    repo_latest = _REPO / "outputs" / "backtests" / "comparisons" / "latest"
    before = _fingerprint(repo_latest)
    output_root = tmp_path / "isolated_outputs"
    rc = cli.main([
        "--symbol", "SPX",
        "--profiles", "all-main",
        "--latest-days", "1",
        "--run-label", "pytest_compare",
        "--trading-root", str(_make_root(tmp_path)),
        "--output-root", str(output_root),
    ])
    output = capsys.readouterr().out
    assert rc == 0
    assert "research-only" in output.lower()
    assert _fingerprint(repo_latest) == before
    latest = output_root / "backtests" / "comparisons" / "latest"
    assert (latest / "profile_rankings.csv").is_file()
    assert (latest / "dynamic_vs_control.csv").is_file()


def test_compare_command_and_ui_wiring():
    command = om.backtest_compare_command("SPX", "all-main", 20, 0, "compare_smoke", 10000, 1)
    assert command == (
        "python -m scripts.backtest_compare --symbol SPX --profiles all-main "
        "--dte 0 --run-label compare_smoke --starting-balance 10000 --contracts 1 "
        "--latest-days 20"
    )
    for text in (
        "Compare Strategies", "Run Comparison", "Refresh Latest Comparison",
        "Main Dynamic", "Controls", "All Main", "Custom", "Selected profiles",
        "Best Expectancy", "Best Drawdown", "Best Profit Factor", "Best Return",
        "Dynamic vs Control", "Corridor Impact", "WDS Tier Impact",
        "Trade Logs by Profile", "ch.read_backtest_comparison(",
    ):
        assert text in _UI_SOURCE


def test_comparison_sources_introduce_no_execution_or_live_api_paths():
    forbidden = (
        "submit_order", "place_order", "preview_order", "create_order",
        "order_preview(", "execute_trade", "tastytrade_provider", "import httpx",
    )
    for relative in ("src/backtesting/comparison.py", "scripts/backtest_compare.py"):
        text = (_REPO / relative).read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{relative} contains forbidden token {token!r}"
