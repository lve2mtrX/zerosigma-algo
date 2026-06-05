"""Phase 10A — local historical backtester data mapping (SPX / SPY / QQQ).

Discovery, raw loader (mixed timestamps, symbol spot column, RTH), snapshot
selection, structure + chain mapping, WDS/corridor recording, dry-run CLI on a
mocked file, outputs repo-local, 1DTE discovered-but-deferred, no hardcoded user,
no broker/live calls. Uses a synthetic trading root — no dependence on disk data.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import scripts.backtest_dry_run as dry
import scripts.backtest_scan_dates as scan
from src.backtesting import mappers as M
from src.backtesting import raw_snapshot_loader as L
from src.backtesting import schemas

_REPO = Path(__file__).resolve().parents[1]


def _repo_latest_fingerprint() -> list[tuple[str, int, int]]:
    latest = _REPO / "outputs" / "backtests" / "latest"
    if not latest.exists():
        return []
    return sorted(
        (str(p.relative_to(latest)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in latest.rglob("*")
        if p.is_file()
    )


def _csv(spot_col: str) -> str:
    # 11:00:00 snapshot forms a VALID corridor (CW1 7570 < spot 7585 < PW1 7600);
    # PUT 10K (WDS 0.60, T2) cleaner than CALL 10K (WDS ~0.47, T3) → PUT dominant.
    h = f"timestamp,session,{spot_col},Strike,CALL Volume,PUT Volume,CALL BID,CALL ASK,PUT BID,PUT ASK"
    rows = [
        h,
        "2026-06-02 11:00:00,RTH,7585,7560,8000,300,30.0,30.4,0.10,0.20",   # CALL W2
        "2026-06-02 11:00:00,RTH,7585,7570,15000,400,22.0,22.4,0.20,0.30",  # CALL floor 10K (W1)
        "2026-06-02 11:00:00,RTH,7585,7600,800,12000,1.0,1.2,9.0,9.4",      # PUT ceiling 10K (W1)
        "2026-06-02 11:00:00,RTH,7585,7610,700,4800,0.5,0.7,14.0,14.4",     # PUT W2
        "2026-06-02T11:05:15-04:00,RTH,7586,7585,500,500,5.0,5.4,5.0,5.4",  # ISO-offset ts (parse test)
        "2026-06-02 08:00:00,PRE,7600,7600,99999,99999,1,2,1,2",            # non-RTH → filtered
    ]
    return "\n".join(rows) + "\n"


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "Trading"
    layout = [
        ("SPX", "SPX", "SPX_RAW_2026-06-02.csv", "SPX_Spot"),
        ("SPY", "SPY", "SPY_RAW_2026-06-02.csv", "SPY_Spot"),
        ("QQQ", "QQQ", "QQQ_RAW_2026-06-02.csv", "QQQ_Spot"),
        ("SPX", "SPX_1DTE", "SPX_RAW_1DTE_2026-06-02.csv", "SPX_Spot"),
    ]
    for _sym, sub, fname, spotcol in layout:
        d = root / "TOS Data" / "Daily Exposures" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_text(_csv(spotcol), encoding="utf-8")
    return root


# ── path discovery (SPX/SPY/QQQ + missing) ───────────────────────────────────

def test_discovery_lists_all_symbols(tmp_path):
    root = _make_root(tmp_path)
    for sym in ("SPX", "SPY", "QQQ"):
        assert L.available_dates(sym, schemas.DTE_0, root=root) == ["2026-06-02"]
        assert L.file_for_date(sym, schemas.DTE_0, "2026-06-02", root=root) is not None
    # 1DTE present for SPX, discovered separately
    assert L.available_dates("SPX", schemas.DTE_1, root=root) == ["2026-06-02"]
    # 0DTE glob must NOT pick up the 1DTE file
    spx0 = L.list_raw_files("SPX", schemas.DTE_0, root=root)
    assert all("1DTE" not in p.name.upper() for p in spx0)


def test_discovery_missing_folder_graceful(tmp_path):
    root = _make_root(tmp_path)
    assert L.available_dates("RUT", schemas.DTE_0, root=root) == []   # no folder
    assert L.file_for_date("SPY", schemas.DTE_1, "2026-06-02", root=root) is None


def test_no_hardcoded_windows_username():
    files = [
        Path("src/backtesting/raw_snapshot_loader.py"),
        Path("src/backtesting/mappers.py"),
        Path("src/backtesting/schemas.py"),
        Path("scripts/backtest_dry_run.py"),
        Path("scripts/backtest_scan_dates.py"),
        Path("scripts/discover_backtest_sources.py"),
    ]
    repo = Path(__file__).resolve().parents[1]
    for f in files:
        low = (repo / f).read_text(encoding="utf-8").lower()
        assert r"c:\users" not in low and "c:/users/" not in low
        assert "danca" not in low


# ── loader: mixed timestamps, symbol spot column, RTH ────────────────────────

def test_parse_mixed_timestamps():
    naive = L.parse_timestamp("2026-06-02 11:00:00")
    iso = L.parse_timestamp("2026-06-02T11:05:15-04:00")
    compact = L.parse_timestamp("20260602 110000")
    assert naive == datetime(2026, 6, 2, 11, 0, 0)
    assert iso == datetime(2026, 6, 2, 11, 5, 15)        # tz dropped to ET wall time
    assert compact == datetime(2026, 6, 2, 11, 0, 0)
    assert L.parse_timestamp("") is None


def test_symbol_spot_column_detection(tmp_path):
    root = _make_root(tmp_path)
    for sym in ("SPX", "SPY", "QQQ"):
        f = L.file_for_date(sym, schemas.DTE_0, "2026-06-02", root=root)
        rows = L.load_raw_rows(f, sym)
        series = M.exposure_series_at(rows, datetime(2026, 6, 2, 11, 0, 0), sym)
        assert series["spot"] == 7585.0          # symbol-specific <SYM>_Spot resolved
        assert schemas.symbol_config(sym).spot_col == f"{sym}_Spot"


def test_rth_filter_drops_non_rth(tmp_path):
    root = _make_root(tmp_path)
    f = L.file_for_date("SPX", schemas.DTE_0, "2026-06-02", root=root)
    assert all(r["session"] == "RTH" for r in L.load_raw_rows(f, "SPX"))
    assert len(L.load_raw_rows(f, "SPX", rth_only=False)) == len(L.load_raw_rows(f, "SPX")) + 1


# ── snapshot selection (closest in window, tie at-or-after) ──────────────────

def test_select_snapshot_closest_and_ties():
    base = datetime(2026, 6, 2)

    def at(h, m, s=0):
        return base.replace(hour=h, minute=m, second=s)
    # closest to 11:00 wins (11:00:30 over 11:04:00)
    r = M.select_snapshot([at(10, 56), at(11, 0, 30), at(11, 4)], "11:00")
    assert r["ok"] and r["timestamp"] == at(11, 0, 30)
    # pre-target allowed inside window
    r2 = M.select_snapshot([at(10, 56)], "11:00")
    assert r2["ok"] and r2["timestamp"] == at(10, 56)
    # tie → prefer at-or-after (10:59 vs 11:01, both 1 min) → 11:01
    r3 = M.select_snapshot([at(10, 59), at(11, 1)], "11:00")
    assert r3["ok"] and r3["timestamp"] == at(11, 1)
    # none in window → unavailable reason
    r4 = M.select_snapshot([at(9, 31)], "11:00")
    assert r4["ok"] is False and "no snapshot within" in r4["reason"]
    assert M.select_snapshot([], "11:00")["ok"] is False


# ── structure mapping: wing thresholds + WDS via Phase 9J helper ─────────────

def test_structure_mapping_wings_and_wds(tmp_path):
    root = _make_root(tmp_path)
    f = L.file_for_date("SPX", schemas.DTE_0, "2026-06-02", root=root)
    rows = L.load_raw_rows(f, "SPX")
    snap = M.map_structure(rows, datetime(2026, 6, 2, 11, 0, 0), "SPX")
    ex = snap.exposures
    # put ceiling = highest strike where PUT vol >= threshold; call floor = lowest
    # strike where CALL vol >= threshold.
    assert ex.call_floor_10k == 7570.0 and ex.put_ceiling_10k == 7600.0
    assert ex.call_floor_2k == 7560.0 and ex.put_ceiling_2k == 7610.0
    # WDS via the Phase 9J helper + corridor (CW1 7570 < spot 7585 < PW1 7600)
    wd = M.corridor_wds(snap)
    assert wd["corridor_valid"] is True and wd["wds_active"] is True
    assert wd["dominant_wing_side"] == "PUT" and wd["dominant_wing_tier"] == 2


def test_chain_mapping_priceable(tmp_path):
    root = _make_root(tmp_path)
    f = L.file_for_date("SPX", schemas.DTE_0, "2026-06-02", root=root)
    rows = L.load_raw_rows(f, "SPX")
    chain = M.map_option_chain(rows, datetime(2026, 6, 2, 11, 0, 0), "SPX")
    assert M.chain_pricing_usable(chain) is True
    # mid-to-mid vertical credit prices from the chain
    cc = M.vertical_credit(chain, 7610.0, 7560.0, "CALL_CREDIT")   # short 7610 call / long 7560 call
    assert cc["priceable"] is True and cc["credit"] is not None


# ── dry-run CLI on the mocked file (no broker/live calls) ────────────────────

def test_dry_run_cli_on_mocked_file(tmp_path, capsys):
    root = _make_root(tmp_path)
    rc = dry.main(["--symbol", "SPX", "--profile", "morning_5k_dynamic_tp75",
                   "--date", "2026-06-02", "--dte", "0", "--entry", "11:00",
                   "--trading-root", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "spot=7585" in out
    assert "ACTIVE" in out and "corridor" in out.lower()
    assert "no broker" in out.lower()


def test_scan_dates_cli_outputs_temp_not_repo_latest(tmp_path, capsys, monkeypatch):
    # Tests must not refresh app-visible outputs/backtests/latest. The mapper
    # honors OUTPUT_DIR, so isolate this scaffold output under tmp_path.
    before = _repo_latest_fingerprint()
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "isolated_outputs"))
    monkeypatch.delenv("DATA_DIR", raising=False)
    root = _make_root(tmp_path)
    rc = scan.main(["--symbol", "SPX", "--profile", "eod_5k_dynamic_sl150_no_tp",
                    "--start", "2026-06-01", "--end", "2026-06-03", "--dte", "0",
                    "--entry", "11:00", "--stamp", "pytest", "--trading-root", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    # output path is isolated under tmp_path, never the raw data folder or app latest
    assert "outputs" in out and "backtests" in out
    assert str(root) not in out and "TOS Data" not in out   # never the raw data root
    assert _repo_latest_fingerprint() == before
    written = M.latest_dir() / "scan_SPX_0DTE_1100.csv"
    assert written.is_file()
    text = written.read_text(encoding="utf-8")
    assert "corridor_valid" in text.splitlines()[0]   # records corridor status


# ── 1DTE discovered but deferred (future) ────────────────────────────────────

def test_1dte_discovered_marked_future(tmp_path):
    root = _make_root(tmp_path)
    # 1DTE data is discoverable …
    assert L.available_dates("SPX", schemas.DTE_1, root=root) == ["2026-06-02"]
    # … and the discovery CLI labels it discovery-only / future.
    src = Path(__file__).resolve().parents[1] / "scripts" / "discover_backtest_sources.py"
    text = src.read_text(encoding="utf-8")
    assert "DISCOVERY-ONLY" in text and "future implementation" in text


# ── no execution surface in the backtesting code ─────────────────────────────

_FORBIDDEN_EXEC = (
    "submit_order", "place_order", "preview_order", "create_order",
    "order_preview", "execute_trade", "broker.",
)


def test_no_execution_tokens():
    repo = Path(__file__).resolve().parents[1]
    for rel in ("src/backtesting/raw_snapshot_loader.py", "src/backtesting/mappers.py",
                "src/backtesting/schemas.py", "src/backtesting/__init__.py",
                "scripts/backtest_dry_run.py", "scripts/backtest_scan_dates.py"):
        text = (repo / rel).read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_EXEC:
            assert tok not in text, f"{rel} contains {tok!r}"
