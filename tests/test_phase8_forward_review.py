"""Phase 8 — forward-run review (read-only). NO network, NO creds, NO execution.

Seeds real ledgers via the Phase 7 forward runner into a tmp --output-dir, then
exercises src.forward.review + scripts.review_forward against them (via the
--forward-root / root override). Also covers hand-built minimal/missing-file runs.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from src.forward import review

run_forward = importlib.import_module("scripts.run_forward")
review_cli = importlib.import_module("scripts.review_forward")


def _seed(tmp_path: Path, profile: str, *extra) -> Path:
    """Run the forward runner into tmp_path; return the forward root (tmp_path)."""
    rc = run_forward.main(["--profile", profile, "--output-dir", str(tmp_path),
                           "--interval-seconds", "0", *extra])
    assert rc == 0
    return tmp_path


def _minimal_run(tmp_path: Path, run_id: str, **manifest_over) -> Path:
    rd = tmp_path / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    man = {"run_id": run_id, "profile_id": "p", "profile_name": "P",
           "profile_hash": "abc", "status": "completed", "started_at": "2026-01-01T00:00:00",
           "ended_at": "2026-01-01T00:00:01", "interval_seconds": 0,
           "daily_selector": "no_trade", "target_dte": 0, "no_execution": True}
    man.update(manifest_over)
    (rd / "run_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return rd


# ── discovery ────────────────────────────────────────────────────────────────

def test_discover_runs_sorted_newest_first(tmp_path):
    _minimal_run(tmp_path, "20260101_000001_a")
    _minimal_run(tmp_path, "20260102_000002_b")
    runs = review.discover_runs(tmp_path)
    assert [p.name for p in runs] == ["20260102_000002_b", "20260101_000001_a"]


def test_discover_runs_empty_root_is_clean(tmp_path):
    assert review.discover_runs(tmp_path / "nope") == []


# ── latest heartbeat / manifest / pointer ───────────────────────────────────

def test_load_latest_heartbeat_and_manifest(tmp_path):
    _seed(tmp_path, "vertical_wing_no_trade", "--once")
    hb = review.load_latest_heartbeat(tmp_path)
    man = review.load_latest_manifest(tmp_path)
    ptr = review.load_latest_pointer(tmp_path)
    assert hb and hb.get("status") in ("completed", "running")
    assert man and man.get("profile_id") == "vertical_wing_no_trade"
    assert ptr and ptr.get("run_id") == man.get("run_id")


def test_resolve_latest_alias(tmp_path):
    _seed(tmp_path, "vertical_wing_no_trade", "--once")
    rd = review.resolve_run_dir("latest", tmp_path)
    assert rd is not None and rd.is_dir()


# ── summaries ────────────────────────────────────────────────────────────────

def test_summarize_run_with_signals_and_dupes(tmp_path):
    _seed(tmp_path, "vertical_wing_score_best_1dte", "--max-ticks", "2")
    s = review.summarize_run("latest", tmp_path)
    assert s is not None
    assert s["tick_count"] == 2
    assert s["signal_count"] == 1           # dedup: one signal across two ticks
    assert s["duplicate_signal_count"] == 1
    assert s["no_trade_count"] == 0
    assert s["error_count"] == 0
    assert s["latest_selected_trade"] is True
    assert len(s["selected_trade_summaries"]) == 1
    assert s["selected_trade_summaries"][0]["side"] in ("CALL_CREDIT", "PUT_CREDIT")


def test_summarize_run_no_trade(tmp_path):
    _seed(tmp_path, "vertical_wing_no_trade", "--once")
    s = review.summarize_run("latest", tmp_path)
    assert s["signal_count"] == 0
    assert s["no_trade_count"] == 1
    assert s["latest_no_trade_reason"]
    assert s["selected_trade_summaries"] == []


def test_summarize_missing_optional_files_clean(tmp_path):
    _minimal_run(tmp_path, "20260101_000001_min")   # manifest only, no logs
    s = review.summarize_run("20260101_000001_min", tmp_path)
    assert s is not None
    assert s["tick_count"] == 0 and s["signal_count"] == 0
    assert s["no_trade_count"] == 0 and s["duplicate_signal_count"] == 0
    assert s["error_count"] == 0
    assert s["selected_trade_summaries"] == []


def test_selected_trades_csv_loaded(tmp_path):
    _seed(tmp_path, "vertical_wing_score_best_1dte", "--once")
    rows = review.load_selected_trades("latest", tmp_path)
    assert rows and rows[0]["side"] in ("CALL_CREDIT", "PUT_CREDIT")


def test_summarize_unknown_run_is_none(tmp_path):
    assert review.summarize_run("does_not_exist", tmp_path) is None


# ── review CLI ───────────────────────────────────────────────────────────────

def test_cli_list(tmp_path, capsys):
    _seed(tmp_path, "vertical_wing_no_trade", "--once")
    rc = review_cli.main(["--list", "--forward-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vertical_wing_no_trade" in out


def test_cli_latest(tmp_path, capsys):
    _seed(tmp_path, "vertical_wing_score_best_1dte", "--once")
    rc = review_cli.main(["--latest", "--forward-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "forward run:" in out
    assert "no_execution:" in out


def test_cli_run_and_signals(tmp_path, capsys):
    _seed(tmp_path, "vertical_wing_score_best_1dte", "--once")
    rid = review.load_latest_manifest(tmp_path)["run_id"]
    assert review_cli.main(["--run", rid, "--forward-root", str(tmp_path)]) == 0
    capsys.readouterr()
    rc = review_cli.main(["--signals", rid, "--forward-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "signals:" in out


def test_cli_missing_run_exits_cleanly(tmp_path, capsys):
    assert review_cli.main(["--run", "nope_zzz", "--forward-root", str(tmp_path)]) == 1
    assert review_cli.main(["--signals", "nope_zzz", "--forward-root", str(tmp_path)]) == 1


def test_cli_export_summary_writes_json(tmp_path):
    _seed(tmp_path, "vertical_wing_score_best_1dte", "--once")
    out = tmp_path / "summary.json"
    rc = review_cli.main(["--export-summary", "latest", "--output", str(out),
                          "--forward-root", str(tmp_path)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["run_id"] and data["tick_count"] == 1
    assert data["no_execution"] is True


# ── no execution surface ─────────────────────────────────────────────────────

def test_no_execution_surface():
    root = Path(__file__).resolve().parents[1]
    for rel in ("src/forward/review.py", "scripts/review_forward.py"):
        src = (root / rel).read_text(encoding="utf-8")
        for forbidden in ("submit_order", "place_order", "preview_order",
                          "create_order", "broker.", "execute_trade", "subprocess"):
            assert forbidden not in src, f"{rel} must not reference {forbidden!r}"


def test_streamlit_parses_and_imports_review():
    import ast
    root = Path(__file__).resolve().parents[1]
    src = (root / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")
    ast.parse(src)   # no syntax error
    assert "from src.forward import review" in src
