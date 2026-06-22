from __future__ import annotations

import ast
import json
from pathlib import Path

from scripts import diagnose_rth_soak_readiness as readiness_cli
from scripts import review_rth_soak as review_cli
from src.reviews.rth_soak import (
    build_rth_soak_review,
    sample_fixture_review,
    summarize_alerts,
    summarize_greeks,
    summarize_paper_trades,
    write_rth_soak_review,
)

REPO = Path(__file__).resolve().parents[1]


def _ready_quote() -> dict:
    return {
        "quote_provider": "tastytrade",
        "configured": True,
        "auth_mode": "oauth",
        "resolved_root": "SPXW",
        "resolved_expiration": "2026-06-22",
        "chain_returned": True,
        "quote_count": 10,
        "validation_passed_count": 10,
        "validation_failed_count": 0,
        "validation_blockers": {},
        "missing_strikes": [],
        "blocker": None,
        "final_status": "quotes ready",
    }


def _greek_probe(*, available: list[str], missing: list[str]) -> dict:
    return {
        "provider": "zerosigma_api",
        "resolved_provider": "zerosigma_api",
        "configured": True,
        "status": "ok",
        "available_metrics": available,
        "missing_metrics": missing,
        "endpoints": {
            "/api/v1/market/snapshot": {
                "status": "ok", "source": "/api/v1/market/snapshot"
            }
        },
    }


def test_readiness_cli_fixture_is_sanitized_and_writes_reports(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TASTY_CLIENT_SECRET", "DO_NOT_PRINT_SECRET")
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "DO_NOT_PRINT_PUSH")
    rc = readiness_cli.main([
        "--profile", "morning_5k_call_tp75_control", "--symbol", "SPX",
        "--dte", "0", "--fixture", "sample", "--json",
        "--output-dir", str(tmp_path),
    ])
    output = capsys.readouterr().out
    assert rc == 0
    assert "DO_NOT_PRINT_SECRET" not in output and "DO_NOT_PRINT_PUSH" not in output
    report = json.loads((tmp_path / "latest" / "rth_soak_readiness.json").read_text())
    assert report["sanitized"] is True and report["can_start"] is True
    assert (tmp_path / "latest" / "rth_soak_readiness.md").is_file()


def test_readiness_detects_missing_da_gex_cleanly(tmp_path):
    report = readiness_cli.collect_rth_soak_readiness(
        profile_ids=["morning_5k_call_tp75_control"],
        symbol="SPX",
        dte=0,
        output_root=tmp_path,
        structure_probe=lambda *_: _greek_probe(
            available=["dex", "vex"], missing=["da_gex", "charm"]
        ),
        quote_probe=lambda *_: _ready_quote(),
    )
    assert report["status"] == "BLOCKED"
    assert "da_gex_unavailable" in report["blockers"]
    assert report["daily_path"]["current_code"] == "R0_UNAVAILABLE"


def test_readiness_detects_tasty_quote_block_cleanly(tmp_path):
    blocked = {**_ready_quote(), "configured": False, "chain_returned": False,
               "validation_passed_count": 0, "blocker": "auth_failed"}
    report = readiness_cli.collect_rth_soak_readiness(
        profile_ids=["morning_5k_call_tp75_control"],
        symbol="SPX",
        dte=0,
        output_root=tmp_path,
        structure_probe=lambda *_: _greek_probe(
            available=["da_gex", "dex", "vex"], missing=[]
        ),
        quote_probe=lambda *_: blocked,
    )
    assert report["can_start"] is False
    assert "auth_failed" in report["blockers"]
    assert report["quotes"]["ready"] is False


def test_empty_soak_review_does_not_crash_and_writes_all_artifacts(tmp_path):
    report = build_rth_soak_review(
        manifest=None,
        heartbeat=None,
        alert_events=[],
        alert_deliveries=[],
        regime_events=[],
        open_trades=[],
        closed_trades=[],
        journal=[],
        marks=[],
        candidates=[],
        generated_at="2026-06-22T16:05:00-04:00",
    )
    destination = write_rth_soak_review(report, tmp_path)
    assert report["insufficient_data"] is True
    expected = {
        "rth_soak_review.md", "rth_soak_review.json", "alert_quality.csv",
        "regime_transition_review.csv", "paper_trade_review.csv",
        "greek_availability_review.csv",
    }
    assert expected <= {path.name for path in destination.iterdir()}


def test_fixture_review_summarizes_alert_regime_greek_and_paper_lifecycle(tmp_path):
    assert review_cli.main([
        "--fixture", "sample", "--json", "--output-dir", str(tmp_path)
    ]) == 0
    report = json.loads((tmp_path / "latest" / "rth_soak_review.json").read_text())
    assert report["alert_summary"]["alert_count"] == 2
    assert report["alert_summary"]["suppressed_count"] == 1
    assert report["alert_summary"]["cooldown_duplicate_count"] == 1
    assert report["regime_summary"]["maxvol_migration_events"] == 1
    assert report["regime_summary"]["r3_whipsaw_events"] == 1
    assert report["regime_summary"]["greek_degradation_events"] == 1
    assert report["paper_summary"]["entered_count"] == 1
    assert report["paper_summary"]["held_count"] == 1
    assert report["paper_summary"]["exited_count"] == 1
    assert report["paper_summary"]["exit_distribution"] == [{"count": 1, "name": "TP"}]


def test_alert_review_counts_source_severity_suppression_and_trade_links():
    fixture = sample_fixture_review()
    summary = fixture["alert_summary"]
    assert summary["source_distribution"] == [{"name": "REGIME_CHANGE", "count": 2}]
    assert summary["severity_distribution"] == [{"name": "WARNING", "count": 2}]
    assert summary["alerts_linked_to_trades"] == 2
    assert summary["noise_assessment"] == "insufficient_alert_volume"


def test_regime_review_counts_r1_r3_and_r4_r5_without_future_recompute():
    summary = sample_fixture_review()["regime_summary"]
    assert {row["name"] for row in summary["daily_regime_distribution"]} == {
        "R1_NEGATIVE_TREND", "R3_WHIPSAW"
    }
    assert {row["name"] for row in summary["context_regime_distribution"]} == {
        "R4_PRE_OPEX_CHARM_BUILD", "R5_OPEX_WEEK_MAGNET"
    }
    assert summary["transitions_near_paper_actions"] == 1


def test_paper_trade_review_counts_tp_sl_eod_regime_and_quote_exits():
    reasons = ("take_profit", "stop_loss", "eod_exit", "regime_thesis_invalid", "quote_invalid")
    trades = [
        {"paper_trade_id": f"p{index}", "status": "closed", "exit_reason": reason,
         "realized_pnl": 10 if index != 4 else -10}
        for index, reason in enumerate(reasons)
    ]
    summary, rows = summarize_paper_trades([], trades, [], [])
    assert {row["exit_category"] for row in rows} == {"TP", "SL", "EOD", "REGIME", "QUOTE"}
    assert summary["regime_exit_count"] == 1
    assert summary["regime_exits_helped"] == 1


def test_greek_review_counts_available_degraded_and_missing_fields():
    fixture = sample_fixture_review()
    summary = fixture["greek_summary"]
    assert summary["latest_status"] == "Degraded"
    assert summary["degraded_observations"] == 2
    assert summary["missing_field_counts"][0] == {"name": "vanna", "count": 2}

    empty_summary, rows = summarize_greeks([], [])
    assert empty_summary["latest_status"] == "No data" and rows == []


def test_alert_review_helper_handles_empty_inputs():
    summary, rows = summarize_alerts([], [])
    assert summary["alert_count"] == 0 and rows == []


def test_ui_imports_and_renders_rth_review_section():
    source = (REPO / "src/app/streamlit_main.py").read_text(encoding="utf-8")
    ast.parse(source)
    for text in (
        "def render_rth_review", "RTH Review", "Readiness", "Last soak",
        "Latest regime", "Greek data", "Soak review artifacts and raw reason codes",
    ):
        assert text in source


def test_phase11g_adds_no_dashboard_or_broker_order_paths():
    paths = (
        "scripts/diagnose_rth_soak_readiness.py",
        "scripts/review_rth_soak.py",
        "src/reviews/rth_soak.py",
    )
    combined = "\n".join((REPO / path).read_text(encoding="utf-8").lower() for path in paths)
    for forbidden in (
        "import dash", "from dash", "submit_order(", "place_order(",
        "preview_order(", "order_preview(", "execute_trade(",
    ):
        assert forbidden not in combined
    assert "zerosigma\\dashboard" not in combined
