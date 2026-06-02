"""Phase 6 — scanner --profile integration. NO network, NO credentials.

Drives scripts.run_scanner with a strategy run-profile (stub structure + mock
quotes) and asserts: profile values are applied, CLI overrides profile, profile
metadata lands in CSV + decision_log, and the run-profile hash is recorded.
"""

from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path

rs = importlib.import_module("scripts.run_scanner")


def _run(monkeypatch, tmp_path, argv):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    for k in ("DAILY_TRADE_SELECTOR", "MAX_TRADES_PER_DAY", "TARGET_DTE",
              "STRICT_TARGET_DTE", "QUOTE_PROVIDER", "ZS_STRUCTURE_PROVIDER",
              "ALLOW_CALL_CREDIT", "ALLOW_PUT_CREDIT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sys, "argv", argv)
    importlib.reload(rs)
    return rs.main()


def _rows(tmp_path: Path):
    with (tmp_path / "latest" / "ranked_candidates.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _snap(tmp_path: Path):
    p = tmp_path / "latest" / "decision_log.jsonl"
    recs = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return recs[0]["snapshot_summary"]


def test_profile_loads_values_into_scanner(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path,
              ["scripts.run_scanner", "--profile", "vertical_wing_score_best_1dte"])
    assert rc == 0
    snap = _snap(tmp_path)
    # provider + selector + target_dte came from the profile
    assert snap["quote_provider"] == "mock"            # profile quote_provider
    assert snap["daily_selector_mode"] == "score_best_valid"
    assert snap["target_dte"] == 1                      # profile target_dte
    # profile provenance recorded
    assert snap["profile_id"] == "vertical_wing_score_best_1dte"
    assert snap["profile_loaded"] is True
    assert snap["profile_hash"]                          # non-empty hash
    assert snap["profile_version"] == 1
    # CSV rows carry the same provenance
    rows = _rows(tmp_path)
    assert rows and rows[0]["profile_id"] == "vertical_wing_score_best_1dte"
    assert rows[0]["profile_hash"] == snap["profile_hash"]
    assert rows[0]["config_source_summary"]


def test_call_only_profile_loads_selector(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path,
              ["scripts.run_scanner", "--profile", "vertical_wing_call_only_1dte"])
    assert rc == 0
    snap = _snap(tmp_path)
    assert snap["daily_selector_mode"] == "call_credit_only"
    # any selected row must be a CALL_CREDIT (puts disabled in the profile)
    for r in _rows(tmp_path):
        if str(r["selected_trade"]).lower() == "true":
            assert r["side"] == "CALL_CREDIT"


def test_cli_overrides_profile(monkeypatch, tmp_path):
    # profile says score_best_valid; CLI forces no_trade → CLI wins.
    rc = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner", "--profile", "vertical_wing_score_best_1dte",
        "--daily-selector", "no_trade",
    ])
    assert rc == 0
    snap = _snap(tmp_path)
    assert snap["daily_selector_mode"] == "no_trade"          # CLI override
    assert snap["selected_trade"] is False
    # config_source_summary records the CLI override
    assert "daily_selector" in snap["config_source_summary"]


def test_no_trade_profile_selects_none(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path,
              ["scripts.run_scanner", "--profile", "vertical_wing_no_trade"])
    assert rc == 0
    snap = _snap(tmp_path)
    assert snap["daily_selector_mode"] == "no_trade"
    assert snap["selected_trade"] is False
    assert snap["target_dte"] == 0


def test_profile_path_loads_too(monkeypatch, tmp_path):
    path = str(Path("profiles/vertical_wing_best_credit_1dte.yaml"))
    rc = _run(monkeypatch, tmp_path, ["scripts.run_scanner", "--profile", path])
    assert rc == 0
    snap = _snap(tmp_path)
    assert snap["profile_id"] == "vertical_wing_best_credit_1dte"
    assert snap["daily_selector_mode"] == "best_credit_valid"


def test_unknown_profile_fails_cleanly(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path,
              ["scripts.run_scanner", "--profile", "no_such_profile_zzz"])
    assert rc == 5   # clean non-zero, no traceback


def test_risk_profile_backcompat(monkeypatch, tmp_path):
    """--profile <known risk-profile name> still works (treated as risk profile)."""
    rc = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner", "--profile", "aggressive_paper_10k",
        "--structure-provider", "stub", "--quote-provider", "mock",
    ])
    assert rc == 0
    snap = _snap(tmp_path)
    assert snap["profile_loaded"] is False     # not a strategy profile
    assert snap["profile_id"] is None


def test_no_profile_is_unchanged_default(monkeypatch, tmp_path):
    """Without --profile the scanner behaves as before (profile_loaded False)."""
    rc = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner", "--structure-provider", "stub", "--quote-provider", "mock",
    ])
    assert rc == 0
    snap = _snap(tmp_path)
    assert snap["profile_loaded"] is False
    assert snap["profile_id"] is None
    assert snap["daily_selector_mode"] == "score_best_valid"   # default
