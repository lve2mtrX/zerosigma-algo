"""Phase 9C — cockpit UI helpers, profile builder, and safe control helpers.

NO network, NO credentials, NO broker execution. The control helpers are tested
by monkeypatching ``control_ui.control`` so no real process is ever spawned. The
Streamlit shell is checked for clean import + syntax (ast.parse)."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from src.app import control_ui
from src.app import profile_builder as pb
from src.app import ui_helpers as ui

REPO = Path(__file__).resolve().parents[1]


# ── Streamlit shell imports cleanly ──────────────────────────────────────────

def test_streamlit_module_imports_cleanly():
    m = importlib.import_module("src.app.streamlit_main")
    assert m is not None
    # also a fresh syntax check (import may be cached)
    src = (REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")
    ast.parse(src)


# ── ui_helpers (pure) ────────────────────────────────────────────────────────

def test_brand_css_has_palette_and_style():
    css = ui.brand_css()
    assert "<style" in css and "</style>" in css
    assert ui.BRAND["accent"] in css            # electric green present
    assert "#0b0f14" in css                       # dark bg present
    assert ".stTabs" in css and "[data-testid=\"stMetric\"]" in css


def test_pill_and_metric_card_and_format():
    p = ui.pill("LIVE", "green")
    assert "zsa-pill" in p and "green" in p and "LIVE" in p
    card = ui.metric_card("Open", 3, "sub")
    assert "zsa-metric" in card and ">3<" in card and "sub" in card
    assert ui.fmt_money(1234.5) == "$1,234.50"
    assert ui.fmt_num(None) == "—" and ui.dash(None) == "—"
    assert ui.pnl_kind(5) == "green" and ui.pnl_kind(-2) == "red" and ui.pnl_kind(None) == "ghost"
    # pill escapes injection
    assert "<script>" not in ui.pill("<script>", "red")


def test_brand_title_highlights_sigma():
    out = ui.brand_title("ZerσSigma Algo Cockpit")
    assert 'class="sig"' in out and "σ" in out


# ── profile_builder (pure CRUD over Phase 6) ─────────────────────────────────

def test_template_builds_valid_profile():
    d = pb.new_template_dict("ui_demo")
    assert pb.validate_dict(d) == []
    assert pb.hash_for(d) and len(pb.hash_for(d)) == 16


def test_build_profile_dict_applies_and_coerces():
    base = pb.new_template_dict("ui_demo")
    built = pb.build_profile_dict(
        {"target_dte": 1, "enabled": True, "min_credit": "0.50", "wing_threshold": ""},
        base=base, now_iso="2026-01-01T00:00:00",
    )
    assert built["target_dte"] == 1
    assert built["enabled"] is True
    assert built["min_credit"] == 0.5
    assert built["wing_threshold"] is None        # blank optfloat → None
    assert built["updated_at"] == "2026-01-01T00:00:00"
    assert pb.validate_dict(built) == []


def test_validate_rejects_execution_and_secret_keys():
    d = pb.new_template_dict("bad")
    d["execution_mode"] = "live"
    d["tasty_refresh_token"] = "secret"
    errs = pb.validate_dict(d)
    assert any("execution_mode" in e for e in errs)
    assert any("tasty_refresh_token" in e for e in errs)


def test_save_refuses_overwrite_unless_explicit(tmp_path):
    d = pb.new_template_dict("ovr")
    ok, _msg, h = pb.save_profile(d, overwrite=False, profiles_dir=tmp_path)
    assert ok is True and h and len(h) == 16
    # second save without overwrite → refused
    ok2, msg2, _ = pb.save_profile(d, overwrite=False, profiles_dir=tmp_path)
    assert ok2 is False and "already exists" in msg2
    # explicit overwrite → allowed
    ok3, _msg3, _h3 = pb.save_profile(d, overwrite=True, profiles_dir=tmp_path)
    assert ok3 is True


def test_save_writes_yaml_and_list_summaries_sees_it(tmp_path):
    d = pb.new_template_dict("seen_profile")
    ok, _msg, _h = pb.save_profile(d, profiles_dir=tmp_path)
    assert ok is True
    assert (tmp_path / "seen_profile.yaml").is_file()
    rows = pb.list_summaries(tmp_path)
    ids = {r["profile_id"]: r for r in rows}
    assert "seen_profile" in ids and ids["seen_profile"]["ok"] is True


def test_save_rejects_invalid_profile(tmp_path):
    d = pb.new_template_dict("badexec")
    d["execution_mode"] = "live"
    ok, msg, h = pb.save_profile(d, profiles_dir=tmp_path)
    assert ok is False and h is None and "validation failed" in msg
    assert not (tmp_path / "badexec.yaml").exists()


def test_clone_dict_round_trips(tmp_path):
    src = pb.new_template_dict("src_prof")
    pb.save_profile(src, profiles_dir=tmp_path)
    cloned, errs = pb.clone_dict(str(tmp_path / "src_prof.yaml"), "cloned_prof")
    assert errs == [] and cloned is not None
    assert cloned["profile_id"] == "cloned_prof"
    assert pb.validate_dict(cloned) == []


# ── control_ui (safe local runner control; control module mocked) ────────────

def test_status_view_states():
    stopped = control_ui.status_view({"status": "stopped", "active": False})
    assert stopped["active"] is False and stopped["badge"] == "ghost" and stopped["stale"] is False
    running = control_ui.status_view({"status": "running", "active": True, "pid": 5})
    assert running["active"] is True and running["badge"] == "green" and running["pid"] == 5
    stale = control_ui.status_view({"status": "stale", "active": False, "pid_alive": False})
    assert stale["stale"] is True and stale["badge"] == "red"


def test_can_start_guard():
    assert control_ui.can_start({"status": "stopped", "active": False})[0] is True
    assert control_ui.can_start({"status": "running", "active": True, "pid": 9})[0] is False
    assert control_ui.can_start({"status": "stale", "active": False})[0] is False
    assert control_ui.can_start({"active": False, "pid_alive": True, "pid": 3})[0] is False


def test_start_runner_refuses_second_live_runner(monkeypatch):
    monkeypatch.setattr(control_ui.control, "status",
                        lambda root=None: {"active": True, "pid": 123, "run_id": "r1", "status": "running"})
    called = {"start": False}
    monkeypatch.setattr(control_ui.control, "start",
                        lambda *a, **k: called.__setitem__("start", True) or (True, "x", 1))
    ok, msg, pid = control_ui.start_runner("vertical_wing_no_trade", once=True)
    assert ok is False and pid is None
    assert "already active" in msg
    assert called["start"] is False        # never launched a second runner


def test_start_runner_happy_path_calls_control_start(monkeypatch):
    monkeypatch.setattr(control_ui.control, "status",
                        lambda root=None: {"active": False, "status": "stopped"})
    seen = {}
    def _fake_start(profile, **kw):
        seen["profile"] = profile
        seen["kw"] = kw
        return True, "started runner pid 4242", 4242
    monkeypatch.setattr(control_ui.control, "start", _fake_start)
    ok, _msg, pid = control_ui.start_runner("vertical_wing_no_trade", once=True, market_hours_only=True)
    assert ok is True and pid == 4242
    assert seen["profile"] == "vertical_wing_no_trade"
    assert seen["kw"]["once"] is True and seen["kw"]["market_hours_only"] is True


def test_start_runner_requires_profile():
    ok, msg, pid = control_ui.start_runner("")
    assert ok is False and pid is None and "profile" in msg


def test_stop_runner_writes_graceful_stop(monkeypatch):
    seen = {}
    monkeypatch.setattr(control_ui.control, "stop",
                        lambda root=None, *, force=False: seen.update(force=force) or (True, "stop: requested"))
    ok, _msg = control_ui.stop_runner(force=False)
    assert ok is True and seen["force"] is False        # graceful first
    control_ui.stop_runner(force=True)
    assert seen["force"] is True                          # force only when asked


def test_cleanup_wraps_control(monkeypatch):
    monkeypatch.setattr(control_ui.control, "cleanup_stale",
                        lambda root=None: (True, "cleaned"))
    assert control_ui.cleanup() == (True, "cleaned")


def test_safe_command_builds_without_launch(monkeypatch):
    monkeypatch.setattr(control_ui.control, "build_command",
                        lambda profile, **k: ["py", "-m", "scripts.run_forward", "--profile", profile])
    cmd = control_ui.safe_command("vertical_wing_no_trade", once=True)
    assert "run_forward" in cmd and "vertical_wing_no_trade" in cmd


# ── no execution / order / preview surface in the new UI modules ─────────────

def test_no_execution_surface_in_ui_modules():
    files = ("src/app/ui_helpers.py", "src/app/profile_builder.py",
             "src/app/control_ui.py", "src/app/streamlit_main.py")
    forbidden = ("submit_order", "place_order", "preview_order", "create_order",
                 "order_preview", "execute_trade", "broker.")
    for rel in files:
        src = (REPO / rel).read_text(encoding="utf-8")
        for tok in forbidden:
            assert tok not in src, f"{rel} must not reference {tok!r}"
