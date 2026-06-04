"""Phase 9I — backtest-source discovery script: graceful on missing paths,
env/CLI/home-derived roots, and NO hardcoded user-specific path in the code.
"""

from __future__ import annotations

from pathlib import Path

import scripts.discover_backtest_sources as disc

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT_SRC = (_REPO / "scripts" / "discover_backtest_sources.py").read_text(encoding="utf-8")


def test_trading_root_resolution_order(monkeypatch, tmp_path):
    # CLI override wins
    assert disc.trading_root(str(tmp_path)) == tmp_path
    # env wins when no CLI
    monkeypatch.setenv("ZSA_TRADING_ROOT", str(tmp_path / "env"))
    assert disc.trading_root(None) == tmp_path / "env"
    # home-derived default when neither
    monkeypatch.delenv("ZSA_TRADING_ROOT", raising=False)
    assert disc.trading_root(None) == Path.home() / "Dropbox" / "Trading"


def test_no_hardcoded_username_in_code():
    low = _SCRIPT_SRC.lower()
    # the code must NOT hardcode a windows user path or a username
    assert r"c:\users" not in low and "c:/users/" not in low
    assert "danca" not in low
    # it must derive from HOME / env
    assert "Path.home()" in _SCRIPT_SRC
    assert "ZSA_TRADING_ROOT" in _SCRIPT_SRC


def test_runs_clean_on_missing_root(monkeypatch, tmp_path, capsys):
    # point at an empty dir → every candidate NOT FOUND, no crash, exit 0
    rc = disc.main(["--root", str(tmp_path / "does_not_exist")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NOT FOUND" in out
    assert "No usable per-strike exposure source" in out


def test_report_one_handles_missing(tmp_path):
    r = disc._report_one(tmp_path, "X", "nope/missing", "per_strike_csv", "n")
    assert r["exists"] is False and r["detail"] == "NOT FOUND" and r["usable"] == "no"


def test_report_one_detects_usable_csv(tmp_path):
    d = tmp_path / "TOS Data" / "Daily Exposures" / "SPX"
    d.mkdir(parents=True)
    (d / "SPX_RAW_2026-06-03.csv").write_text(
        "timestamp,date,time,session,SPX_Spot,CALL Volume,Strike,PUT Volume\n"
        "2026-06-03 10:00,2026-06-03,10:00,RTH,5800,1200,5800,3400\n",
        encoding="utf-8")
    r = disc._report_one(tmp_path, "SPX", "TOS Data/Daily Exposures/SPX",
                         "per_strike_csv", "n")
    assert r["exists"] is True and r["usable"] == "yes"
    assert "required cols present" in r["detail"]


def test_main_smoke_default_root_no_crash(capsys):
    # Whatever the real machine has (or not), the script must not crash.
    rc = disc.main([])
    assert rc == 0
    assert "backtest source discovery" in capsys.readouterr().out
