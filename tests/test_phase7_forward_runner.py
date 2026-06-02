"""Phase 7 — forward runner (local paper MONITORING). NO network, NO creds.

Drives scripts.run_forward.main([...]) in-process against the committed
stub+mock run-profiles, into a tmp --output-dir. Asserts ledger files,
manifest lifecycle, dedup, market-hours skip, error/stop paths, and that NO
execution/order surface exists.
"""

from __future__ import annotations

import glob
import importlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

rf = importlib.import_module("scripts.run_forward")

_ET = ZoneInfo("America/New_York")


def _run(tmp_path, *args):
    return rf.main(["--output-dir", str(tmp_path), *args])


def _run_dir(tmp_path) -> Path:
    dirs = glob.glob(str(tmp_path / "runs" / "*"))
    assert dirs, "no run folder created"
    return Path(dirs[0])


def _jsonl(p: Path):
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── _is_rth pure ─────────────────────────────────────────────────────────────

def test_is_rth_pure():
    assert rf._is_rth(datetime(2026, 6, 2, 10, 0, tzinfo=_ET)) is True       # Tue 10:00
    assert rf._is_rth(datetime(2026, 6, 2, 9, 29, tzinfo=_ET)) is False      # pre-open
    assert rf._is_rth(datetime(2026, 6, 2, 16, 1, tzinfo=_ET)) is False      # post-close
    assert rf._is_rth(datetime(2026, 6, 6, 12, 0, tzinfo=_ET)) is False      # Saturday


# ── dry-run ──────────────────────────────────────────────────────────────────

def test_dry_run_writes_manifest_only_no_scan(tmp_path):
    rc = _run(tmp_path, "--profile", "vertical_wing_score_best_1dte", "--dry-run")
    assert rc == 0
    rd = _run_dir(tmp_path)
    man = json.loads((rd / "run_manifest.json").read_text())
    assert man["dry_run"] is True
    assert man["status"] == "dry_run"
    assert man["no_execution"] is True
    # NO scanning happened → no tick/signal logs
    assert not (rd / "tick_log.jsonl").exists()
    assert not (rd / "signal_log.jsonl").exists()


# ── --once ──────────────────────────────────────────────────────────────────

def test_once_creates_run_folder_manifest_tick_heartbeat(tmp_path):
    rc = _run(tmp_path, "--profile", "vertical_wing_no_trade", "--once", "--interval-seconds", "0")
    assert rc == 0
    rd = _run_dir(tmp_path)
    assert (rd / "run_manifest.json").is_file()
    assert (rd / "tick_log.jsonl").is_file()
    assert (rd / "heartbeat.json").is_file()
    man = json.loads((rd / "run_manifest.json").read_text())
    assert man["status"] == "completed"
    assert man["max_ticks"] == 1
    assert man["no_execution"] is True and man["execution_mode"] == "disabled_local_monitoring"
    assert len(_jsonl(rd / "tick_log.jsonl")) == 1


def test_max_ticks_two_runs_two_ticks_completed(tmp_path):
    rc = _run(tmp_path, "--profile", "vertical_wing_no_trade", "--max-ticks", "2",
              "--interval-seconds", "0")
    assert rc == 0
    rd = _run_dir(tmp_path)
    ticks = _jsonl(rd / "tick_log.jsonl")
    assert len(ticks) == 2
    assert [t["tick_id"] for t in ticks] == [1, 2]
    assert json.loads((rd / "run_manifest.json").read_text())["status"] == "completed"


# ── selected vs no-trade ledgers ─────────────────────────────────────────────

def test_selected_trade_creates_signal_log_and_csv(tmp_path):
    rc = _run(tmp_path, "--profile", "vertical_wing_score_best_1dte", "--once",
              "--interval-seconds", "0")
    assert rc == 0
    rd = _run_dir(tmp_path)
    assert (rd / "signal_log.jsonl").is_file()
    assert (rd / "selected_trades.csv").is_file()
    sigs = _jsonl(rd / "signal_log.jsonl")
    assert len(sigs) >= 1
    s = sigs[0]
    # signal carries provenance + the key trade fields from ranked_candidates.csv
    assert s["profile_id"] == "vertical_wing_score_best_1dte"
    assert s["profile_hash"]
    assert s["symbol"] == "SPX"
    assert s["side"] in ("CALL_CREDIT", "PUT_CREDIT")
    assert s["daily_selector_mode"] == "score_best_valid"
    assert "credit" in s and "score" in s and "selected_expiry" in s


def test_no_selected_trade_creates_no_trade_log(tmp_path):
    rc = _run(tmp_path, "--profile", "vertical_wing_no_trade", "--once", "--interval-seconds", "0")
    assert rc == 0
    rd = _run_dir(tmp_path)
    assert (rd / "no_trade_log.jsonl").is_file()
    assert not (rd / "signal_log.jsonl").exists()
    nt = _jsonl(rd / "no_trade_log.jsonl")[0]
    assert nt["daily_selector"] == "no_trade"
    assert nt["no_trade_reason"]


# ── duplicate signal protection ──────────────────────────────────────────────

def test_duplicate_signal_not_appended_twice(tmp_path):
    rc = _run(tmp_path, "--profile", "vertical_wing_score_best_1dte", "--max-ticks", "2",
              "--interval-seconds", "0")
    assert rc == 0
    rd = _run_dir(tmp_path)
    # Same selected trade both ticks → signal emitted ONCE.
    assert len(_jsonl(rd / "signal_log.jsonl")) == 1
    ticks = _jsonl(rd / "tick_log.jsonl")
    assert ticks[0]["duplicate_selected_signal"] is False
    assert ticks[1]["duplicate_selected_signal"] is True
    assert ticks[0]["selected_trade"] is True and ticks[1]["selected_trade"] is True


# ── heartbeat ─────────────────────────────────────────────────────────────────

def test_heartbeat_updates_each_tick(tmp_path):
    _run(tmp_path, "--profile", "vertical_wing_no_trade", "--max-ticks", "2", "--interval-seconds", "0")
    rd = _run_dir(tmp_path)
    hb = json.loads((rd / "heartbeat.json").read_text())
    # final heartbeat reflects the completed run + last tick
    assert hb["run_id"] and hb["status"] == "completed"
    assert hb["tick_id"] == 2


# ── market-hours guard ───────────────────────────────────────────────────────

def test_market_hours_only_skips_outside_rth(tmp_path, monkeypatch):
    import src.utils.time as _t
    closed = datetime(2026, 6, 6, 12, 0, tzinfo=_ET)   # Saturday
    monkeypatch.setattr(_t, "now_et", lambda: closed)
    rc = _run(tmp_path, "--profile", "vertical_wing_score_best_1dte", "--once",
              "--interval-seconds", "0", "--market-hours-only")
    assert rc == 0
    rd = _run_dir(tmp_path)
    ticks = _jsonl(rd / "tick_log.jsonl")
    assert len(ticks) == 1
    assert ticks[0]["status"] == "skipped_market_closed"
    # skipped → no scan → no signal/no-trade ledgers
    assert not (rd / "signal_log.jsonl").exists()
    assert not (rd / "no_trade_log.jsonl").exists()


# ── error / stop / unknown ───────────────────────────────────────────────────

def test_unknown_profile_exits_cleanly(tmp_path):
    rc = _run(tmp_path, "--profile", "no_such_profile_zzz", "--once")
    assert rc == 2
    assert not glob.glob(str(tmp_path / "runs" / "*"))   # no run folder


def test_scanner_failure_marks_error(tmp_path, monkeypatch):
    rs = importlib.import_module("scripts.run_scanner")
    monkeypatch.setattr(rs, "main", lambda argv=None: 3)   # simulate scanner failure
    rc = _run(tmp_path, "--profile", "vertical_wing_score_best_1dte", "--once",
              "--interval-seconds", "0")
    assert rc == 1
    rd = _run_dir(tmp_path)
    man = json.loads((rd / "run_manifest.json").read_text())
    assert man["status"] == "error"
    tick = _jsonl(rd / "tick_log.jsonl")[0]
    assert tick["status"] == "error"
    assert tick["scanner_return_code"] == 3


def test_keyboardinterrupt_marks_stopped(tmp_path, monkeypatch):
    rs = importlib.import_module("scripts.run_scanner")

    def _boom(argv=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr(rs, "main", _boom)
    rc = _run(tmp_path, "--profile", "vertical_wing_score_best_1dte", "--max-ticks", "3",
              "--interval-seconds", "0")
    assert rc == 0
    man = json.loads((_run_dir(tmp_path) / "run_manifest.json").read_text())
    assert man["status"] == "stopped"


# ── no execution surface ─────────────────────────────────────────────────────

def test_no_execution_surface_in_forward_runner():
    src = (Path(__file__).resolve().parents[1] / "scripts" / "run_forward.py").read_text(encoding="utf-8")
    for forbidden in ("submit_order", "place_order", "preview_order", "create_order",
                      "broker.", "execute_trade"):
        assert forbidden not in src, f"forward runner must not reference {forbidden!r}"
    assert rf.NO_EXECUTION is True
    assert rf.EXECUTION_MODE == "disabled_local_monitoring"
