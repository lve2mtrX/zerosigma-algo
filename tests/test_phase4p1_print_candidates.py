"""Phase 4.1 — --print-candidates CLI flag smoke.

NO network. Monkey-patches the structure + quote provider so main() exits
0 with deterministic output, then captures stdout and asserts the per-
candidate blocks contain the new fields.

Also asserts NO secret-shaped strings appear in stdout (tokens, headers).
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout

import pytest


def _run_scanner(monkeypatch, argv: list[str]) -> tuple[int, str]:
    """Run scripts.run_scanner.main() with captured stdout. Returns (rc, stdout)."""
    monkeypatch.setattr(sys, "argv", argv)
    # Re-import in case it was already imported with old argv
    import importlib
    rs = importlib.import_module("scripts.run_scanner")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = rs.main()
    return rc, buf.getvalue()


def test_print_candidates_flag_produces_blocks(monkeypatch):
    """--print-candidates --dry-run --quote-provider mock should print at
    least one block with the Phase 4.1 group headers."""
    monkeypatch.delenv("ZS_STRUCTURE_PROVIDER", raising=False)
    monkeypatch.delenv("QUOTE_PROVIDER", raising=False)
    rc, out = _run_scanner(monkeypatch, [
        "scripts.run_scanner",
        "--strategy", "vertical_wing_v1",
        "--quote-provider", "mock",
        "--structure-provider", "stub",
        "--dry-run",
        "--print-candidates",
    ])
    assert rc == 0, f"main exited {rc}; stdout: {out}"
    # Header
    assert "===" in out
    # Group headers
    for header in ("--- identity ---", "--- risk ---", "--- score ---",
                   "--- quote ---", "--- selector ---"):
        assert header in out, f"missing group {header!r}"
    # New Phase 4.1 fields present
    for key in ("score_edge", "marginal_score", "quote_quality_bucket",
                "risk_rejection_type", "selector_eligible_base", "selector_blockers",
                "selected_expiry", "target_dte"):
        assert f"{key}=" in out, f"missing field {key!r} in audit block"


def test_print_candidates_no_secret_leaks(monkeypatch):
    """The audit print MUST NOT leak Authorization, tokens, or credentials
    even when run with a real-ish TASTY_* env (we set sham values)."""
    monkeypatch.setenv("TASTY_CLIENT_ID",     "TEST_CLIENT_ID_DO_NOT_USE")
    monkeypatch.setenv("TASTY_CLIENT_SECRET", "TEST_CLIENT_SECRET_DO_NOT_USE")
    monkeypatch.setenv("TASTY_REFRESH_TOKEN", "TEST_REFRESH_TOKEN_DO_NOT_USE")
    monkeypatch.setenv("QUOTE_PROVIDER", "mock")    # force mock anyway

    rc, out = _run_scanner(monkeypatch, [
        "scripts.run_scanner",
        "--strategy", "vertical_wing_v1",
        "--quote-provider", "mock",
        "--structure-provider", "stub",
        "--dry-run",
        "--print-candidates",
    ])
    assert rc == 0
    for forbidden in (
        "TEST_CLIENT_ID_DO_NOT_USE", "TEST_CLIENT_SECRET_DO_NOT_USE",
        "TEST_REFRESH_TOKEN_DO_NOT_USE",
        "Authorization:", "Bearer ",
    ):
        assert forbidden not in out, f"audit print leaked {forbidden!r}"


@pytest.mark.parametrize("flag", ["--print-candidates"])
def test_help_lists_print_candidates_flag(flag, monkeypatch):
    """argparse --help must surface the new flag so operators can discover it."""
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
    assert out.returncode == 0, out.stderr
    assert flag in out.stdout
