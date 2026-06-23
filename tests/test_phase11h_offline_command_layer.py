from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

from scripts import review_notification_dry_run, review_profile_readiness
from src.reviews.notification_dry_run import (
    build_notification_dry_run,
    sample_alert_events,
    write_notification_dry_run,
)
from src.reviews.operator_command import (
    PRIMARY_BENCHMARK,
    SECONDARY_BENCHMARK,
    build_operator_status,
    build_profile_readiness_matrix,
    write_operator_command_artifacts,
)

REPO = Path(__file__).resolve().parents[1]

REVIEW_SOURCE_FILES = (
    "scripts/review_profile_readiness.py",
    "scripts/review_notification_dry_run.py",
    "src/reviews/operator_command.py",
    "src/reviews/notification_dry_run.py",
)

# Import targets that would let an offline module reach a real sender or the network.
_FORBIDDEN_IMPORT_PREFIXES = (
    "src.notifications", "httpx", "requests", "urllib", "urllib3",
    "http.client", "aiohttp", "socket",
)
_FORBIDDEN_IMPORT_NAMES = (
    "notifications", "httpx", "requests", "urllib", "urllib3", "aiohttp", "socket",
)
# Method/function names that imply delivery, order routing, or execution, regardless of receiver.
_FORBIDDEN_CALL_NAMES = (
    "send", "post", "submit_order", "place_order",
    "preview_order", "order_preview", "execute_trade",
)
# Substrings that, in a *called name* inside the cockpit surface, would imply an action.
_FORBIDDEN_COCKPIT_CALL_TOKENS = (
    "button", "send", "submit", "execute", "preview", "order", "broker", "playback",
)
# Structural action surfaces checked against the AST-bounded function source segment.
_FORBIDDEN_COCKPIT_SEGMENT_TOKENS = (
    "subprocess", "os.system", "st.button", "download_button",
    "link_button", "form_submit_button", "on_click",
)


def _matches_prefix(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _callable_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _forbidden_import_hits(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(_matches_prefix(alias.name, p) for p in _FORBIDDEN_IMPORT_PREFIXES):
                    hits.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (any(_matches_prefix(module, p) for p in _FORBIDDEN_IMPORT_PREFIXES)
                    or "notifications" in module.split(".")):
                hits.append(f"from {module or '.'} import ...")
            for alias in node.names:
                if alias.name in _FORBIDDEN_IMPORT_NAMES:
                    hits.append(f"from {module or '.'} import {alias.name}")
    return hits


def _forbidden_call_hits(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callable_name(node.func)
        if name in _FORBIDDEN_CALL_NAMES:
            hits.append(f"{name}()")
        # getattr(obj, "send")(...) with a literal forbidden attribute name.
        if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            target = node.args[1]
            if isinstance(target, ast.Constant) and target.value in _FORBIDDEN_CALL_NAMES:
                hits.append(f"getattr(..., {target.value!r})")
    return hits


def test_profile_matrix_reads_all_local_profiles_and_stays_offline():
    matrix = build_profile_readiness_matrix(generated_at="2026-06-22T20:00:00-04:00")
    assert matrix["profile_count"] == 14
    assert matrix["invalid_profile_count"] == 0
    assert matrix["offline_only"] is True
    assert matrix["live_rth_evidence"] is False
    assert matrix["automatic_promotion"] is False
    assert matrix["benchmark_profile_ids"] == [PRIMARY_BENCHMARK, SECONDARY_BENCHMARK]


def test_profile_matrix_marks_controls_as_benchmarks_not_approved():
    matrix = build_profile_readiness_matrix(generated_at="2026-06-22T20:00:00-04:00")
    rows = {row["profile_id"]: row for row in matrix["rows"]}
    primary = rows[PRIMARY_BENCHMARK]
    secondary = rows[SECONDARY_BENCHMARK]
    assert primary["benchmark_label"] == "Primary RTH benchmark"
    assert secondary["benchmark_label"] == "Secondary RTH benchmark"
    assert primary["backtest_status"] == "Control Positive / Comparison Only"
    assert primary["rth_soak_eligibility"] == "BENCHMARK_PENDING_REAL_RTH_READINESS"
    assert "no_real_rth_soak_evidence" in primary["blocker_reason_codes"]
    assert "profile_disabled" in primary["blocker_reason_codes"]


def test_profile_matrix_includes_required_metadata():
    row = build_profile_readiness_matrix(
        profile_ids={PRIMARY_BENCHMARK},
        generated_at="2026-06-22T20:00:00-04:00",
    )["rows"][0]
    assert row["symbol"] == "SPX" and row["target_dte"] == 0
    assert row["side_policy"] == "call only"
    assert row["selector_mode"] == "call_credit_only"
    assert row["tp_metadata"] == "TP75% credit capture"
    assert row["sl_metadata"] == "SL150% credit loss"
    assert row["provider_mode"] == "offline sandbox profile"
    assert row["forward_paper_status"] == "Fixture paper artifact"


def test_operator_status_separates_offline_rth_execution_and_ml_work():
    matrix = build_profile_readiness_matrix(generated_at="2026-06-22T20:00:00-04:00")
    status = build_operator_status(matrix)
    assert status["evidence_status"] == "NO_REAL_RTH_EVIDENCE_CAPTURED"
    assert status["offline_ready_tasks"]
    assert status["blocked_on_rth_tasks"]
    assert status["deferred_execution_tasks"]
    assert status["deferred_hermes_ml_tasks"]
    assert status["live_rth_evidence"] is False


def test_operator_artifacts_write_latest_and_timestamped_run(tmp_path):
    matrix = build_profile_readiness_matrix(generated_at="2026-06-22T20:00:00-04:00")
    status = build_operator_status(matrix)
    paths = write_operator_command_artifacts(
        matrix, status, output_root=tmp_path, run_id="fixture_operator_run"
    )
    expected = {
        "operator_status.json", "operator_status.md", "profile_readiness_matrix.json",
        "profile_readiness_matrix.md", "profile_readiness_matrix.csv",
    }
    assert expected <= {path.name for path in (tmp_path / "latest").iterdir()}
    assert expected <= {
        path.name for path in (tmp_path / "runs" / "fixture_operator_run").iterdir()
    }
    assert paths["run_id"] == "fixture_operator_run"


def test_profile_readiness_cli_smoke(tmp_path, capsys):
    rc = review_profile_readiness.main([
        "--profiles", f"{PRIMARY_BENCHMARK},{SECONDARY_BENCHMARK}",
        "--output-dir", str(tmp_path), "--json",
    ])
    output = capsys.readouterr().out
    assert rc == 0
    assert "NO_REAL_RTH_EVIDENCE_CAPTURED" in output
    payload = json.loads((tmp_path / "latest" / "profile_readiness_matrix.json").read_text())
    assert payload["profile_count"] == 2


def test_notification_fixture_preview_is_deterministic_and_sends_nothing():
    backend = {
        "global_delivery_enabled": False,
        "cockpit_enabled": True,
        "pushover_enabled": False,
        "voice_enabled": False,
        "default_cooldown_seconds": 300,
        "credential_values_included": False,
    }
    first = build_notification_dry_run(
        sample_alert_events(), input_source="deterministic_fixture", backends=backend,
        generated_at="2026-06-22T20:00:00-04:00",
    )
    second = build_notification_dry_run(
        sample_alert_events(), input_source="deterministic_fixture", backends=backend,
        generated_at="2026-06-22T20:00:00-04:00",
    )
    assert first == second
    assert first["event_count"] == 3
    assert first["preview_row_count"] == 4
    assert first["suppressed_event_count"] == 1
    assert first["dry_run_sent_count"] == 0
    assert all(row["dry_run_sent"] is False for row in first["rows"])
    assert all(row["push_preview_message"] for row in first["rows"])
    assert all(row["voice_preview"] for row in first["rows"])
    # Two non-suppressed ALL-delivery events route to both channels; the duplicate is suppressed.
    assert first["push_route_eligible_events"] == 2
    assert first["voice_route_eligible_events"] == 2


def test_notification_preview_records_cooldown_backend_state_and_failure_boundary():
    report = build_notification_dry_run(
        sample_alert_events(), input_source="deterministic_fixture",
        backends={
            "global_delivery_enabled": False, "cockpit_enabled": True,
            "pushover_enabled": False, "voice_enabled": False,
            "default_cooldown_seconds": 300, "credential_values_included": False,
        },
        generated_at="2026-06-22T20:00:00-04:00",
    )
    suppressed = [row for row in report["rows"] if row["suppressed"]]
    assert suppressed[0]["cooldown_status"] == "suppressed_by_cooldown"
    assert suppressed[0]["push_route_eligible"] is False
    assert report["backend_state"]["pushover_enabled"] is False
    assert "must never block" in report["rows"][0]["failure_handling_note"]


def test_notification_cli_smoke_and_artifacts(tmp_path, capsys):
    rc = review_notification_dry_run.main([
        "--fixture", "sample", "--output-dir", str(tmp_path), "--json",
    ])
    output = capsys.readouterr().out
    assert rc == 0 and "OFFLINE_DRY_RUN_NO_SEND" in output
    expected = {
        "notification_dry_run.json", "notification_dry_run.md",
        "notification_dry_run.csv",
    }
    assert expected <= {path.name for path in (tmp_path / "latest").iterdir()}
    payload = json.loads((tmp_path / "latest" / "notification_dry_run.json").read_text())
    assert payload["dry_run_sent_count"] == 0


def test_notification_writer_creates_timestamped_copy(tmp_path):
    report = build_notification_dry_run(
        sample_alert_events(), input_source="deterministic_fixture",
        generated_at="2026-06-22T20:00:00-04:00",
    )
    paths = write_notification_dry_run(
        report, output_root=tmp_path, run_id="fixture_notification_run"
    )
    assert paths["run_id"] == "fixture_notification_run"
    assert (tmp_path / "runs" / "fixture_notification_run" / "notification_dry_run.json").is_file()


def test_cockpit_has_display_only_phase11h_surfaces():
    source = (REPO / "src/app/streamlit_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "render_offline_command_layer"
    ]
    assert len(functions) == 1, "render_offline_command_layer must be defined exactly once"
    func = functions[0]
    assert func.body, "render_offline_command_layer must have a non-empty body"

    segment = ast.get_source_segment(source, func)
    assert segment, "could not extract render_offline_command_layer source segment"

    # AST: every callable name and keyword inside the surface — alias-robust, ignores strings.
    called_names: list[str] = []
    keyword_names: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            called_names.append(_callable_name(node.func).lower())
            keyword_names.extend(kw.arg for kw in node.keywords if kw.arg)
    offending = [
        name for name in called_names
        if any(token in name for token in _FORBIDDEN_COCKPIT_CALL_TOKENS)
    ]
    assert not offending, f"action-like calls in display-only cockpit surface: {offending}"
    assert "on_click" not in keyword_names
    assert "on_change" not in keyword_names

    # Structural action surfaces, checked against the AST-bounded segment (not a fragile slice).
    lowered = segment.lower()
    for token in _FORBIDDEN_COCKPIT_SEGMENT_TOKENS:
        assert token not in lowered, f"forbidden cockpit action surface: {token}"

    for text in (
        "Operator Status / Roadmap", "Profile Readiness Matrix",
        "Notification / Voice Dry-Run Preview", "Offline local-file review only",
        "Sent / spoken",
    ):
        assert text in source


def test_phase11h_sources_have_no_network_delivery_order_or_execution_paths():
    for rel in REVIEW_SOURCE_FILES:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        import_hits = _forbidden_import_hits(tree)
        call_hits = _forbidden_call_hits(tree)
        assert not import_hits, f"{rel}: forbidden network/notification imports {import_hits}"
        assert not call_hits, f"{rel}: forbidden delivery/order/execution calls {call_hits}"
    matrix = build_profile_readiness_matrix(generated_at="2026-06-22T20:00:00-04:00")
    assert matrix["automatic_promotion"] is False


def test_ast_guardrails_catch_aliased_and_indirect_violations():
    """The hardened guardrails must catch the bypasses a substring denylist would miss."""
    bypasses = (
        "from src import notifications as n",
        "from src.notifications import pushover",
        "import urllib.request",
        "import http.client",
        "import aiohttp",
        "import socket",
    )
    for snippet in bypasses:
        assert _forbidden_import_hits(ast.parse(snippet)), f"missed import bypass: {snippet}"
    for snippet in (
        "backend.send(event)",
        "client.post(url)",
        'getattr(backend, "send")(event)',
        "place_order(ticket)",
    ):
        assert _forbidden_call_hits(ast.parse(snippet)), f"missed call bypass: {snippet}"
    # The clean modules themselves must remain free of any hit.
    for rel in REVIEW_SOURCE_FILES:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        assert not _forbidden_import_hits(tree)
        assert not _forbidden_call_hits(tree)


def test_notification_pushover_only_delivery_action_routes_push_not_voice():
    events = [{
        "timestamp": "2026-06-22T10:30:00-04:00",
        "event_id": "dryrun_push_only",
        "severity": "INFO",
        "source": "PAPER_EXIT",
        "reason_codes": ["take_profit_threshold_hit"],
        "suppressed": False,
        "delivery_action": "PUSHOVER",
    }]
    report = build_notification_dry_run(
        events, input_source="deterministic_fixture",
        backends={
            "global_delivery_enabled": False, "cockpit_enabled": True,
            "pushover_enabled": False, "voice_enabled": False,
            "default_cooldown_seconds": 300, "credential_values_included": False,
        },
        generated_at="2026-06-22T20:00:00-04:00",
    )
    row = report["rows"][0]
    assert row["push_route_eligible"] is True
    assert row["voice_route_eligible"] is False
    assert report["push_route_eligible_events"] == 1
    assert report["voice_route_eligible_events"] == 0
    assert report["dry_run_sent_count"] == 0


def test_notification_artifacts_assert_offline_banners_and_no_send(tmp_path):
    report = build_notification_dry_run(
        sample_alert_events(), input_source="deterministic_fixture",
        generated_at="2026-06-22T20:00:00-04:00",
    )
    write_notification_dry_run(report, output_root=tmp_path, run_id="fixture_banner_run")
    latest = tmp_path / "latest"
    markdown = (latest / "notification_dry_run.md").read_text(encoding="utf-8")
    assert "NOTHING WAS SENT OR SPOKEN" in markdown
    with (latest / "notification_dry_run.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and all(row["dry_run_sent"] == "False" for row in rows)


def test_operator_artifacts_assert_offline_banners(tmp_path):
    matrix = build_profile_readiness_matrix(generated_at="2026-06-22T20:00:00-04:00")
    status = build_operator_status(matrix)
    write_operator_command_artifacts(matrix, status, output_root=tmp_path, run_id="banner_run")
    latest = tmp_path / "latest"
    matrix_md = (latest / "profile_readiness_matrix.md").read_text(encoding="utf-8")
    status_md = (latest / "operator_status.md").read_text(encoding="utf-8")
    assert "NO LIVE-RTH EVIDENCE" in matrix_md
    assert "NO LIVE-RTH CLAIMS" in status_md
