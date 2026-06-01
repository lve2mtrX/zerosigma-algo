"""EOD summary generation from sample local files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.reporting.config_change_log import log_session_snapshot
from src.reporting.decision_log import log_decision
from src.reporting.eod import generate_eod_summary
from src.strategies.base import Candidate, StrategyDecision
from src.utils.time import today_et_date


def _seed_decision_log(repo_root: Path, output_root: Path, date_str: str):
    """Build a synthetic decision and write it under outputs/runs/{date}/."""
    # Use a Candidate without scoring to keep math simple
    c = Candidate(
        strategy_id="vertical_wing_v1",
        side="CALL_CREDIT",
        symbol="SPX",
        expiry=date_str,
        short_strike=5815.0,
        long_strike=5820.0,
        credit=0.80,
        max_risk=4.20,
        reward_risk=0.19,
        breakeven=5815.8,
        distance_from_spot=15.0,
        score=0.65,
    )
    decision = StrategyDecision(
        strategy_id="vertical_wing_v1",
        decision="TRADE_CALL_CREDIT",
        selected=c, all_candidates=[c],
        explanation="seeded",
    )
    log_decision(
        output_root, decision,
        {"spot": 5800.0, "maxvol": 5810.0, "gamma_regime": "positive",
         "put_ceiling_2k": 5815.0, "call_floor_2k": 5785.0},
        datetime.fromisoformat(date_str + "T14:00:00-04:00"),
        date_str=date_str,
    )


def test_generate_eod_summary_creates_both_daily_and_latest(tmp_path: Path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    output_root = tmp_path / "outputs"
    output_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OUTPUT_DIR", str(output_root))

    date_str = today_et_date()
    _seed_decision_log(repo_root, output_root, date_str)

    out_md = generate_eod_summary(repo_root, date_str)
    assert out_md.exists()
    md_text = out_md.read_text(encoding="utf-8")
    assert "EOD Summary" in md_text
    assert "Best candidate of the day" in md_text
    assert "CALL_CREDIT" in md_text

    # outputs/latest copy
    latest_md = output_root / "latest" / "eod_summary.md"
    latest_json = output_root / "latest" / "eod_summary.json"
    assert latest_md.exists()
    assert latest_json.exists()
    payload = json.loads(latest_json.read_text(encoding="utf-8"))
    assert payload["trade_decisions"] == 1
    assert payload["no_trade_decisions"] == 0
    assert payload["best_candidate_of_day"]["side"] == "CALL_CREDIT"


def test_config_change_log_session_snapshot(tmp_path: Path):
    path = log_session_snapshot(
        tmp_path,
        session_dict={"starting_balance": 10000, "contracts_per_trade": 5},
        active_strategy="vertical_wing_v1",
        active_risk_profile="aggressive_paper_10k",
    )
    assert path.exists()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records[0]["event"] == "session_start"
    assert records[0]["session_snapshot"]["contracts_per_trade"] == 5
