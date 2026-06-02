"""Phase 5 — daily selector wired through the scanner. NO network, NO creds.

Drives scripts.run_scanner (stub structure + mock quotes) via importlib.reload +
monkeypatched argv, then asserts the selector columns land in the CSV, the
decision_log carries selector metadata, --print-candidates shows the selector
section, and AT MOST ONE row is marked selected_trade.
"""

from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path

rs = importlib.import_module("scripts.run_scanner")


def _run(monkeypatch, tmp_path, argv, capsys=None):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    for k in ("DAILY_TRADE_SELECTOR", "MAX_TRADES_PER_DAY", "ALLOW_CALL_CREDIT",
              "ALLOW_PUT_CREDIT", "REQUIRE_SCORE_EDGE", "STRICT_TARGET_DTE",
              "TARGET_DTE", "ZS_STRUCTURE_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sys, "argv", argv)
    importlib.reload(rs)
    rc = rs.main()
    out = capsys.readouterr().out if capsys is not None else ""
    return rc, out


def _rows(tmp_path: Path):
    p = tmp_path / "latest" / "ranked_candidates.csv"
    with p.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _decisions(tmp_path: Path):
    p = tmp_path / "latest" / "decision_log.jsonl"
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _n_selected(rows) -> int:
    return sum(1 for r in rows if str(r.get("selected_trade")).lower() == "true")


def test_default_selector_marks_at_most_one_and_stamps_metadata(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner", "--strategy", "vertical_wing_v1",
        "--structure-provider", "stub", "--quote-provider", "mock",
        "--daily-selector", "score_best_valid", "--print-candidates",
    ], capsys)
    assert rc == 0
    assert "Traceback" not in out

    rows = _rows(tmp_path)
    assert rows, "no candidate rows written"
    # Phase 5 columns present in the CSV header.
    for col in ("daily_selector_mode", "selected_trade", "selector_rank",
                "selector_reason", "selector_score", "side_allowed_by_config",
                "max_trades_per_day", "selector_no_trade_reason"):
        assert col in rows[0], f"CSV missing Phase 5 column {col!r}"
    # AT MOST ONE selected_trade (max_trades_per_day defaults to 1).
    assert _n_selected(rows) <= 1
    assert all(r["daily_selector_mode"] == "score_best_valid" for r in rows)

    # Decision log carries the selector metadata + pre/post decision.
    snap = _decisions(tmp_path)[0]["snapshot_summary"]
    assert snap["daily_selector_mode"] == "score_best_valid"
    assert "pre_selector_decision" in snap and "post_selector_decision" in snap
    assert "selector_result" in snap
    assert snap["max_trades_per_day"] == 1
    sr = snap["selector_result"]
    assert "candidates_with_selector_metadata" in sr
    # If a trade was selected, exactly one row carries it and the side matches.
    if snap["post_selector_decision"].startswith("TRADE_"):
        assert _n_selected(rows) == 1
        sel = next(r for r in rows if str(r["selected_trade"]).lower() == "true")
        side = "CALL_CREDIT" if snap["post_selector_decision"].endswith("CALL_CREDIT") else "PUT_CREDIT"
        assert sel["side"] == side
    # Pre-selector (strategy) decision is preserved, not destroyed.
    assert snap["pre_selector_decision"] in (
        "TRADE_CALL_CREDIT", "TRADE_PUT_CREDIT", "NO_TRADE",
    )
    # Audit print shows the tick-level selector block.
    assert "=== DAILY SELECTOR ===" in out
    assert "daily_selector_mode=" in out


def test_no_trade_mode_selects_none_in_scanner(monkeypatch, tmp_path):
    rc, _ = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner", "--strategy", "vertical_wing_v1",
        "--structure-provider", "stub", "--quote-provider", "mock",
        "--daily-selector", "no_trade", "--print-candidates",
    ])
    assert rc == 0
    rows = _rows(tmp_path)
    assert _n_selected(rows) == 0
    snap = _decisions(tmp_path)[0]["snapshot_summary"]
    assert snap["selected_trade"] is False
    assert snap["post_selector_decision"] == "NO_TRADE"
    assert snap["selector_no_trade_reason"] == "no_trade_mode"


def test_call_credit_only_via_cli_never_selects_a_put(monkeypatch, tmp_path):
    rc, _ = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner", "--strategy", "vertical_wing_v1",
        "--structure-provider", "stub", "--quote-provider", "mock",
        "--daily-selector", "call_credit_only",
    ])
    assert rc == 0
    rows = _rows(tmp_path)
    assert _n_selected(rows) <= 1
    for r in rows:
        if str(r["selected_trade"]).lower() == "true":
            assert r["side"] == "CALL_CREDIT"


def test_both_sides_disabled_selects_none(monkeypatch, tmp_path):
    rc, _ = _run(monkeypatch, tmp_path, [
        "scripts.run_scanner", "--strategy", "vertical_wing_v1",
        "--structure-provider", "stub", "--quote-provider", "mock",
        "--no-allow-call-credit", "--no-allow-put-credit",
    ])
    assert rc == 0
    rows = _rows(tmp_path)
    assert _n_selected(rows) == 0
    snap = _decisions(tmp_path)[0]["snapshot_summary"]
    assert snap["selector_no_trade_reason"] == "no_sides_allowed"


def test_help_lists_phase5_flags(monkeypatch):
    import os
    import subprocess
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    out = subprocess.run(
        [sys.executable, "-m", "scripts.run_scanner", "--help"],
        capture_output=True, text=True, timeout=30, env=env,
        encoding="utf-8", errors="replace",
    )
    assert out.returncode == 0
    for flag in ("--daily-selector", "--max-trades-per-day", "--no-allow-call-credit",
                 "--require-score-edge", "--min-selector-score"):
        assert flag in out.stdout, f"--help missing {flag}"
