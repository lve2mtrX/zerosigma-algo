"""Phase 9C — safe local-runner control helpers for the Streamlit cockpit.

Thin, testable guards over the Phase 9A process-control module
(src/forward/control.py). LOCAL MONITORING ONLY — these start/stop/inspect a local
background ``run_forward`` monitor. They NEVER place orders, submit paper orders,
call order preview, select a broker account, or execute anything. There is no
brokerage here.

The helpers call ``control.*`` so tests can monkeypatch ``control_ui.control`` to
exercise the refuse-second-runner / graceful-stop logic without spawning a real
process."""

from __future__ import annotations

from typing import Any

from src.forward import control

EXECUTION_BANNER = "LOCAL MONITORING ONLY — NO BROKER EXECUTION"

# stored statuses that mean a live (or transitional) runner exists
_LIVE_STATUSES = frozenset({"starting", "running", "stopping"})


def get_status(root: Any = None) -> dict[str, Any]:
    """Reconciled control status (delegates to control.status)."""
    return control.status(root)


def status_view(status: dict[str, Any]) -> dict[str, Any]:
    """Map a control-status dict to display-friendly fields (pure)."""
    st = status or {}
    state = st.get("status") or "stopped"
    active = bool(st.get("active"))
    badge = {
        "running": "green", "starting": "green", "stopping": "amber",
        "stale": "red", "error": "red", "stopped": "ghost", "completed": "blue",
    }.get(state, "ghost")
    return {
        "status": state,
        "active": active,
        "pid": st.get("pid"),
        "pid_alive": st.get("pid_alive"),
        "run_id": st.get("run_id"),
        "profile_id": st.get("profile_id"),
        "stale": state == "stale",
        "badge": badge,
        "no_execution": st.get("no_execution", True),
        "execution_mode": st.get("execution_mode"),
    }


def can_start(status: dict[str, Any]) -> tuple[bool, str]:
    """Pure guard: may a new runner start given current status?

    Refuses if a live/transitional runner exists, or if the state is stale (a
    dead PID whose files must be cleaned first)."""
    st = status or {}
    if bool(st.get("active")) or st.get("pid_alive"):
        return False, (f"a runner is already active (pid {st.get('pid')}, "
                       f"run {st.get('run_id')}). Stop it before starting another.")
    if (st.get("status") or "stopped") in _LIVE_STATUSES:
        return False, f"runner state is '{st.get('status')}' — refuse to start a second one."
    if (st.get("status")) == "stale":
        return False, "control state is STALE (dead PID). Run cleanup-stale first."
    return True, "ok"


def start_runner(profile: str, *, root: Any = None, interval_seconds: float | None = None,
                 once: bool = False, max_ticks: int | None = None,
                 market_hours_only: bool = False, quote_provider: str | None = None,
                 structure_provider: str | None = None) -> tuple[bool, str, int | None]:
    """Guarded start: refuses a second live runner, then delegates to
    control.start. Returns (ok, message, pid). Phase 9I — optional
    ``quote_provider`` / ``structure_provider`` overrides (already supported by
    control.start) let the Tester run a profile under the APP data source."""
    if not profile:
        return False, "select a run profile first", None
    ok, why = can_start(control.status(root))
    if not ok:
        return False, why, None
    return control.start(
        profile, root=root, interval_seconds=interval_seconds, once=once,
        max_ticks=max_ticks, market_hours_only=market_hours_only,
        quote_provider=quote_provider, structure_provider=structure_provider,
    )


def stop_runner(*, root: Any = None, force: bool = False) -> tuple[bool, str]:
    """Graceful stop (writes the stop sentinel). ``force=True`` additionally
    terminates ONLY the stored PID — gate this behind an explicit UI checkbox."""
    return control.stop(root, force=force)


def cleanup(root: Any = None) -> tuple[bool, str]:
    """Remove stale pid/state/stop files (only when no live runner)."""
    return control.cleanup_stale(root)


def safe_command(profile: str, *, interval_seconds: float | None = None,
                 once: bool = False, market_hours_only: bool = False,
                 quote_provider: str | None = None,
                 structure_provider: str | None = None) -> str:
    """A copy-pasteable run_forward command (NOT launched). Never includes
    secrets or execution intent. Phase 9I — reflects the run-source provider
    overrides so the printed command matches what the buttons run."""
    argv = control.build_command(
        profile, interval_seconds=interval_seconds, once=once,
        market_hours_only=market_hours_only,
        quote_provider=quote_provider, structure_provider=structure_provider,
    )
    return " ".join(argv)
