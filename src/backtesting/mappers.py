"""Phase 10A — map a selected raw snapshot into the SAME shapes the live/replay
path uses (StructureSnapshot + OptionChainSnapshot), plus snapshot selection and
output-dir helpers. Reuses the live structure mapper and the Phase 9J WDS helper —
no strategy/structure fork. Pure (except the small output-dir maker). No execution.
"""

from __future__ import annotations

import os
from datetime import datetime, time
from pathlib import Path
from typing import Any

import src.app.cockpit_helpers as ch
from src.backtesting import schemas
from src.providers.quotes.types import OptionChainSnapshot, OptionQuote, OptionType
from src.providers.structure.types import StructureSnapshot
from src.providers.structure.zerosigma_api import GREEK_API_UNITS
from src.replay.snapshot_loader import map_payload_to_snapshot

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _col(row: dict, *names: str) -> Any:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


# ── entry-window + snapshot selection ────────────────────────────────────────

def parse_target_time(target: str) -> time:
    """'11:00' / '15:15:00' → datetime.time."""
    parts = str(target).strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    s = int(parts[2]) if len(parts) > 2 else 0
    return time(h, m, s)


def entry_window(target: str) -> tuple[int, int]:
    """(start_offset_min, end_offset_min) for a target like '11:00' (default ±15)."""
    return schemas.ENTRY_WINDOWS.get(str(target).strip(), (-15, +15))


def _secs(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def select_snapshot(timestamps: list[datetime], target: str) -> dict[str, Any]:
    """Pick the snapshot CLOSEST to the target inside its entry window
    (smallest |distance|; ties prefer at-or-after). Returns
    {ok, timestamp, offset_minutes, window, reason}."""
    start_off, end_off = entry_window(target)
    tt = _secs(parse_target_time(target))
    window = f"[{start_off:+d},{end_off:+d}] min of {target}"
    if not timestamps:
        return {"ok": False, "timestamp": None, "offset_minutes": None,
                "window": window, "reason": "no snapshots loaded"}
    in_window: list[tuple[datetime, int]] = []
    for dt in timestamps:
        delta = _secs(dt.time()) - tt
        if start_off * 60 <= delta <= end_off * 60:
            in_window.append((dt, delta))
    if not in_window:
        return {"ok": False, "timestamp": None, "offset_minutes": None,
                "window": window, "reason": f"no snapshot within {window}"}
    # smallest |delta| wins; tie → at-or-after (before_flag=1 when delta<0)
    chosen, delta = min(in_window, key=lambda t: (abs(t[1]) * 2 + (1 if t[1] < 0 else 0)))
    return {"ok": True, "timestamp": chosen, "offset_minutes": round(delta / 60.0, 2),
            "window": window, "reason": ""}


# ── structure + option-chain mapping ─────────────────────────────────────────

def exposure_series_at(rows: list[dict], ts: datetime, symbol: str) -> dict[str, Any]:
    """{strikes, calls, puts, spot} for one timestamp (strikes ascending, side
    volumes), using the symbol-specific spot column."""
    cfg = schemas.symbol_config(symbol)
    triples: list[tuple[float, float, float]] = []
    spot = None
    for r in rows:
        if r.get("_ts") != ts:
            continue
        k = _num(_col(r, "Strike", "strike"))
        if k is None:
            continue
        triples.append((k, _num(_col(r, "CALL Volume")) or 0.0, _num(_col(r, "PUT Volume")) or 0.0))
        if spot is None:
            spot = _num(_col(r, cfg.spot_col, "spot", "Spot"))
    triples.sort(key=lambda t: t[0])
    return {"strikes": [t[0] for t in triples], "calls": [t[1] for t in triples],
            "puts": [t[2] for t in triples], "spot": spot}


_RAW_GREEK_PAIRS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "delta": (("CALL Delta", "c_delta"), ("PUT Delta", "p_delta")),
    "gamma": (("CALL Gamma", "c_gamma"), ("PUT Gamma", "p_gamma")),
    "theta": (("CALL Theta", "c_theta"), ("PUT Theta", "p_theta")),
    "vanna": (("CALL Vanna", "c_vanna"), ("PUT Vanna", "p_vanna")),
    "charm": (("CALL Charm", "c_charm"), ("PUT Charm", "p_charm")),
    "vomma": (("CALL Vomma", "c_vomma"), ("PUT Vomma", "p_vomma")),
    "speed": (("CALL Speed", "c_speed"), ("PUT Speed", "p_speed")),
    "zomma": (("CALL Zomma", "c_zomma"), ("PUT Zomma", "p_zomma")),
    "iv": (("CALL IMPL_VOL", "c_iv"), ("PUT IMPL_VOL", "p_iv")),
    "oi": (("CALL OPEN_INT", "c_oi"), ("PUT OPEN_INT", "p_oi")),
    "volume": (("CALL Volume", "c_volume"), ("PUT Volume", "p_volume")),
    "da_gex": (
        ("CALL Delta Adj GEX", "CALL DA GEX", "c_da_gex_1pct"),
        ("PUT Delta Adj GEX", "PUT DA GEX", "p_da_gex_1pct"),
    ),
    "raw_gex": (("CALL GEX", "c_raw_gex_1pct"), ("PUT GEX", "p_raw_gex_1pct")),
    "dex": (("CALL DEX", "c_dex_1pct"), ("PUT DEX", "p_dex_1pct")),
    "vex": (("CALL VEX", "c_vex_1vol"), ("PUT VEX", "p_vex_1vol")),
    "cex": (("CALL CEX", "c_cex"), ("PUT CEX", "p_cex")),
}


def greek_metrics_at(rows: list[dict], ts: datetime) -> dict[str, Any]:
    """Research-only Greek availability/aggregate read for one raw timestamp."""
    snapshot_rows = [row for row in rows if row.get("_ts") == ts]
    available: list[str] = []
    missing: list[str] = []
    totals: dict[str, float | None] = {}
    for metric, (call_names, put_names) in _RAW_GREEK_PAIRS.items():
        seen = any(
            any(
                name in row and row.get(name) not in (None, "")
                for name in (*call_names, *put_names)
            )
            for row in snapshot_rows
        )
        if not seen:
            missing.append(metric)
            totals[metric] = None
            continue
        available.append(metric)
        total = 0.0
        for row in snapshot_rows:
            total += _num(_col(row, *call_names)) or 0.0
            total += _num(_col(row, *put_names)) or 0.0
        totals[metric] = total

    for metric, net_names in (
        ("da_gex", ("NET DELTA-ADJ GEX", "NET DA GEX")),
        ("raw_gex", ("NET GEX",)),
    ):
        values = [_num(_col(row, *net_names)) for row in snapshot_rows]
        usable = [value for value in values if value is not None]
        if usable:
            totals[metric] = sum(usable)
            if metric not in available:
                available.append(metric)
                missing = [value for value in missing if value != metric]
    return {
        "totals": totals,
        "available_fields": tuple(available),
        "missing_fields": tuple(missing),
        "source_endpoint": "backtest_raw_csv",
        "units": {
            metric: GREEK_API_UNITS.get(metric, "raw source units") for metric in available
        },
    }


def da_gex_observations(
    rows: list[dict], timestamps: list[datetime]
) -> list[tuple[datetime, float | None]]:
    """Single-pass DA-GEX totals in chronological timestamp order."""
    allowed = set(timestamps)
    net_totals: dict[datetime, float] = {}
    side_totals: dict[datetime, float] = {}
    net_seen: set[datetime] = set()
    side_seen: set[datetime] = set()
    for row in rows:
        timestamp = row.get("_ts")
        if timestamp not in allowed:
            continue
        net = _num(_col(row, "NET DELTA-ADJ GEX", "NET DA GEX"))
        if net is not None:
            net_totals[timestamp] = net_totals.get(timestamp, 0.0) + net
            net_seen.add(timestamp)
            continue
        call_value = _num(_col(row, "CALL Delta Adj GEX", "CALL DA GEX", "c_da_gex_1pct"))
        put_value = _num(_col(row, "PUT Delta Adj GEX", "PUT DA GEX", "p_da_gex_1pct"))
        if call_value is not None or put_value is not None:
            side_totals[timestamp] = (
                side_totals.get(timestamp, 0.0) + (call_value or 0.0) + (put_value or 0.0)
            )
            side_seen.add(timestamp)
    return [
        (
            timestamp,
            net_totals[timestamp] if timestamp in net_seen
            else side_totals[timestamp] if timestamp in side_seen
            else None,
        )
        for timestamp in timestamps
    ]


def map_structure(rows: list[dict], ts: datetime, symbol: str) -> StructureSnapshot:
    """Map a snapshot timestamp → StructureSnapshot via the SHARED live mapper
    (2K/5K/10K wings + W2/WDS inputs derive identically to live). NOTE: the shared
    mapper uses the standard 2K/5K/10K volume thresholds; symbol-specific threshold
    calibration for SPY/QQQ is a documented future extension (see schemas + plan)."""
    cfg = schemas.symbol_config(symbol)
    series = exposure_series_at(rows, ts, symbol)
    snap_payload = {"spot": {"spot": series["spot"]}, "timestamp": ts.isoformat(), "exposures": {}}
    vol_series = {"strikes": series["strikes"], "calls": series["calls"], "puts": series["puts"]}
    return map_payload_to_snapshot(snap_payload, vol_series, symbol=cfg.symbol, source="backtest_raw")


def map_option_chain(rows: list[dict], ts: datetime, symbol: str,
                     *, expiry: str | None = None) -> OptionChainSnapshot:
    """Build an OptionChainSnapshot (one CALL + one PUT quote per strike) from the
    raw bid/ask — enough to price vertical credit spreads. No Tastytrade."""
    cfg = schemas.symbol_config(symbol)
    quotes: list[OptionQuote] = []
    spot = None
    exp = expiry or (ts.date().isoformat())
    for r in rows:
        if r.get("_ts") != ts:
            continue
        k = _num(_col(r, "Strike", "strike"))
        if k is None:
            continue
        if spot is None:
            spot = _num(_col(r, cfg.spot_col, "spot", "Spot"))
        cb, ca = _num(_col(r, "CALL BID")), _num(_col(r, "CALL ASK"))
        pb, pa = _num(_col(r, "PUT BID")), _num(_col(r, "PUT ASK"))
        cmid = (cb + ca) / 2 if cb is not None and ca is not None else None
        pmid = (pb + pa) / 2 if pb is not None and pa is not None else None
        quotes.append(OptionQuote(
            underlying=cfg.symbol, expiry=exp, option_type=OptionType.CALL, strike=k,
            bid=cb, ask=ca, mid=cmid, volume=_num(_col(r, "CALL Volume")),
            open_interest=_num(_col(r, "CALL OPEN_INT")), quote_time=ts))
        quotes.append(OptionQuote(
            underlying=cfg.symbol, expiry=exp, option_type=OptionType.PUT, strike=k,
            bid=pb, ask=pa, mid=pmid, volume=_num(_col(r, "PUT Volume")),
            open_interest=_num(_col(r, "PUT OPEN_INT")), quote_time=ts))
    quotes.sort(key=lambda q: (q.strike, str(q.option_type)))
    return OptionChainSnapshot(underlying=cfg.symbol, spot=spot or 0.0, expiry=exp,
                               quotes=quotes, quote_ts=ts, provider_name="backtest_raw")


def chain_pricing_usable(chain: OptionChainSnapshot) -> bool:
    """True when at least one strike has a usable call AND put mid (priceable)."""
    has_call = any(q.option_type == OptionType.CALL and q.mid is not None for q in chain.quotes)
    has_put = any(q.option_type == OptionType.PUT and q.mid is not None for q in chain.quotes)
    return has_call and has_put


def find_mid(chain: OptionChainSnapshot, strike: float, option_type: OptionType) -> float | None:
    for q in chain.quotes:
        if q.strike == strike and q.option_type == option_type:
            return q.mid
    return None


def vertical_credit(chain: OptionChainSnapshot, short_strike: float, long_strike: float,
                    side: str) -> dict[str, Any]:
    """Mid-to-mid credit (points) for a vertical credit spread. CALL_CREDIT = short
    call / long higher call; PUT_CREDIT = short put / long lower put. Display only —
    no order, no execution."""
    ot = OptionType.CALL if side == "CALL_CREDIT" else OptionType.PUT
    sm, lm = find_mid(chain, short_strike, ot), find_mid(chain, long_strike, ot)
    credit = round(sm - lm, 2) if (sm is not None and lm is not None) else None
    return {"side": side, "short_strike": short_strike, "long_strike": long_strike,
            "short_mid": sm, "long_mid": lm, "credit": credit,
            "priceable": credit is not None}


def corridor_wds(structure: StructureSnapshot) -> dict[str, Any]:
    """Phase 9J/10A Wing-Dominance + corridor read for a mapped structure."""
    return ch.wing_dominance(structure.exposures, structure.spot)


# ── output dirs (repo-local; never inside the raw data folders) ──────────────

def output_base() -> Path:
    env = os.environ.get("OUTPUT_DIR") or os.environ.get("DATA_DIR")
    base = Path(env).expanduser() if env else (_REPO_ROOT / "outputs")
    return base / "backtests"


def latest_dir() -> Path:
    d = output_base() / "latest"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_dir(stamp: str, label: str = "run") -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(label))[:48] or "run"
    d = output_base() / "runs" / f"{stamp}_{safe}"
    d.mkdir(parents=True, exist_ok=True)
    return d
