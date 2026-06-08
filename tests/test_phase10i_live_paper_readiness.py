"""Phase 10I live-paper readiness, near-miss stress, and reference docs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import scripts.diagnose_live_readiness as live_cli
from src.app import cockpit_helpers as ch
from src.app import operator_mode as om
from src.backtesting import forward_readiness, stress_review

_REPO = Path(__file__).resolve().parents[1]
_UI = (_REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")


def _readiness(**overrides):
    values = {
        "runner_can_start": True,
        "runner_reason": "ok",
        "selected_profile_valid": True,
        "local_paper_mode": True,
        "structure_available": True,
        "required_strikes": [7500.0, 7505.0],
        "quote_state": "chain_returned_usable",
        "top_blocker": None,
        "sandbox": False,
    }
    values.update(overrides)
    return om.paper_test_readiness(**values)


def test_live_readiness_enables_only_when_requirements_are_met():
    ready = _readiness()
    assert ready["can_start"] is True
    assert ready["preview_only"] is False
    assert ready["quote_label"] == "Available"

    for state in (
        "chain_returned_stale",
        "chain_returned_missing_required_strikes",
        "chain_returned_validation_failed",
        "chain_unavailable",
        "auth_failed",
    ):
        blocked = _readiness(quote_state=state, top_blocker="spread_abs")
        assert blocked["can_start"] is False
        assert blocked["preview_only"] is True


def test_required_strikes_empty_has_clear_reason_and_sandbox_is_explicit():
    blocked = _readiness(required_strikes=[])
    assert blocked["can_start"] is False
    assert "Required strikes are empty" in blocked["reason"]
    assert "Structure anchors" in blocked["reason"]
    sandbox = _readiness(quote_state="mock", sandbox=True)
    assert sandbox["can_start"] is True
    assert sandbox["quote_label"] == "Sandbox"


def test_wide_quote_state_is_friendly_and_exact():
    result = _readiness(
        quote_state="chain_returned_validation_failed",
        top_blocker="spread_abs",
    )
    assert result["quote_label"] == "Wide"
    assert result["reason"] == "Live quotes are too wide."


def test_live_readiness_blocks_profile_chain_dte_mismatch():
    result = _readiness(profile_dte=0, quote_chain_dte=1)
    assert result["can_start"] is False
    assert result["preview_only"] is True
    assert "Profile targets 0DTE" in result["reason"]
    assert om.quote_chain_dte("2026-06-08", datetime(2026, 6, 7)) == 1


def test_live_readiness_cli_output_is_sanitized(monkeypatch, capsys, tmp_path):
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
        "--symbol", "SPX", "--profile", "morning_5k_call_tp75_control", "--dte", "0",
        "--output-root", str(tmp_path),
    ])
    out = capsys.readouterr().out.lower()
    assert rc == 0
    assert "start paper test enabled" in out and "quotes: available" in out
    for token in ("client_secret", "refresh_token", "password", "access_token"):
        assert token not in out


def _trades():
    return [
        {
            "date": f"2026-01-{index:02d}",
            "pnl_dollars": pnl,
            "entry_credit_dollars": 100.0,
            "entry_credit_points": 1.0,
            "side": "CALL_CREDIT",
            "exit_reason": "TP" if pnl > 0 else "SL",
            "wds_tier": 1 if index % 2 else 2,
            "corridor_valid": index % 3 != 0,
            "contracts": 1,
        }
        for index, pnl in enumerate(
            [100, -50, 80, 70, -40, 60, 90, -30, 50, 40, -20, 30, 20, 10, 15],
            start=1,
        )
    ]


def test_stress_review_outputs_split_fill_account_and_concentration(tmp_path):
    trades = _trades()
    dates = [row["date"] for row in trades]
    split = stress_review.split_stress(trades, dates)
    fill = stress_review.slippage_stress(trades)
    account = stress_review.account_sizing_stress(trades)
    concentration = stress_review.concentration_check(trades)
    recommendation = stress_review.stress_recommendation(split, fill, account, concentration)
    result = stress_review.StressReviewResult(
        candidate_profile_snapshot={"parameter_hash": stress_review.CANDIDATE_HASH},
        split_stress_summary=split,
        slippage_stress_summary=fill,
        account_sizing_stress=account,
        concentration_summary=concentration,
        recommendation=recommendation,
        narrative="Candidate stress review.",
    )
    stress_review.write_stress_review(result, [tmp_path])
    expected = {
        "candidate_profile_snapshot.json",
        "split_stress_summary.csv",
        "slippage_stress_summary.csv",
        "account_sizing_stress.csv",
        "concentration_summary.csv",
        "narrative_summary.md",
    }
    assert expected <= {path.name for path in tmp_path.iterdir()}
    loaded = ch.read_backtest_stress_review(tmp_path)
    assert loaded["available"]
    assert len(loaded["split_stress_summary"]) == 15
    assert {row["scenario"] for row in fill} == {
        "base", "credit_haircut_5pct", "credit_haircut_10pct",
    }
    assert len(account) == 4
    assert "excluding_best_3_trades" in {
        row["category"] for row in concentration
    }


def test_stress_freeze_rule_passes_and_fails_deterministically():
    split = []
    for name in ("60/20/20", "50/25/25", "70/15/15", "55/20/25", "65/20/15"):
        split.extend([
            {"split": name, "scope": "validation", "expectancy_dollars": 10, "total_trades": 10},
            {"split": name, "scope": "holdout", "expectancy_dollars": 1, "total_trades": 5},
        ])
    fill = [{"scenario": "credit_haircut_10pct", "total_pnl_dollars": 1}]
    account = [{"max_drawdown_pct": 10}]
    concentration = [{
        "dimension": "contribution_check",
        "category": "excluding_best_3_trades",
        "pnl_dollars": 1,
    }]
    passed = stress_review.stress_recommendation(split, fill, account, concentration)
    assert passed["freeze_eligible"] is True
    split[0]["total_trades"] = 9
    failed = stress_review.stress_recommendation(split, fill, account, concentration)
    assert failed["freeze_eligible"] is False
    assert "validation_trade_floor_every_split" in failed["failed_criteria"]


def test_forward_readiness_report_is_generated(tmp_path):
    report = forward_readiness.build_forward_readiness()
    out = forward_readiness.write_forward_readiness(report, tmp_path)
    assert report["candidate_count"] == 2
    assert report["production_approved"] is False
    assert (out / "forward_paper_candidates.md").is_file()
    payload = json.loads((out / "forward_paper_candidates.json").read_text(encoding="utf-8"))
    assert payload["broker_execution_available"] is False


def test_ui_has_readiness_gate_and_near_miss_review():
    for text in (
        "Paper-test readiness",
        "Quote Provider Status",
        "Start Paper Test",
        "Near-Miss Candidate Review",
        "Split Stress",
        "Fill Stress",
        "Account Stress",
        "Concentration Check",
        "Final Recommendation",
    ):
        assert text in _UI


def test_research_reference_docs_exist():
    expected = (
        "README.md",
        "index.md",
        "notes/reference_note_template.md",
        "papers/.gitkeep",
        "articles/.gitkeep",
        "notes/.gitkeep",
        "extracted_hypotheses/.gitkeep",
        "rejected_ideas/.gitkeep",
    )
    base = _REPO / "docs" / "research"
    assert all((base / path).is_file() for path in expected)
    template = (base / "notes" / "reference_note_template.md").read_text(encoding="utf-8")
    assert "## Possible strategy hypotheses" in template
    assert "## How we could test this" in template


def test_phase10i_sources_introduce_no_broker_or_order_paths():
    paths = (
        "scripts/diagnose_live_readiness.py",
        "scripts/backtest_stress_review.py",
        "scripts/build_forward_readiness.py",
        "src/backtesting/stress_review.py",
        "src/backtesting/forward_readiness.py",
    )
    for rel in paths:
        text = (_REPO / rel).read_text(encoding="utf-8").lower()
        for token in ("submit_order", "place_order", "preview_order", "execute_trade", "import httpx"):
            assert token not in text, f"{rel} contains {token!r}"
