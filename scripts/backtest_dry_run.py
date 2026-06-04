"""Phase 10A — read-only backtest DRY-RUN: map one entry snapshot for one symbol.

Finds the raw daily CSV for a symbol/date/DTE, selects the entry snapshot inside
the entry window, maps the SAME StructureSnapshot + OptionChainSnapshot the live
path uses, and prints the structure read (spot, 2K/5K/10K wings, corridor + WDS,
primary/secondary gamma) plus the candidate vertical spreads and whether the chain
is priceable.

NOT the full backtester — just proves the data mapping. No broker calls, no live
API calls, no order/execution. Paths are env/home-derived (no hardcoded username).

Usage:
    python -m scripts.backtest_dry_run --symbol SPX --profile morning_5k_dynamic_tp75 --date 2026-06-02 --dte 0 --entry 11:00
    python -m scripts.backtest_dry_run --symbol SPY --profile eod_5k_dynamic_sl150_no_tp --latest --entry 15:15
"""

from __future__ import annotations

import argparse

import src.app.cockpit_helpers as ch
from src.backtesting import mappers as M
from src.backtesting import raw_snapshot_loader as L
from src.backtesting import schemas
from src.config.strategy_profiles import load_profile_file


def _dte_bucket(dte: str) -> str:
    return schemas.DTE_1 if str(dte).strip().lower() in ("1", "1dte") else schemas.DTE_0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only backtest dry-run (Phase 10A).")
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: latest available).")
    ap.add_argument("--latest", action="store_true", help="Use the latest available date.")
    ap.add_argument("--dte", default="0", help="0 or 1 (DTE bucket).")
    ap.add_argument("--entry", default=None, help="Entry target, e.g. 11:00 or 15:15.")
    ap.add_argument("--trading-root", default=None, help="Override trading root (env/home otherwise).")
    args = ap.parse_args(argv)

    symbol = (args.symbol or "SPX").strip().upper()
    dte = _dte_bucket(args.dte)
    root = L.trading_root(args.trading_root)

    res = load_profile_file(args.profile)
    if not res.ok or res.profile is None:
        print(f"Profile '{args.profile}' not loadable: {res.errors}")
        return 1
    prof = res.profile
    entry = args.entry or prof.target_time or "11:00"
    spread_width = prof.spread_width or 5.0

    print("ZerσSigma Algo — backtest dry-run (read-only, no broker/live API)")
    print(f"symbol={symbol}  profile={prof.profile_id}  selector={prof.daily_selector}  "
          f"side(call={prof.allow_call_credit},put={prof.allow_put_credit})  dte={dte}")

    dates = L.available_dates(symbol, dte, root=root)
    if not dates:
        print(f"No {symbol} {dte} raw files under {L.exposures_dir(symbol, dte, root=root)}.")
        print("Run `python -m scripts.discover_backtest_sources --symbols SPX SPY QQQ`.")
        return 0
    date = dates[-1] if (args.latest or not args.date) else args.date
    csv_path = L.file_for_date(symbol, dte, date, root=root)
    if csv_path is None:
        print(f"No {symbol} {dte} file for {date}. Available (last 5): {dates[-5:]}")
        return 0

    rows = L.load_raw_rows(csv_path, symbol)
    tss = L.available_timestamps(rows)
    sel = M.select_snapshot(tss, entry)
    print(f"date={date}  file={csv_path.name}  timestamps={len(tss)}")
    print(f"entry target={entry}  window={sel['window']}")
    if not sel["ok"]:
        print(f"  entry snapshot UNAVAILABLE: {sel['reason']}")
        return 0
    ts = sel["timestamp"]
    print(f"  selected snapshot @ {ts.time()}  (offset {sel['offset_minutes']:+} min)")

    snap = M.map_structure(rows, ts, symbol)
    chain = M.map_option_chain(rows, ts, symbol)
    ex = snap.exposures
    wd = M.corridor_wds(snap)
    gamma = ch.primary_secondary_gamma(ex, snap.spot)

    print(f"\nspot={snap.spot}")
    print(f"  call floors 2K/5K/10K : {ex.call_floor_2k} / {ex.call_floor_5k} / {ex.call_floor_10k}")
    print(f"  put ceilings 2K/5K/10K: {ex.put_ceiling_2k} / {ex.put_ceiling_5k} / {ex.put_ceiling_10k}")
    print(f"  corridor: CW1={wd['corridor_cw1']} < spot={wd['corridor_spot']} < PW1={wd['corridor_pw1']}  "
          f"→ {'ACTIVE' if wd['corridor_valid'] else 'INACTIVE'}")
    if wd["wds_active"]:
        print(f"  WDS (active): dominant {wd['dominant_wing_label']} {wd['dominant_wing_strike']} "
              f"WDS {wd['dominant_wing_wds_pct']} Tier {wd['dominant_wing_tier']}")
    elif wd["raw_wds_source"] == "true":
        print(f"  WDS (raw, inactive): {wd['raw_dominant_label']} {wd['raw_dominant_strike']} "
              f"WDS {wd['raw_dominant_wds_pct']} — NOT active structure")
    else:
        print(f"  WDS: unavailable — {wd['wds_reason']}")
    if gamma["available"]:
        print(f"  gamma: primary {gamma['primary_fmt']} secondary {gamma['secondary_fmt']} ({gamma['source']})")
    else:
        print("  gamma: unavailable")

    # Candidate vertical spreads (2K wings, mid-to-mid) — display only.
    print(f"\ncandidate spreads (spread_width={spread_width}, mid-to-mid, display only):")
    if prof.allow_call_credit and ex.put_ceiling_2k is not None:
        cc = M.vertical_credit(chain, ex.put_ceiling_2k, ex.put_ceiling_2k + spread_width, "CALL_CREDIT")
        print(f"  CALL_CREDIT short {cc['short_strike']} / long {cc['long_strike']}  credit="
              f"{cc['credit']}  priceable={cc['priceable']}")
    if prof.allow_put_credit and ex.call_floor_2k is not None:
        pc = M.vertical_credit(chain, ex.call_floor_2k, ex.call_floor_2k - spread_width, "PUT_CREDIT")
        print(f"  PUT_CREDIT  short {pc['short_strike']} / long {pc['long_strike']}  credit="
              f"{pc['credit']}  priceable={pc['priceable']}")
    print(f"  chain pricing usable: {M.chain_pricing_usable(chain)}  (quotes={len(chain.quotes)})")
    print("\nDry-run only — no scanner/selector/lifecycle run, no broker, no execution.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
