"""Local process control for the forward runner — Phase 9A.

Safe start / stop / status for a SINGLE local background forward-run process.
LOCAL PROCESS CONTROL ONLY — it launches/monitors `scripts.run_forward` (the
Phase 7 monitor), never a brokerage. NO execution, NO orders, NO order preview.
`subprocess` here spawns the *monitor*, not any trading action.

State lives under `outputs/forward/control/`:
  forward_runner.pid     – the background runner's PID (plain int)
  control_state.json     – the structured control state (schema below)
  stop_requested.json    – graceful-stop sentinel the runner polls each tick
  logs/{ts}_{profile}.{out,err}.log – captured stdout/stderr of a controlled start

PID liveness uses os.kill(pid,0) on POSIX and a non-destructive ctypes
OpenProcess/GetExitCodeProcess probe on Windows (no signal sent, no admin). Both
are wrapped in `_pid_alive`, which tests monkeypatch for determinism.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_MODE = "disabled_local_monitoring"

# Windows process-query constants (used by _pid_alive on nt).
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_STILL_ACTIVE = 259


def forward_root(root: Path | str | None = None) -> Path:
    if root is None:
        return REPO_ROOT / "outputs" / "forward"
    p = Path(root)
    return p if p.is_absolute() else (REPO_ROOT / p)


def control_dir(root: Path | str | None = None) -> Path:
    return forward_root(root) / "control"


def control_paths(root: Path | str | None = None) -> dict[str, Path]:
    cd = control_dir(root)
    return {
        "control_dir": cd,
        "pid_file": cd / "forward_runner.pid",
        "state_file": cd / "control_state.json",
        "stop_file": cd / "stop_requested.json",
        "logs_dir": cd / "logs",
    }


# ── tolerant IO ──────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)


# ── PID liveness / termination (monkeypatched in tests) ─────────────────────

def _pid_alive(pid: int | None) -> bool:
    """True if `pid` refers to a live process. Cross-platform, non-destructive,
    no admin. (Windows GetExitCodeProcess STILL_ACTIVE has a rare false-positive
    when a process legitimately exits with code 259 — acceptable here.)"""
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_WIN_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == _WIN_STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _terminate_pid(pid: int) -> bool:
    """Best-effort terminate. Caller MUST verify pid == our stored control PID
    AND _pid_alive(pid) first. Returns True if a terminate was issued."""
    import signal
    try:
        os.kill(int(pid), signal.SIGTERM)
        return True
    except (ProcessLookupError, OSError):
        return False


# ── control state read/write ────────────────────────────────────────────────

def read_control_state(root: Path | str | None = None) -> dict[str, Any] | None:
    return _read_json(control_paths(root)["state_file"])


def write_control_state(state: dict, root: Path | str | None = None) -> None:
    _write_json(control_paths(root)["state_file"], state)


def update_control_state(state_path: Path | str, **fields: Any) -> dict[str, Any]:
    """Merge `fields` into the control state at an EXPLICIT path (used by the
    runner via --control-state-path). Creates the file if missing."""
    p = Path(state_path)
    state = _read_json(p) or {}
    state.update(fields)
    _write_json(p, state)
    return state


def read_pid(root: Path | str | None = None) -> int | None:
    p = control_paths(root)["pid_file"]
    if not p.is_file():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def request_stop(root: Path | str | None = None, *, reason: str = "user_requested") -> Path:
    from datetime import datetime
    paths = control_paths(root)
    _write_json(paths["stop_file"], {
        "stop_requested": True, "reason": reason,
        "requested_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    })
    return paths["stop_file"]


def clear_stop(root: Path | str | None = None) -> None:
    sp = control_paths(root)["stop_file"]
    try:
        sp.unlink()
    except OSError:
        pass


# ── status ───────────────────────────────────────────────────────────────────

def status(root: Path | str | None = None) -> dict[str, Any]:
    """Reconcile stored control state with live PID liveness + latest heartbeat.
    Returns a clean dict even when nothing has ever run."""
    paths = control_paths(root)
    state = read_control_state(root)
    if not state:
        return {
            "active": False, "status": "stopped", "pid": None, "run_id": None,
            "profile_id": None, "message": "no control state (no runner has been started)",
            "no_execution": True, "execution_mode": EXECUTION_MODE,
        }

    pid = state.get("pid")
    alive = _pid_alive(pid)
    stored_status = state.get("status")
    if alive:
        eff_status = stored_status if stored_status in ("starting", "running", "stopping") else "running"
        active = True
    else:
        # PID gone: completed/stopped/error are terminal; anything else is stale.
        eff_status = stored_status if stored_status in ("completed", "stopped", "error") else "stale"
        active = False

    hb = _read_json(Path(state["latest_heartbeat_path"])) if state.get("latest_heartbeat_path") else None
    return {
        "active": active,
        "status": eff_status,
        "stored_status": stored_status,
        "pid": pid,
        "pid_alive": alive,
        "run_id": state.get("run_id"),
        "profile_id": state.get("profile_id"),
        "profile_name": state.get("profile_name"),
        "profile_hash": state.get("profile_hash"),
        "command": state.get("command"),
        "started_at": state.get("started_at"),
        "last_seen_at": state.get("last_seen_at"),
        "latest_heartbeat_path": state.get("latest_heartbeat_path"),
        "latest_manifest_path": state.get("latest_manifest_path"),
        "latest_decision": (hb or {}).get("latest_decision"),
        "latest_tick_time": (hb or {}).get("latest_tick_time"),
        "latest_selected_trade": (hb or {}).get("selected_trade"),
        "stop_requested": paths["stop_file"].is_file(),
        "no_execution": True,
        "execution_mode": EXECUTION_MODE,
    }


def cleanup_stale(root: Path | str | None = None) -> tuple[bool, str]:
    """Remove pid/state/stop files ONLY if no live runner. Refuses if alive."""
    paths = control_paths(root)
    state = read_control_state(root)
    if not state:
        # also clear an orphan pid/stop file if present
        for key in ("pid_file", "stop_file"):
            try:
                paths[key].unlink()
            except OSError:
                pass
        return False, "no control state to clean"
    if _pid_alive(state.get("pid")):
        return False, f"runner pid {state.get('pid')} is ALIVE — refusing to clean (use stop)"
    for key in ("state_file", "pid_file", "stop_file"):
        try:
            paths[key].unlink()
        except OSError:
            pass
    return True, f"cleaned stale control files (pid {state.get('pid')} not alive)"


# ── command building / start / stop ─────────────────────────────────────────

def build_command(
    profile: str, *, interval_seconds: float | None = None, once: bool = False,
    max_ticks: int | None = None, market_hours_only: bool = False,
    quote_provider: str | None = None, structure_provider: str | None = None,
    output_dir: str | None = None, python_exe: str | None = None,
    control_state_path: str | None = None, stop_file: str | None = None,
) -> list[str]:
    """Build the run_forward argv (same interpreter/venv). NO secrets, NO orders."""
    argv = [python_exe or sys.executable, "-m", "scripts.run_forward", "--profile", profile]
    if once:
        argv.append("--once")
    if max_ticks is not None:
        argv += ["--max-ticks", str(max_ticks)]
    if interval_seconds is not None:
        argv += ["--interval-seconds", str(interval_seconds)]
    if market_hours_only:
        argv.append("--market-hours-only")
    if quote_provider:
        argv += ["--quote-provider", quote_provider]
    if structure_provider:
        argv += ["--structure-provider", structure_provider]
    if output_dir:
        argv += ["--output-dir", output_dir]
    if control_state_path:
        argv += ["--control-state-path", str(control_state_path)]
    if stop_file:
        argv += ["--stop-file", str(stop_file)]
    return argv


def start(
    profile: str, *, root: Path | str | None = None, interval_seconds: float | None = None,
    once: bool = False, max_ticks: int | None = None, market_hours_only: bool = False,
    quote_provider: str | None = None, structure_provider: str | None = None,
) -> tuple[bool, str, int | None]:
    """Launch a background forward runner. Refuses if one is already alive.

    Returns (ok, message, pid). Uses the current interpreter; logs go under
    control/logs/. Detached so it survives the launching shell."""
    from datetime import datetime

    from src.config.strategy_profiles import load_profile_file

    res = load_profile_file(profile)
    if not res.ok or res.profile is None:
        return False, f"profile {profile!r} invalid/not found: {res.errors}", None
    prof = res.profile

    existing = read_control_state(root)
    if existing and _pid_alive(existing.get("pid")):
        return (False,
                f"a forward runner is already active (pid {existing.get('pid')}, "
                f"run_id {existing.get('run_id')}). Stop it first.",
                existing.get("pid"))

    paths = control_paths(root)
    paths["control_dir"].mkdir(parents=True, exist_ok=True)
    paths["logs_dir"].mkdir(parents=True, exist_ok=True)
    clear_stop(root)   # never inherit a previous stop sentinel

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_log = paths["logs_dir"] / f"{ts}_{prof.profile_id}.out.log"
    err_log = paths["logs_dir"] / f"{ts}_{prof.profile_id}.err.log"

    fr = forward_root(root)
    argv = build_command(
        profile, interval_seconds=interval_seconds, once=once, max_ticks=max_ticks,
        market_hours_only=market_hours_only, quote_provider=quote_provider,
        structure_provider=structure_provider, output_dir=str(fr),
        control_state_path=str(paths["state_file"]), stop_file=str(paths["stop_file"]),
    )

    creationflags = 0
    popen_kwargs: dict[str, Any] = {"cwd": str(REPO_ROOT)}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survives the shell, own group.
        creationflags = 0x00000008 | 0x00000200
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    out_fh = out_log.open("w", encoding="utf-8")
    err_fh = err_log.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(argv, stdout=out_fh, stderr=err_fh, **popen_kwargs)
    except Exception as exc:                       # pragma: no cover - defensive
        out_fh.close()
        err_fh.close()
        return False, f"failed to launch runner: {type(exc).__name__}: {exc}", None

    pid = proc.pid
    paths["pid_file"].write_text(str(pid), encoding="utf-8")
    state = {
        "active": True,
        "pid": pid,
        "run_id": None,                  # the runner fills this once it starts ticking
        "profile_id": prof.profile_id,
        "profile_name": prof.profile_name,
        "profile_hash": prof.profile_hash(),
        "command": argv,
        "started_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "last_seen_at": None,
        "status": "starting",
        "latest_heartbeat_path": str(fr / "latest" / "heartbeat.json"),
        "latest_manifest_path": str(fr / "latest" / "run_manifest.json"),
        "out_log": str(out_log),
        "err_log": str(err_log),
        "no_execution": True,
        "execution_mode": EXECUTION_MODE,
    }
    write_control_state(state, root)
    return True, f"started forward runner pid {pid} (logs: {out_log})", pid


def stop(root: Path | str | None = None, *, force: bool = False) -> tuple[bool, str]:
    """Graceful stop (writes stop_requested.json the runner polls). With force,
    ALSO terminate — but ONLY the PID stored in our control state, and only if alive."""
    state = read_control_state(root)
    if not state:
        return False, "no control state — nothing to stop"
    pid = state.get("pid")
    alive = _pid_alive(pid)
    if not alive:
        return False, f"stored runner pid {pid} is not alive — nothing to stop (try cleanup-stale)"

    request_stop(root)
    update_control_state(control_paths(root)["state_file"], status="stopping")
    msg = f"requested graceful stop for pid {pid} (stop_requested.json written)"

    if force:
        # Defensive: only ever target OUR stored pid, and only if still alive.
        if pid and _pid_alive(pid):
            ok = _terminate_pid(int(pid))
            msg += f"; force-terminate pid {pid} issued={ok}"
    return True, msg
