"""Phase 10A — read-only multi-day backtest SCAFFOLD: map the entry snapshot for
every date in a range and write one row per selected snapshot to a CSV.

This proves DATA MAPPING across many dates (structure + corridor + WDS + pricing
status). It does NOT run the TP/SL lifecycle yet. No broker calls, no live API, no
order/execution. Outputs go under repo-local `outputs/backtests/` only.

Usage:
    python -m scripts.backtest_scan_dates --symbol SPX --profile eod_5k_dynamic_sl150_no_tp \
        --start 2026-01-01 --end 2026-06-01 --dte 0 --entry 15:15 --limit 5
"""

from __future__ import annotations

import argparse
import csv

import src.app.cockpit_helpers as ch
from src.backtesting import mappers as M
from src.backtesting import raw_snapshot_loader as L
from src.backtesting import schemas
from src.config.strategy_profiles import load_profile_file

_COLUMNS = [
    "date", "symbol", "dte", "entry_target", "mapping_status", "skip_reason",
    "snapshot_ts", "offset_minutes", "spot",
    "call_floor_2k", "call_floor_5k", "call_floor_10k",
    "put_ceiling_2k", "put_ceiling_5k", "put_ceiling_10k",
    "corridor_valid", "cw1", "pw1", "corridor_reason",
    "raw_wds", "raw_dominant_side", "active_wds", "dominant_side", "dominant_tier",
    "gamma_primary", "gamma_secondary", "chain_priceable", "chain_quotes",
]


def _dte_bucket(dte: str) -> str:
    return schemas.DTE_1 if str(dte).strip().lower() in ("1", "1dte") else schemas.DTE_0


def _row_for_date(symbol: str, dte: str, date: str, entry: str, root) -> dict:
    row = {c: "" for c in _COLUMNS}
    row.update(date=date, symbol=symbol, dte=dte, entry_target=entry, mapping_status="ok")
    csv_path = L.file_for_date(symbol, dte, date, root=root)
    if csv_path is None:
        row.update(mapping_status="skipped", skip_reason="no file for date")
        return row
    try:
        rows = L.load_raw_rows(csv_path, symbol)
    except (OSError, ValueError) as exc:
        row.update(mapping_status="error", skip_reason=f"load_error:{type(exc).__name__}")
        return row
    sel = M.select_snapshot(L.available_timestamps(rows), entry)
    if not sel["ok"]:
        row.update(mapping_status="skipped", skip_reason=sel["reason"])
        return row
    ts = sel["timestamp"]
    snap = M.map_structure(rows, ts, symbol)
    chain = M.map_option_chain(rows, ts, symbol)
    ex = snap.exposures
    wd = M.corridor_wds(snap)
    gamma = ch.primary_secondary_gamma(ex, snap.spot)
    row.update(
        snapshot_ts=ts.isoformat(), offset_minutes=sel["offset_minutes"], spot=snap.spot,
        call_floor_2k=ex.call_floor_2k, call_floor_5k=ex.call_floor_5k, call_floor_10k=ex.call_floor_10k,
        put_ceiling_2k=ex.put_ceiling_2k, put_ceiling_5k=ex.put_ceiling_5k, put_ceiling_10k=ex.put_ceiling_10k,
        corridor_valid=wd["corridor_valid"], cw1=wd["corridor_cw1"], pw1=wd["corridor_pw1"],
        corridor_reason=wd["corridor_reason"],
        raw_wds=wd["raw_dominant_wds"], raw_dominant_side=wd["raw_dominant_side"],
        active_wds=wd["dominant_wing_wds"], dominant_side=wd["dominant_wing_side"],
        dominant_tier=wd["dominant_wing_tier"],
        gamma_primary=gamma["primary"], gamma_secondary=gamma["secondary"],
        chain_priceable=M.chain_pricing_usable(chain), chain_quotes=len(chain.quotes),
    )
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only multi-day backtest mapping scaffold (Phase 10A).")
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (inclusive).")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (inclusive).")
    ap.add_argument("--dte", default="0")
    ap.add_argument("--entry", default=None, help="Entry target (default: profile target_time or 11:00).")
    ap.add_argument("--limit", type=int, default=0, help="Max dates (0 = all in range).")
    ap.add_argument("--stamp", default="run", help="Run-dir stamp (default 'run'; CLIs may pass a timestamp).")
    ap.add_argument("--trading-root", default=None)
    args = ap.parse_args(argv)

    symbol = (args.symbol or "SPX").strip().upper()
    dte = _dte_bucket(args.dte)
    root = L.trading_root(args.trading_root)
    res = load_profile_file(args.profile)
    if not res.ok or res.profile is None:
        print(f"Profile '{args.profile}' not loadable: {res.errors}")
        return 1
    entry = args.entry or res.profile.target_time or "11:00"

    dates = L.available_dates(symbol, dte, root=root)
    if args.start:
        dates = [d for d in dates if d >= args.start]
    if args.end:
        dates = [d for d in dates if d <= args.end]
    if args.limit and args.limit > 0:
        dates = dates[:args.limit]

    print(f"ZerσSigma Algo — backtest scan ({symbol} {dte}, entry {entry}, profile "
          f"{res.profile.profile_id}) — read-only, no broker/lifecycle")
    if not dates:
        print(f"No {symbol} {dte} dates in range under {L.exposures_dir(symbol, dte, root=root)}.")
        return 0

    rows = [_row_for_date(symbol, dte, d, entry, root) for d in dates]
    label = f"scan_{symbol}_{dte}_{entry.replace(':', '')}"
    out_latest = M.latest_dir() / f"{label}.csv"
    out_run = M.run_dir(args.stamp, label) / f"{label}.csv"
    for path in (out_latest, out_run):
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=_COLUMNS)
            w.writeheader()
            w.writerows(rows)

    ok = sum(1 for r in rows if r["mapping_status"] == "ok")
    active = sum(1 for r in rows if r["corridor_valid"] is True)
    print(f"  dates={len(rows)}  mapped_ok={ok}  active_corridor={active}")
    print(f"  wrote {out_latest}")
    print(f"  wrote {out_run}")
    print("Mapping scaffold only — no TP/SL lifecycle, no broker, no execution.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
