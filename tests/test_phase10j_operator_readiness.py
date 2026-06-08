"""Phase 10J operator readiness polish and runbook tests."""

from __future__ import annotations

import json
from pathlib import Path

import scripts.diagnose_live_readiness as live_cli
from src.app import operator_mode as om
from src.app import readiness_snapshot
from src.backtesting import forward_readiness

_REPO = Path(__file__).resolve().parents[1]
_UI = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def test_startup_checklist_pass_blocked_unknown_states():
    assert om.local_paper_execution_mode("local_paper") is True
    assert om.local_paper_execution_mode("live") is False
    rows = om.morning_startup_checklist(
        app_source_live=True,
        symbol="SPX",
        structure_available=True,
        tasty_configured=True,
        tasty_authenticated=True,
        selected_profile_id="morning_5k_call_tp75_control",
        profile_dte=0,
        quote_chain_dte=0,
        required_strikes=[7400.0, 7405.0],
        quote_state="chain_returned_usable",
        start_enabled=True,
        local_paper_only=True,
    )
    assert len(rows) == 10
    assert {row["status"] for row in rows} == {"Pass"}

    blocked = om.morning_startup_checklist(
        app_source_live=False,
        symbol="SPY",
        structure_available=None,
        tasty_configured=True,
        tasty_authenticated=False,
        selected_profile_id="morning_5k_dynamic_tp75",
        profile_dte=0,
        quote_chain_dte=1,
        required_strikes=[],
        quote_state="chain_returned_validation_failed",
        top_blocker="spread_abs",
        start_enabled=False,
        local_paper_only=True,
    )
    status_by_item = {row["item"]: row["status"] for row in blocked}
    assert status_by_item["App source is Live"] == "Blocked"
    assert status_by_item["ZerσSigma structure is available"] == "Unknown"
    assert status_by_item["Quotes are Available"] == "Blocked"


def test_disabled_start_maps_to_friendly_next_actions():
    assert "Re-check during RTH" in om.readiness_next_action(
        reason="Profile targets 0DTE, but the live quote chain is 1DTE.",
        quote_state="chain_returned_usable",
    )
    assert "Wait for RTH" in om.readiness_next_action(
        quote_state="chain_returned_stale", top_blocker="stale"
    )
    assert "failed spread validation" in om.readiness_next_action(
        quote_state="chain_returned_validation_failed", top_blocker="spread_abs"
    )
    assert "ZS API" in om.readiness_next_action(structure_available=False)


def test_rth_diagnostic_commands_include_selected_profile():
    commands = om.rth_diagnostic_commands("spx", "morning_2k_call_no_tp_control", 0)
    assert commands == [
        "python -m scripts.diagnose_tasty_quotes --symbol SPX --dte 0",
        "python -m scripts.diagnose_cockpit_quote_status --symbol SPX --dte 0",
        (
            "python -m scripts.diagnose_live_readiness --symbol SPX "
            "--profile morning_2k_call_no_tp_control --dte 0"
        ),
    ]


def test_candidate_cards_include_two_benchmarks():
    report = forward_readiness.build_forward_readiness()
    profiles = {row["profile_id"]: row for row in report["profiles"]}
    assert set(profiles) == {
        "morning_5k_call_tp75_control",
        "morning_2k_call_no_tp_control",
    }
    assert profiles["morning_5k_call_tp75_control"]["role"] == "Benchmark"
    assert profiles["morning_2k_call_no_tp_control"]["contracts"] == 1
    assert profiles["morning_5k_call_tp75_control"]["starting_account_suggestion"] == 10000


def test_eod_checklist_and_runbook_docs_exist():
    checklist = om.eod_review_checklist()
    assert "Generate / Refresh EOD summary." in checklist
    assert "Compare live paper behavior to backtest expectation." in checklist
    for rel in (
        "docs/runbooks/local_paper_morning_runbook.md",
        "docs/runbooks/eod_review_runbook.md",
    ):
        assert (_REPO / rel).is_file()


def test_readiness_snapshot_is_sanitized(tmp_path):
    result = {
        "symbol": "SPX",
        "profile_id": "morning_5k_call_tp75_control",
        "start_paper_test_enabled": False,
        "start_reason": "Profile targets 0DTE, but the live quote chain is 1DTE.",
        "client_secret": "do-not-write",
        "refresh_token": "do-not-write",
    }
    path = readiness_snapshot.write_readiness_snapshot(result, output_root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbol"] == "SPX"
    assert payload["profile_id"] == "morning_5k_call_tp75_control"
    assert "client_secret" not in payload
    assert "refresh_token" not in payload


def test_live_readiness_cli_writes_latest_snapshot(monkeypatch, tmp_path, capsys):
    payload = {
        "symbol": "SPX",
        "target_dte": 0,
        "profile_id": "morning_5k_call_tp75_control",
        "profile_valid": True,
        "profile_dte": 0,
        "zs_configured": True,
        "structure_available": True,
        "structure_provider": "zerosigma_api",
        "spot": 6000.0,
        "corridor_10k_valid": True,
        "corridor_10k_reason": "spot is between CW1 and PW1",
        "required_strikes": [6000.0, 6005.0],
        "quote_provider": "tastytrade",
        "tasty_configured": True,
        "tasty_auth_mode": "oauth",
        "quote_root": "SPXW",
        "quote_expiration": "2026-06-08",
        "quote_chain_dte": 0,
        "chain_returned": True,
        "quote_count": 4,
        "quote_state": "chain_returned_usable",
        "quote_label": "Available",
        "missing_strikes": [],
        "top_blocker": None,
        "start_paper_test_enabled": True,
        "start_reason": "Ready for a local Live-data paper test. No broker execution.",
    }
    monkeypatch.setattr(live_cli, "collect_live_readiness", lambda **kwargs: payload)
    rc = live_cli.main([
        "--symbol", "SPX",
        "--profile", "morning_5k_call_tp75_control",
        "--dte", "0",
        "--output-root", str(tmp_path),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Sanitized latest snapshot" in out
    assert (tmp_path / "readiness" / "latest" / "readiness_summary.json").is_file()


def test_ui_renders_phase10j_sections():
    for text in (
        "Morning Startup Checklist",
        "Advanced — RTH diagnostics commands",
        "This week's forward-paper candidates",
        "EOD Review Checklist",
        "Latest readiness snapshot",
    ):
        assert text in _UI


def test_phase10j_sources_introduce_no_broker_or_order_paths():
    paths = (
        "src/app/operator_mode.py",
        "src/app/readiness_snapshot.py",
        "src/app/streamlit_main.py",
        "scripts/diagnose_live_readiness.py",
    )
    for rel in paths:
        text = (_REPO / rel).read_text(encoding="utf-8").lower()
        for token in ("submit_order", "place_order", "preview_order", "execute_trade", "import httpx"):
            assert token not in text, f"{rel} contains {token!r}"
