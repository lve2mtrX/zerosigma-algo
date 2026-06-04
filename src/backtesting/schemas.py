"""Phase 10A — symbol/data schemas + config for the local historical backtester.

Supports SPX / SPY / QQQ raw daily snapshot CSVs (the TOS logger format). Pure
config + small helpers; no I/O. Symbol-aware spot columns and (configurable) wing
volume thresholds live here so the loader/mappers stay generic.

TODO (future calibration): SPY/QQQ wing thresholds may need symbol-specific tuning
after initial discovery — SPX 10K contract volume is not structurally equivalent to
SPY/QQQ 10K. Defaults below start everyone at 2K/5K/10K; override per run.
"""

from __future__ import annotations

from dataclasses import dataclass

# Required STRUCTURE columns (per-strike) — needed to derive wings + WDS.
REQUIRED_STRUCTURE_COLS: tuple[str, ...] = ("timestamp", "Strike", "CALL Volume", "PUT Volume")
# Required PRICING columns — needed to price vertical credit spreads.
REQUIRED_PRICING_COLS: tuple[str, ...] = ("CALL BID", "CALL ASK", "PUT BID", "PUT ASK")
# Optional metric columns (used when present; never required).
OPTIONAL_METRIC_COLS: tuple[str, ...] = (
    "CALL Delta Adj GEX", "PUT Delta Adj GEX", "CALL OPEN_INT", "PUT OPEN_INT",
    "CALL IMPL_VOL", "PUT IMPL_VOL", "CALL Gamma", "PUT Gamma",
    "CALL Delta", "PUT Delta", "NET GEX", "NET DELTA-ADJ GEX",
)

# Default wing volume thresholds (Dan's 0DTE: 2K / 5K / 10K).
DEFAULT_THRESHOLDS: dict[str, float] = {"2k": 2000.0, "5k": 5000.0, "10k": 10000.0}

# Entry windows (from the reference vertical-wing backtest): target → (start, end)
# signed minute offsets. Closest snapshot inside the window wins; ties at-or-after.
ENTRY_WINDOWS: dict[str, tuple[int, int]] = {
    "11:00": (-5, +5),     # 10:55:00 – 11:05:00 (morning)
    "15:00": (-15, +15),   # 14:45:00 – 15:15:00
    "15:15": (-15, +15),   # 15:00:00 – 15:30:00 (EOD)
    "15:30": (-15, +15),   # 15:15:00 – 15:45:00
}
DEFAULT_ENTRY_BY_KIND: dict[str, str] = {"morning": "11:00", "eod": "15:15"}

RTH_START: tuple[int, int] = (9, 30)    # America/New_York
RTH_END: tuple[int, int] = (16, 0)

DTE_0 = "0DTE"
DTE_1 = "1DTE"
DTE_BUCKETS = (DTE_0, DTE_1)


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    spot_col: str                       # e.g. SPX_Spot / SPY_Spot / QQQ_Spot
    thresholds: dict[str, float]
    note: str = ""


_KNOWN: dict[str, SymbolConfig] = {
    "SPX": SymbolConfig("SPX", "SPX_Spot", dict(DEFAULT_THRESHOLDS)),
    "SPY": SymbolConfig(
        "SPY", "SPY_Spot", dict(DEFAULT_THRESHOLDS),
        "SPY wing thresholds are provisional (2K/5K/10K) — may need symbol-specific calibration."),
    "QQQ": SymbolConfig(
        "QQQ", "QQQ_Spot", dict(DEFAULT_THRESHOLDS),
        "QQQ wing thresholds are provisional (2K/5K/10K) — may need symbol-specific calibration."),
}

SUPPORTED_SYMBOLS: tuple[str, ...] = ("SPX", "SPY", "QQQ")


def symbol_config(symbol: str, *, thresholds_override: dict[str, float] | None = None) -> SymbolConfig:
    """Config for a symbol (spot column + wing thresholds). Unknown symbols get a
    `<SYM>_Spot` default + default thresholds with a note."""
    s = (symbol or "").strip().upper()
    cfg = _KNOWN.get(s) or SymbolConfig(
        s, f"{s}_Spot", dict(DEFAULT_THRESHOLDS),
        f"{s} is not a preconfigured symbol — assuming {s}_Spot + default thresholds.")
    if thresholds_override:
        cfg = SymbolConfig(cfg.symbol, cfg.spot_col, dict(thresholds_override), cfg.note)
    return cfg


def exposures_subdir(symbol: str, dte: str) -> str:
    """Folder name under 'Daily Exposures' for a symbol + DTE bucket."""
    s = (symbol or "").strip().upper()
    return f"{s}_1DTE" if dte == DTE_1 else s


def raw_glob(symbol: str, dte: str) -> str:
    """Glob for the raw daily CSVs of a symbol + DTE bucket."""
    s = (symbol or "").strip().upper()
    return f"{s}_RAW_1DTE_*.csv" if dte == DTE_1 else f"{s}_RAW_*.csv"
