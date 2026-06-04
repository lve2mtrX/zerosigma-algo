"""Phase 10 (prep) — read-only discovery of REAL backtest data sources.

Looks for the saved exposure snapshots + Wingonomics outputs a Phase 10 replay
engine would consume, and reports whether each looks usable + a schema hint.

Paths are derived from the user's HOME (never a hardcoded username) and can be
overridden by env or CLI:
    ZSA_TRADING_ROOT   base "Trading" folder   (default: ~/Dropbox/Trading)
    ZSA_BACKTEST_DIRS  extra dirs to scan, os.pathsep-separated (optional)
    --root PATH        override the trading root for this run (repeatable)

Read-only: never writes, never hits the network, never parses large Excel
workbooks (existence + size only), never places an order. Missing paths are
reported gracefully as NOT FOUND.

Usage:
    python -m scripts.discover_backtest_sources
    python -m scripts.discover_backtest_sources --root "D:/Trading"
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# Candidate sources RELATIVE to the trading root. (label, relpath, kind, note)
CANDIDATES: tuple[tuple[str, str, str, str], ...] = (
    ("SPX per-strike exposures (PRIMARY)", "TOS Data/Daily Exposures/SPX",
     "per_strike_csv", "SPX_RAW_*.csv — per-strike per-timestamp volumes/Greeks → exposure_series"),
    ("SPX 1DTE per-strike exposures", "TOS Data/Daily Exposures/SPX_1DTE",
     "per_strike_csv", "1DTE per-strike CSVs (same schema as SPX)"),
    ("Wingonomics outputs", "TOS Data/WINGONOMICS",
     "wingonomics_out", "wingonomics_daily_stats.csv / wingonomics_latest.json — validation reference"),
    ("Wingonomics script (reference, DO NOT MODIFY)",
     "TOS Data/0 - Strategies_Backtesting/wingonomics/scripts/wingonomics.py",
     "script", "wing-detection logic (volume-threshold floors/ceilings) — read-only reference"),
    ("Greek data master workbook", "Greek_Data_MASTER_CURRENT.xlsm",
     "excel", "binary workbook — reference only (not time-series replay)"),
    ("DeltaDrift daily snapshots", "TOS Data/DeltaDrift Daily Snapshots",
     "pdf", "PDF reports — visual only, not structured per-strike data"),
    ("Backtesting templates", "TOS Data/0 - Strategies_Backtesting/_templates",
     "templates", "strategy-folder templates — not data"),
)

# Columns that mark a per-strike exposure CSV as usable for the replay ETL.
_REQUIRED_RAW_COLS = ("Strike", "CALL Volume", "PUT Volume")


def trading_root(cli_root: str | None) -> Path:
    if cli_root:
        return Path(cli_root).expanduser()
    env = os.environ.get("ZSA_TRADING_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Dropbox" / "Trading"


def _peek_header(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.readline().strip()
    except OSError:
        return None


def _sample_dir(d: Path, limit: int = 8) -> tuple[int, list[str], set[str]]:
    """(file_count, sample_names, extensions) — bounded, read-only."""
    names: list[str] = []
    exts: set[str] = set()
    count = 0
    try:
        for p in sorted(d.iterdir()):
            if p.is_file():
                count += 1
                exts.add(p.suffix.lower())
                if len(names) < limit:
                    names.append(p.name)
    except OSError:
        pass
    return count, names, exts


def _report_one(root: Path, label: str, relpath: str, kind: str, note: str) -> dict:
    target = root / relpath
    exists = target.exists()
    out = {"label": label, "path": str(target), "exists": exists, "kind": kind,
           "note": note, "usable": "no", "detail": ""}
    if not exists:
        out["detail"] = "NOT FOUND"
        return out

    if kind == "per_strike_csv":
        count, names, _ = _sample_dir(target)
        raws = [n for n in names if n.lower().endswith(".csv")]
        header = None
        # peek the header of the first CSV in the directory (bounded)
        try:
            first_csv = next((p for p in sorted(target.iterdir())
                              if p.is_file() and p.suffix.lower() == ".csv"), None)
        except OSError:
            first_csv = None
        if first_csv is not None:
            header = _peek_header(first_csv)
        has_cols = bool(header) and all(c in header for c in _REQUIRED_RAW_COLS)
        out["usable"] = "yes" if has_cols else ("maybe" if first_csv else "no")
        out["detail"] = (f"{count} files; sample={raws[:3]}; "
                         f"required cols {'present' if has_cols else 'NOT all present'}")
    elif kind == "wingonomics_out":
        count, _, exts = _sample_dir(target)
        has_stats = bool(list(target.glob("*daily_stats*.csv")))
        has_latest = bool(list(target.glob("*latest*.json")))
        out["usable"] = "maybe"
        out["detail"] = (f"{count} files; exts={sorted(exts)}; "
                         f"daily_stats={'present' if has_stats else 'absent'}; "
                         f"latest_json={'present' if has_latest else 'absent'}")
    elif kind == "script":
        out["usable"] = "reference"
        out["detail"] = "reference script (read-only) — wing-detection logic to mirror in ETL"
    elif kind == "excel":
        try:
            kb = target.stat().st_size // 1024
        except OSError:
            kb = -1
        out["usable"] = "no"
        out["detail"] = f"binary workbook ~{kb} KB — reference only"
    elif kind == "pdf":
        count, _, _ = _sample_dir(target)
        out["usable"] = "no"
        out["detail"] = f"{count} PDF reports — visual only"
    elif kind == "templates":
        count, names, _ = _sample_dir(target)
        out["usable"] = "no"
        out["detail"] = f"{count} template files: {names[:4]}"
    return out


def _symbol_report(symbol: str, dte: str, root) -> dict:
    """Per-symbol/DTE report: folder, file count, date range, sample, required
    structure + pricing columns, usable-for-structure / usable-for-pricing."""
    from src.backtesting import raw_snapshot_loader as L
    from src.backtesting import schemas
    d = L.exposures_dir(symbol, dte, root=root)
    files = L.list_raw_files(symbol, dte, root=root)
    cfg = schemas.symbol_config(symbol)
    out = {"symbol": symbol, "dte": dte, "path": str(d), "files": len(files),
           "structure_ok": False, "pricing_ok": False}
    print(f"  {symbol} {dte}: {d}")
    if not d.is_dir():
        print("        NOT FOUND")
        return out
    if not files:
        print("        0 raw files (folder exists but empty)")
        return out
    dates = L.available_dates(symbol, dte, root=root)
    cols = L.header_columns(files[0])
    req_struct = [c for c in schemas.REQUIRED_STRUCTURE_COLS if c in cols]
    req_price = [c for c in schemas.REQUIRED_PRICING_COLS if c in cols]
    opt = [c for c in schemas.OPTIONAL_METRIC_COLS if c in cols]
    spot_ok = cfg.spot_col in cols
    struct_ok = spot_ok and len(req_struct) == len(schemas.REQUIRED_STRUCTURE_COLS)
    price_ok = len(req_price) == len(schemas.REQUIRED_PRICING_COLS)
    out.update(structure_ok=struct_ok, pricing_ok=price_ok)
    print(f"        files={len(files)}  dates={dates[0]} → {dates[-1]}  sample={files[0].name}")
    print(f"        spot column {cfg.spot_col}: {'present' if spot_ok else 'MISSING'}")
    print(f"        structure cols {len(req_struct)}/{len(schemas.REQUIRED_STRUCTURE_COLS)}  ·  "
          f"pricing cols {len(req_price)}/{len(schemas.REQUIRED_PRICING_COLS)}  ·  optional {len(opt)}")
    print(f"        usable for STRUCTURE: {'yes' if struct_ok else 'no'}  ·  "
          f"usable for PRICING: {'yes' if price_ok else 'no'}")
    if cfg.note:
        print(f"        note: {cfg.note}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only backtest-source discovery (Phase 10 prep).")
    ap.add_argument("--root", action="append", default=None,
                    help="Override the trading root (repeatable). Default: $ZSA_TRADING_ROOT "
                         "or ~/Dropbox/Trading.")
    ap.add_argument("--symbols", nargs="+", default=None,
                    help="Per-symbol report (e.g. --symbols SPX SPY QQQ).")
    ap.add_argument("--include-1dte", action="store_true",
                    help="Also report the 1DTE buckets (discovery only — future implementation).")
    args = ap.parse_args(argv)

    roots = [trading_root(r) for r in (args.root or [None])]
    extra = os.environ.get("ZSA_BACKTEST_DIRS")
    print("ZerσSigma Algo — backtest source discovery (read-only)")

    # ── Phase 10A — multi-symbol report ──
    if args.symbols:
        from src.backtesting import schemas
        buckets = [schemas.DTE_0] + ([schemas.DTE_1] if args.include_1dte else [])
        for root in roots:
            print(f"\nTrading root: {root}  ({'exists' if root.exists() else 'NOT FOUND'})")
            for sym in args.symbols:
                for dte in buckets:
                    _symbol_report(sym.strip().upper(), dte, root)
        if args.include_1dte:
            print("\n1DTE buckets are DISCOVERY-ONLY in Phase 10A — full 1DTE strategy logic is a "
                  "future implementation (see plan.md / docs/phase10_backtest_plan.md).")
        print("\nNote: paths are derived from your HOME / env — nothing is hardcoded, "
              "nothing was written, no network was used.")
        return 0
    for root in roots:
        print(f"\nTrading root: {root}  ({'exists' if root.exists() else 'NOT FOUND'})")
        usable_primary = False
        for label, relpath, kind, note in CANDIDATES:
            r = _report_one(root, label, relpath, kind, note)
            flag = {"yes": "✅", "maybe": "🟡", "reference": "📖",
                    "no": "⬜"}.get(r["usable"], "⬜")
            status = "EXISTS" if r["exists"] else "NOT FOUND"
            print(f"  {flag} [{status:9}] {label}")
            print(f"        {r['path']}")
            print(f"        {r['detail']}")
            if kind == "per_strike_csv" and r["usable"] == "yes":
                usable_primary = True
        if not usable_primary:
            print("\n  No usable per-strike exposure source found under this root.")
            print("  Phase 10 ETL needs SPX_RAW_*.csv (Strike + CALL/PUT Volume columns) to")
            print("  build the exposure_series block. See docs/phase10_backtest_plan.md.")

    if extra:
        print(f"\nExtra dirs from ZSA_BACKTEST_DIRS: {extra}")
        for d in extra.split(os.pathsep):
            p = Path(d).expanduser()
            print(f"  {'EXISTS' if p.exists() else 'NOT FOUND'}: {p}")

    print("\nNote: paths are derived from your HOME / env — nothing is hardcoded, "
          "nothing was written, no network was used.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
