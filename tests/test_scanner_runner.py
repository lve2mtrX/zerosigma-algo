"""Scanner runner exercises the full provider-split pipeline."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_scanner(extra_env: dict[str, str]) -> int:
    cmd = [sys.executable, "-m", "scripts.run_scanner"]
    return subprocess.call(
        cmd, cwd=str(REPO_ROOT),
        env={**_clean_env(), **extra_env},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _clean_env() -> dict[str, str]:
    """Inherit the user's environ but strip ZS_* vars so the subprocess
    runs against stub/mock providers regardless of the developer's local
    .env contents."""
    import os
    env = dict(os.environ)
    for k in list(env.keys()):
        if k.startswith("ZS_API_") or k == "ZS_STRUCTURE_PROVIDER":
            env.pop(k, None)
    # Force the subprocess into the safe defaults.
    env["ZS_STRUCTURE_PROVIDER"] = "stub"
    env["ZS_API_AUTH_MODE"]      = "none"
    return env


def test_scanner_writes_outputs_with_leg_quotes(tmp_path: Path):
    out = tmp_path / "outputs"
    rc = _run_scanner({"OUTPUT_DIR": str(out), "PYTHONPATH": str(REPO_ROOT)})
    assert rc == 0

    latest_csv = out / "latest" / "ranked_candidates.csv"
    assert latest_csv.exists()
    with latest_csv.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows, "ranked_candidates.csv has no rows"
    sample = rows[0]
    # leg bid/ask/mid columns now flow through (post Phase 1.5)
    for col in ("short_bid", "short_ask", "short_mid",
                "long_bid", "long_ask", "long_mid",
                "bid_ask_quality",
                "planned_loss_dollars", "theoretical_max_loss_dollars"):
        assert col in sample, f"missing column {col}"
    # numeric — chain-derived
    assert float(sample["short_bid"]) > 0


def test_decision_log_carries_both_providers(tmp_path: Path):
    out = tmp_path / "outputs"
    rc = _run_scanner({"OUTPUT_DIR": str(out), "PYTHONPATH": str(REPO_ROOT)})
    assert rc == 0

    latest_log = out / "latest" / "decision_log.jsonl"
    assert latest_log.exists()
    records = [json.loads(line) for line in latest_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records
    summary = records[0]["snapshot_summary"]
    assert summary["structure_provider"] == "stub"
    assert summary["quote_provider"]     == "mock"
    assert "structure_ts" in summary and "quote_ts" in summary
    # spot comes from QuoteProvider; structure_spot is the structure provider's
    assert summary["spot"] is not None
    assert "structure_spot" in summary
    # vertical-wing levels from structure
    assert summary["put_ceiling_2k"] == 5815.0
    assert summary["call_floor_2k"]  == 5785.0


def test_scanner_default_profile_still_emits_a_trade_decision(tmp_path: Path):
    out = tmp_path / "outputs"
    rc = _run_scanner({"OUTPUT_DIR": str(out), "PYTHONPATH": str(REPO_ROOT)})
    assert rc == 0
    latest_log = out / "latest" / "decision_log.jsonl"
    records = [json.loads(line) for line in latest_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    decisions = {r["decision"] for r in records}
    assert any(d.startswith("TRADE_") for d in decisions), decisions
