"""Scanner runner produces ranked candidates + decision logs end-to-end with mock data."""

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
    import os
    e = dict(os.environ)
    return e


def test_scanner_writes_outputs_to_temp_paths(tmp_path: Path):
    out = tmp_path / "outputs"
    rc = _run_scanner({
        "OUTPUT_DIR": str(out),
        "PYTHONPATH": str(REPO_ROOT),
    })
    assert rc == 0

    # outputs/latest/ranked_candidates.csv
    latest_csv = out / "latest" / "ranked_candidates.csv"
    assert latest_csv.exists(), f"missing {latest_csv}"
    with latest_csv.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows, "ranked_candidates.csv has no rows"
    sides = {r["side"] for r in rows}
    assert "CALL_CREDIT" in sides
    assert "PUT_CREDIT" in sides

    # ranked rows carry planned + theoretical dollar columns
    sample = rows[0]
    assert "planned_loss_dollars" in sample
    assert "theoretical_max_loss_dollars" in sample
    assert float(sample["theoretical_max_loss_dollars"]) > 0

    # outputs/latest/decision_log.jsonl exists + is parseable
    latest_log = out / "latest" / "decision_log.jsonl"
    assert latest_log.exists()
    records = [json.loads(line) for line in latest_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records
    assert {r["strategy_id"] for r in records} == {"vertical_wing_v1"}


def test_scanner_default_profile_emits_a_trade_decision(tmp_path: Path):
    out = tmp_path / "outputs"
    rc = _run_scanner({"OUTPUT_DIR": str(out), "PYTHONPATH": str(REPO_ROOT)})
    assert rc == 0
    latest_log = out / "latest" / "decision_log.jsonl"
    records = [json.loads(line) for line in latest_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    decisions = {r["decision"] for r in records}
    # With aggressive_paper_10k + tuned stub, at least one TRADE_* decision fires
    assert any(d.startswith("TRADE_") for d in decisions), decisions
