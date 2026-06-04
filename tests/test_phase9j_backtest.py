"""Phase 10A (prep) — SPX_RAW loader maps to the SAME StructureSnapshot the live
provider produces (2K/5K/10K wings + W2/WDS inputs), via the shared mapper.

Uses a synthetic CSV (no dependence on Dan's filesystem). Read-only; no execution.
"""

from __future__ import annotations

from pathlib import Path

import src.app.cockpit_helpers as ch
from src.replay import spx_raw_loader as sl


def _write_csv(tmp_path: Path) -> Path:
    # columns: timestamp, session, SPX_Spot, Strike, CALL Volume, PUT Volume.
    # Spot 7575 sits INSIDE the corridor (CW1 7560 < 7575 < PW1 7600) so the
    # dominant wing is active (Phase 10A corridor rule).
    rows = [
        "timestamp,session,SPX_Spot,Strike,CALL Volume,PUT Volume",
        "2026-06-03 12:00:00,RTH,7575,7550,500,300",
        "2026-06-03 12:00:00,RTH,7575,7555,8264,400",     # CALL W2 (one below floor)
        "2026-06-03 12:00:00,RTH,7575,7560,15734,600",    # CALL floor 10K (W1)
        "2026-06-03 12:00:00,RTH,7575,7595,900,3000",
        "2026-06-03 12:00:00,RTH,7575,7600,800,12000",    # PUT ceiling 10K (W1)
        "2026-06-03 12:00:00,RTH,7575,7605,700,4800",     # PUT W2 (one above ceiling)
        "2026-06-03 09:25:00,RTH,7610,7600,100,100",      # earlier tick, no 10K
        "2026-06-03 08:00:00,EXT,7610,7600,99999,99999",  # non-RTH → filtered out
    ]
    p = tmp_path / "SPX_RAW_2026-06-03.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_read_rows_rth_filter(tmp_path):
    p = _write_csv(tmp_path)
    rows = sl.read_rows(p)                       # RTH only by default
    assert all(r["session"] == "RTH" for r in rows)
    assert len(sl.read_rows(p, rth_only=False)) == len(rows) + 1   # the EXT row


def test_available_timestamps(tmp_path):
    p = _write_csv(tmp_path)
    tss = sl.available_timestamps(p)
    assert tss == ["2026-06-03 12:00:00", "2026-06-03 09:25:00"]


def test_exposure_series_sorted_and_sided(tmp_path):
    p = _write_csv(tmp_path)
    rows = sl.read_rows(p)
    series = sl.exposure_series_at(rows, "2026-06-03 12:00:00")
    assert series["strikes"] == sorted(series["strikes"])      # ascending
    assert series["spot"] == 7575.0
    # side-specific volumes line up with strikes
    i = series["strikes"].index(7560.0)
    assert series["calls"][i] == 15734.0


def test_snapshot_at_derives_wings_and_w2(tmp_path):
    p = _write_csv(tmp_path)
    snap = sl.snapshot_at(p, "2026-06-03 12:00:00")
    assert snap.source == "spx_raw_replay" and snap.symbol == "SPX"
    ex = snap.exposures
    assert ex.call_floor_10k == 7560.0 and ex.call_floor_10k_volume == 15734.0
    assert ex.call_floor_10k_w2_strike == 7555.0 and ex.call_floor_10k_w2_volume == 8264.0
    assert ex.put_ceiling_10k == 7600.0 and ex.put_ceiling_10k_volume == 12000.0
    assert ex.put_ceiling_10k_w2_strike == 7605.0 and ex.put_ceiling_10k_w2_volume == 4800.0
    # WDS computes from the mapped W1/W2 — PUT cleaner (0.60 T2) than CALL (0.475 T3)
    wd = ch.wing_dominance(ex, snap.spot)
    assert wd["wds_source"] == "true" and wd["dominant_wing_side"] == "PUT"
    assert wd["dominant_wing_tier"] == 2


def test_snapshot_default_first_timestamp(tmp_path):
    p = _write_csv(tmp_path)
    snap = sl.snapshot_at(p)        # first ts = midday (file order)
    assert snap.exposures.call_floor_10k == 7560.0


def test_available_dates_and_file_for_date(tmp_path):
    _write_csv(tmp_path)
    assert sl.available_dates(tmp_path) == ["2026-06-03"]
    assert sl.file_for_date(tmp_path, "2026-06-03").name == "SPX_RAW_2026-06-03.csv"
    assert sl.available_dates(tmp_path / "missing") == []
    assert sl.file_for_date(tmp_path, "1999-01-01") is None
