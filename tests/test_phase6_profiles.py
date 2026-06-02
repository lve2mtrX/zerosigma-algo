"""Phase 6 — strategy run-profile schema, storage, hash, and CLI tests.

NO network, NO credentials, NO execution.
"""

from __future__ import annotations

import importlib

from src.config.strategy_profiles import (
    StrategyProfile,
    default_profiles_dir,
    list_profiles,
    load_profile_file,
    template_profile_dict,
    validate_profile_dict,
)

mp = importlib.import_module("scripts.manage_profiles")


def _valid_dict(**over):
    d = template_profile_dict("unit_test_profile")
    d["enabled"] = True
    d.update(over)
    return d


# ── schema / validation ─────────────────────────────────────────────────────

def test_valid_profile_loads():
    errs = validate_profile_dict(_valid_dict())
    assert errs == [], errs
    p = StrategyProfile.from_dict(_valid_dict())
    assert p.profile_id == "unit_test_profile"
    assert p.daily_selector == "score_best_valid"


def test_missing_required_field_fails_cleanly():
    d = _valid_dict()
    del d["strategy_id"]
    errs = validate_profile_dict(d)
    assert any("strategy_id" in e for e in errs)


def test_invalid_enum_fails_cleanly():
    for field, bad in (("quote_provider", "robinhood"),
                       ("structure_provider", "bloomberg"),
                       ("daily_selector", "yolo_mode")):
        errs = validate_profile_dict(_valid_dict(**{field: bad}))
        assert any(field in e for e in errs), f"{field}={bad} should be rejected: {errs}"


def test_wrong_type_fails_cleanly():
    errs = validate_profile_dict(_valid_dict(target_dte="soon", enabled="yes"))
    assert any("target_dte" in e for e in errs)
    assert any("enabled" in e for e in errs)


def test_profile_must_not_carry_secrets_or_execution():
    errs = validate_profile_dict(_valid_dict(execution_mode="live", refresh_token="x"))
    assert any("execution_mode" in e for e in errs)
    assert any("refresh_token" in e for e in errs)


# ── hash determinism + updated_at exclusion ─────────────────────────────────

def test_profile_hash_deterministic():
    a = StrategyProfile.from_dict(_valid_dict())
    b = StrategyProfile.from_dict(_valid_dict())
    assert a.profile_hash() == b.profile_hash()


def test_hash_excludes_timestamps_includes_config():
    base = StrategyProfile.from_dict(_valid_dict())
    # changing created_at / updated_at must NOT change the hash
    ts_changed = StrategyProfile.from_dict(_valid_dict(
        created_at="2030-01-01T00:00:00+00:00",
        updated_at="2030-01-01T00:00:00+00:00",
    ))
    assert base.profile_hash() == ts_changed.profile_hash()
    # changing a real config knob MUST change the hash
    cfg_changed = StrategyProfile.from_dict(_valid_dict(daily_selector="best_credit_valid"))
    assert base.profile_hash() != cfg_changed.profile_hash()


# ── checked-in example profiles ──────────────────────────────────────────────

def test_example_profiles_present_and_valid():
    results = list_profiles(default_profiles_dir())
    ids = {r.profile.profile_id for r in results if r.ok and r.profile}
    for expected in ("vertical_wing_score_best_1dte", "vertical_wing_call_only_1dte",
                     "vertical_wing_best_credit_1dte", "vertical_wing_no_trade"):
        assert expected in ids, f"missing example profile {expected}"
    assert all(r.ok for r in results), [r.errors for r in results if not r.ok]


def test_example_profiles_are_safe():
    """Examples: mock quotes, disabled, no secrets/execution keys."""
    for r in list_profiles(default_profiles_dir()):
        assert r.ok and r.profile
        assert r.profile.quote_provider in ("mock", "null", "tastytrade")
        assert r.profile.enabled is False
        assert "secret" not in (r.raw or {})
        assert "execution_mode" not in (r.raw or {})


# ── CLI (manage_profiles) — driven via main(argv) ───────────────────────────

def test_cli_list(capsys):
    rc = mp.main(["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vertical_wing_score_best_1dte" in out
    assert "score_best_valid" in out


def test_cli_validate_all(capsys):
    rc = mp.main(["--validate-all"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "profiles valid" in out


def test_cli_show_sanitized(capsys):
    rc = mp.main(["--show", "vertical_wing_score_best_1dte"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "profile_hash" in out
    # no secret-bearing KEYS in the rendered profile (profiles never hold secrets;
    # a doc hint like "TASTY_* env" inside notes is fine — we check for key forms).
    for forbidden in ("refresh_token:", "client_secret:", "password:", "client_id:"):
        assert forbidden not in out


def test_cli_validate_missing_profile_fails(capsys):
    rc = mp.main(["--validate", "does_not_exist_xyz"])
    assert rc == 1


def test_cli_create_template_then_refuses_overwrite(tmp_path, capsys):
    rc = mp.main(["--create-template", "tmpl_a", "--profiles-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "tmpl_a.yaml").is_file()
    # template is valid
    assert load_profile_file("tmpl_a", tmp_path).ok
    # second time without --force fails
    rc2 = mp.main(["--create-template", "tmpl_a", "--profiles-dir", str(tmp_path)])
    assert rc2 == 1
    # with --force succeeds
    rc3 = mp.main(["--create-template", "tmpl_a", "--force", "--profiles-dir", str(tmp_path)])
    assert rc3 == 0


def test_cli_copy_then_refuses_overwrite(tmp_path, capsys):
    # seed a source by copying an example into tmp first
    mp.main(["--create-template", "src_p", "--profiles-dir", str(tmp_path)])
    rc = mp.main(["--copy", "src_p", "dst_p", "--profiles-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "dst_p.yaml").is_file()
    # copied profile has the new id + is valid
    cp = load_profile_file("dst_p", tmp_path)
    assert cp.ok and cp.profile.profile_id == "dst_p"
    # refuses overwrite without --force
    rc2 = mp.main(["--copy", "src_p", "dst_p", "--profiles-dir", str(tmp_path)])
    assert rc2 == 1
    rc3 = mp.main(["--copy", "src_p", "dst_p", "--force", "--profiles-dir", str(tmp_path)])
    assert rc3 == 0
