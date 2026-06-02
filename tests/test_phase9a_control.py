"""Phase 9A — forward-runner process control. NO network, NO creds, NO execution.

PID liveness / process launch are mocked (monkeypatch control._pid_alive,
control._terminate_pid, control.subprocess.Popen) so tests are deterministic and
never spawn or kill a real process. The run_forward stop/control integration is
exercised in-process against tmp dirs.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from src.forward import control

run_forward = importlib.import_module("scripts.run_forward")
control_cli = importlib.import_module("scripts.control_forward")


def _write_state(tmp_path: Path, **over) -> dict:
    st = {
        "active": True, "pid": 4242, "run_id": "20260101_000000_x",
        "profile_id": "vertical_wing_no_trade", "profile_name": "P",
        "profile_hash": "abc", "status": "running",
        "started_at": "2026-01-01T00:00:00", "last_seen_at": "2026-01-01T00:00:05",
        "latest_heartbeat_path": str(tmp_path / "latest" / "heartbeat.json"),
        "latest_manifest_path": str(tmp_path / "latest" / "run_manifest.json"),
        "no_execution": True, "execution_mode": "disabled_local_monitoring",
    }
    st.update(over)
    control.write_control_state(st, tmp_path)
    control.control_paths(tmp_path)["pid_file"].write_text(str(st["pid"]), encoding="utf-8")
    return st


# ── status ───────────────────────────────────────────────────────────────────

def test_status_no_state_is_stopped(tmp_path):
    st = control.status(tmp_path)
    assert st["active"] is False
    assert st["status"] == "stopped"
    assert st["pid"] is None
    assert st["no_execution"] is True


def test_status_alive_is_running(tmp_path, monkeypatch):
    _write_state(tmp_path, status="running", pid=4242)
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    st = control.status(tmp_path)
    assert st["active"] is True
    assert st["status"] == "running"
    assert st["pid"] == 4242


def test_status_dead_pid_is_stale(tmp_path, monkeypatch):
    _write_state(tmp_path, status="running", pid=4242)
    monkeypatch.setattr(control, "_pid_alive", lambda pid: False)
    st = control.status(tmp_path)
    assert st["active"] is False
    assert st["status"] == "stale"


# ── cleanup-stale ────────────────────────────────────────────────────────────

def test_cleanup_stale_removes_dead(tmp_path, monkeypatch):
    _write_state(tmp_path, pid=4242)
    control.request_stop(tmp_path)
    monkeypatch.setattr(control, "_pid_alive", lambda pid: False)
    ok, _msg = control.cleanup_stale(tmp_path)
    assert ok is True
    paths = control.control_paths(tmp_path)
    assert not paths["state_file"].exists()
    assert not paths["pid_file"].exists()
    assert not paths["stop_file"].exists()


def test_cleanup_stale_refuses_when_alive(tmp_path, monkeypatch):
    _write_state(tmp_path, pid=4242)
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    ok, msg = control.cleanup_stale(tmp_path)
    assert ok is False
    assert "ALIVE" in msg
    assert control.control_paths(tmp_path)["state_file"].exists()


# ── command (no launch) ──────────────────────────────────────────────────────

def test_command_prints_safe_command_no_launch(tmp_path, capsys):
    rc = control_cli.main(["--forward-root", str(tmp_path), "command",
                           "--profile", "vertical_wing_score_best_1dte",
                           "--interval-seconds", "60", "--market-hours-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "run_forward" in out and "vertical_wing_score_best_1dte" in out
    assert "--market-hours-only" in out
    # command must NOT create any control state / launch anything
    assert not control.control_paths(tmp_path)["state_file"].exists()
    assert not control.control_paths(tmp_path)["pid_file"].exists()


# ── start (mocked Popen) ─────────────────────────────────────────────────────

class _FakePopen:
    def __init__(self, argv, **kw):
        self.args = argv
        self.pid = 4242
        self._kw = kw


def test_start_creates_pid_state_logs(tmp_path, monkeypatch):
    captured = {}

    def _fake(argv, **kw):
        captured["argv"] = argv
        return _FakePopen(argv, **kw)

    monkeypatch.setattr(control.subprocess, "Popen", _fake)
    monkeypatch.setattr(control, "_pid_alive", lambda pid: False)  # nothing running yet
    ok, _msg, pid = control.start("vertical_wing_no_trade", root=tmp_path, once=True)
    assert ok is True and pid == 4242
    paths = control.control_paths(tmp_path)
    assert paths["pid_file"].read_text().strip() == "4242"
    state = json.loads(paths["state_file"].read_text())
    assert state["status"] == "starting" and state["pid"] == 4242
    assert state["no_execution"] is True
    # launched argv targets run_forward + wires control/stop paths
    av = captured["argv"]
    assert any("run_forward" in str(a) for a in av)
    assert "--control-state-path" in av and "--stop-file" in av
    assert "--profile" in av and "vertical_wing_no_trade" in av
    # log files created
    assert list((paths["logs_dir"]).glob("*.out.log"))
    assert list((paths["logs_dir"]).glob("*.err.log"))


def test_start_refuses_when_active(tmp_path, monkeypatch):
    _write_state(tmp_path, pid=4242)
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    called = {"popen": False}
    monkeypatch.setattr(control.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", True))
    ok, msg, _pid = control.start("vertical_wing_no_trade", root=tmp_path, once=True)
    assert ok is False
    assert "already active" in msg
    assert called["popen"] is False    # never launched a second runner


# ── stop / force ─────────────────────────────────────────────────────────────

def test_stop_writes_stop_sentinel(tmp_path, monkeypatch):
    _write_state(tmp_path, pid=4242, status="running")
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    ok, _msg = control.stop(tmp_path)
    assert ok is True
    assert control.control_paths(tmp_path)["stop_file"].is_file()
    assert json.loads(control.control_paths(tmp_path)["state_file"].read_text())["status"] == "stopping"


def test_force_stop_targets_only_stored_pid(tmp_path, monkeypatch):
    _write_state(tmp_path, pid=4242, status="running")
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    killed = []
    monkeypatch.setattr(control, "_terminate_pid", lambda pid: killed.append(pid) or True)
    ok, _msg = control.stop(tmp_path, force=True)
    assert ok is True
    assert killed == [4242]        # ONLY our stored pid, nothing else


def test_stop_no_active_runner_is_noop(tmp_path, monkeypatch):
    _write_state(tmp_path, pid=4242, status="running")
    monkeypatch.setattr(control, "_pid_alive", lambda pid: False)
    ok, msg = control.stop(tmp_path)
    assert ok is False
    assert "not alive" in msg


# ── run_forward stop/control integration ─────────────────────────────────────

def test_run_forward_exits_on_stop_file(tmp_path):
    stop_file = tmp_path / "stop.json"
    stop_file.write_text("{}", encoding="utf-8")   # stop already requested
    rc = run_forward.main([
        "--profile", "vertical_wing_no_trade", "--output-dir", str(tmp_path / "fwd"),
        "--max-ticks", "5", "--interval-seconds", "0", "--stop-file", str(stop_file),
    ])
    assert rc == 0
    man = json.loads((tmp_path / "fwd" / "latest" / "run_manifest.json").read_text())
    assert man["status"] == "stopped"
    # broke before any tick ran
    assert not (tmp_path / "fwd" / "latest" / "heartbeat.json").read_text() == ""


def test_run_forward_updates_control_state(tmp_path):
    state_path = tmp_path / "control_state.json"
    rc = run_forward.main([
        "--profile", "vertical_wing_no_trade", "--output-dir", str(tmp_path / "fwd"),
        "--once", "--interval-seconds", "0", "--control-state-path", str(state_path),
    ])
    assert rc == 0
    st = json.loads(state_path.read_text())
    assert st["status"] == "completed"
    assert st["active"] is False
    assert st["run_id"]


def test_run_forward_standalone_unchanged(tmp_path):
    # No control args → exact Phase 7 behavior (no control_state written anywhere).
    rc = run_forward.main([
        "--profile", "vertical_wing_no_trade", "--output-dir", str(tmp_path / "fwd"),
        "--once", "--interval-seconds", "0",
    ])
    assert rc == 0
    assert not (tmp_path / "fwd" / "control").exists()


# ── CLI arg validation + no execution ────────────────────────────────────────

def test_cli_start_requires_profile():
    with pytest.raises(SystemExit):
        control_cli.main(["start"])


def test_cli_status_runs(tmp_path, capsys):
    rc = control_cli.main(["--forward-root", str(tmp_path), "status"])
    assert rc == 0
    assert "control status" in capsys.readouterr().out


def test_no_execution_surface():
    root = Path(__file__).resolve().parents[1]
    for rel in ("src/forward/control.py", "scripts/control_forward.py"):
        src = (root / rel).read_text(encoding="utf-8")
        for forbidden in ("submit_order", "place_order", "preview_order",
                          "create_order", "broker.", "execute_trade"):
            assert forbidden not in src, f"{rel} must not reference {forbidden!r}"
