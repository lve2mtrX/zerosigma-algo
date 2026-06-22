"""Phase 9D — pure cockpit helpers (formatting, spot fallback, provider defaults,
log export, review prompt).

Stdlib + read-only review/ledger modules only. ZERO ``import streamlit`` so every
helper is unit-testable. NOTHING here executes, places, or previews an order — UI
formatting + read-only log export only. Provider "configured" detection checks env
var PRESENCE (never reads or returns secret values).
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# ── compact number formatting ────────────────────────────────────────────────

def fmt_exposure(v: Any) -> str:
    """Exposure in $B (already billions): 4.181966 → '4.18B', 0.735 → '735M',
    -1.2 → '-1.20B'. None → '—'."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    av = abs(x)
    if av >= 1.0:
        return f"{x:.2f}B"
    if av > 0.0:
        return f"{x * 1000:.0f}M"
    return "0B"


def fmt_strike(v: Any) -> str:
    """Strike/level price: 5815.0 → '5815', 5817.5 → '5817.50'. None → '—'."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if x == int(x):
        return f"{int(x)}"
    return f"{x:.2f}"


def fmt_price(v: Any) -> str:
    """Spot/price with thousands separator: 7609.78 → '7,609.78'. None → '—'."""
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def fmt_money(v: Any, decimals: int = 2) -> str:
    try:
        return f"${float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(v: Any, *, as_fraction: bool = True, decimals: int = 2) -> str:
    """7.31% formatting. ``as_fraction`` True → input is a fraction (0.0731)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if as_fraction:
        x *= 100.0
    return f"{x:.{decimals}f}%"


def fmt_count(v: Any) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—"


def gamma_regime_badge(regime: Any, da_gex_signed: Any = None) -> str:
    """Display regime; derive from DA-GEX sign when regime is missing."""
    r = regime if isinstance(regime, str) and regime else None
    if r is None and da_gex_signed is not None:
        try:
            g = float(da_gex_signed)
            r = "positive" if g > 0 else "negative" if g < 0 else None
        except (TypeError, ValueError):
            r = None
    if r == "positive":
        return "positive ↑"
    if r == "negative":
        return "negative ↓"
    return "—"


# ── spot fallback (prefer quote spot, fall back to structure spot) ───────────

def _usable(v: Any) -> float | None:
    """A spot is usable only if it is a positive finite number (0.0 = error)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0.0 else None


def spot_with_source(chain_spot: Any, structure_spot: Any,
                     quote_last: Any = None) -> tuple[float | None, str]:
    """Return (spot, source_badge). Prefer chain spot → structure spot → quote last.
    source_badge ∈ 'quote' | 'Zσ structure' | 'quote (last)' | '—'."""
    cs = _usable(chain_spot)
    if cs is not None:
        return cs, "quote"
    ss = _usable(structure_spot)
    if ss is not None:
        return ss, "Zσ structure"
    ql = _usable(quote_last)
    if ql is not None:
        return ql, "quote (last)"
    return None, "—"


# ── provider "configured" detection (env PRESENCE only — no secret values) ──

def _present(env: dict, *keys: str) -> bool:
    return all(bool(env.get(k)) for k in keys)


def tasty_configured(env: dict | None = None) -> bool:
    """True iff Tastytrade creds are present in env (OAuth or legacy). Checks
    presence only — never reads the secret values."""
    e = env if env is not None else dict(os.environ)
    oauth = _present(e, "TASTY_CLIENT_ID", "TASTY_CLIENT_SECRET", "TASTY_REFRESH_TOKEN")
    legacy = _present(e, "TASTY_USERNAME", "TASTY_PASSWORD")
    return oauth or legacy


def zs_configured(env: dict | None = None) -> bool:
    """True iff the ZS API base URL + a non-'none' auth mode are present."""
    e = env if env is not None else dict(os.environ)
    base = bool(e.get("ZS_API_BASE_URL"))
    mode = (e.get("ZS_API_AUTH_MODE") or "").strip().lower()
    return base and mode not in ("", "none")


def default_provider(options: list[str], *, preferred: str, sandbox: str,
                     configured: bool) -> str:
    """Pick the realistic default when configured, else the sandbox provider."""
    if configured and preferred in options:
        return preferred
    if sandbox in options:
        return sandbox
    return options[0] if options else preferred


def provider_index(options: list[str], choice: str) -> int:
    return options.index(choice) if choice in options else 0


def provider_label(name: str) -> str:
    """selectbox display: mark non-live providers as sandbox/testing."""
    sandbox = {"mock": "mock (sandbox)", "stub": "stub (sandbox)", "null": "null (manual marks)"}
    live = {"tastytrade": "tastytrade (live quotes)", "zerosigma_api": "zerosigma_api (live structure)"}
    return sandbox.get(name) or live.get(name) or name


# ── chain-unavailable guidance ───────────────────────────────────────────────

def chain_unavailable_actions(quote_name: str, *, last_error: str | None = None) -> list[str]:
    """Compact, copy-safe suggestions when the quote chain is unavailable.
    last_error is already sanitized by the provider (safe to show)."""
    le = str(last_error or "").lower()
    if "no_required_strikes" in le:
        return [
            "The quote request had no required strikes.",
            "Check whether the current structure payload has enough anchors for the strategy.",
            "Switch the Quote provider to `mock` (sandbox) for UI testing.",
        ]
    actions = [
        "Quotes may be unavailable because the market is closed or quotes are stale.",
        "Try again during RTH (09:30–16:00 ET).",
        "Switch the Quote provider to `mock` (sandbox) for UI testing.",
    ]
    if quote_name == "tastytrade":
        actions.append("Check Tasty auth in `.env` (TASTY_* — never shown here).")
    if last_error:
        actions.append(f"Provider note: {last_error}")
    return actions


# ── strict-DTE UX copy ───────────────────────────────────────────────────────

STRICT_DTE_LABEL = "Require exact DTE match"
STRICT_DTE_HELP = (
    "If enabled, a 1DTE profile will not fall back to 0DTE or the nearest expiry. "
    "If the exact DTE is unavailable, the scanner returns no trade. Most strategies "
    "should define their own target DTE."
)


def strict_dte_label() -> str:
    return STRICT_DTE_LABEL


def strict_dte_help() -> str:
    return STRICT_DTE_HELP


# ── operational status strip ─────────────────────────────────────────────────

def status_strip_cells(*, run_profile: Any, structure_name: Any, quote_name: Any,
                       runner_status: Any, selected_trade: Any, open_trades: Any,
                       realized_pnl: Any) -> list[tuple[str, str]]:
    """Pure: (label, value) pairs for the top operational status strip."""
    return [
        ("Run profile", str(run_profile) if run_profile else "—"),
        ("Structure", str(structure_name) if structure_name else "—"),
        ("Quote", str(quote_name) if quote_name else "—"),
        ("Runner", str(runner_status) if runner_status else "stopped"),
        ("Selected", str(selected_trade) if selected_trade else "—"),
        ("Open paper", str(open_trades if open_trades is not None else 0)),
        ("Realized P&L", fmt_money(realized_pnl) if realized_pnl is not None else "$0.00"),
    ]


# ── review prompt ────────────────────────────────────────────────────────────

def review_prompt(run_id: str | None = None) -> str:
    """A copy-paste prompt for reviewing a forward/portfolio run with an external
    assistant. Does NOT call any LLM — text only."""
    target = f" `{run_id}`" if run_id else ""
    return (
        f"Review this forward run{target} and identify issues. Look at the tick_log, "
        "signal_log, no_trade_log, portfolio_summary, paper_trade_events, and "
        "reconciliation_report. Specifically diagnose:\n"
        "1. Trade selection issues — why was a candidate selected or rejected by the "
        "daily selector? Any selector blockers?\n"
        "2. Quote problems — stale/invalid quotes, wide bid/ask, quote-validation "
        "failures, clock skew, missing chains.\n"
        "3. No-trade reasons — what drove each NO_TRADE (score below threshold, "
        "filters, strict DTE, market closed)?\n"
        "4. P&L lifecycle issues — TP/SL/EOD exits, unrealized vs realized P&L, "
        "duplicate-skipped / blocked-by-limits events, reconciliation problems.\n"
        "Summarize findings and concrete next steps. This is local paper analysis "
        "only — no broker execution."
    )


# ── log export targets (read-only; graceful when missing) ────────────────────

def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _entry(label: str, path: Path) -> dict[str, Any]:
    exists = path.is_file()
    return {
        "label": label,
        "filename": path.name,
        "path": str(path),
        "exists": exists,
        "text": _read_text_or_none(path) if exists else None,
    }


def forward_export_files(root: Path | str | None = None) -> list[dict[str, Any]]:
    """Latest forward-run log files for download. Graceful when none exist."""
    from src.forward import review as fr
    run_dir = fr.resolve_run_dir("latest", root)
    if run_dir is None:
        return [{"label": lbl, "filename": fn, "path": None, "exists": False, "text": None}
                for lbl, fn in (("Forward tick log", "tick_log.jsonl"),
                                ("Forward signal log", "signal_log.jsonl"),
                                ("Forward no-trade log", "no_trade_log.jsonl"))]
    return [
        _entry("Forward tick log", run_dir / "tick_log.jsonl"),
        _entry("Forward signal log", run_dir / "signal_log.jsonl"),
        _entry("Forward no-trade log", run_dir / "no_trade_log.jsonl"),
    ]


def portfolio_export_files(root: Path | str | None = None) -> list[dict[str, Any]]:
    """Latest portfolio-run log files for download. Graceful when none exist."""
    from src.paper import ledger
    run_dir = ledger.resolve_portfolio_run_dir("latest", root)
    if run_dir is None:
        return [{"label": lbl, "filename": fn, "path": None, "exists": False, "text": None}
                for lbl, fn in (("Paper trade events", "paper_trade_events.jsonl"),
                                ("Portfolio summary", "portfolio_summary.json"),
                                ("Reconciliation report", "reconciliation_report.json"))]
    paths = ledger.portfolio_paths(run_dir)
    return [
        _entry("Paper trade events", paths["events"]),
        _entry("Portfolio summary", paths["summary"]),
        _entry("Reconciliation report", paths["reconciliation"]),
    ]


# ── Phase 9F — EOD export + strategy-stats aggregation (read-only flat files) ──

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_json_file(path: Path) -> dict[str, Any] | None:
    import json
    txt = _read_text_or_none(path)
    if not txt:
        return None
    try:
        d = json.loads(txt)
        return d if isinstance(d, dict) else None
    except ValueError:
        return None


def eod_export_file(output_root: Path | str | None = None) -> dict[str, Any]:
    """The latest EOD summary JSON for download (graceful when missing)."""
    base = Path(output_root) if output_root else (_REPO_ROOT / "outputs")
    return _entry("EOD summary", base / "latest" / "eod_summary.json")


def latest_run_stats(forward_root: Path | str | None = None,
                     portfolio_root: Path | str | None = None) -> dict[str, Any]:
    """Section A — latest forward run + latest portfolio P&L (existing flat files)."""
    from src.forward import review as fr
    from src.paper import ledger
    man = fr.load_latest_manifest(forward_root) or {}
    summ = fr.summarize_run("latest", forward_root) or {}
    psum = ledger.load_latest_summary(portfolio_root) or {}
    return {
        "run_id": summ.get("run_id") or man.get("run_id") or "—",
        "profile": summ.get("profile_name") or man.get("profile_name") or "—",
        "ticks": summ.get("tick_count", 0),
        "signals": summ.get("signal_count", 0),
        "no_trade": summ.get("no_trade_count", 0),
        "open_paper": psum.get("open_trade_count", 0),
        "realized_pnl": psum.get("realized_pnl", 0.0),
        "total_pnl": psum.get("total_pnl", 0.0),
        "has_data": bool(man or summ or psum),
    }


def historical_stats(forward_root: Path | str | None = None,
                     portfolio_root: Path | str | None = None) -> dict[str, Any]:
    """Section B — aggregates across all discovered runs (no database; flat files)."""
    from src.forward import review as fr
    from src.paper import ledger
    fwd = fr.list_run_summaries(root=forward_root) or []
    pruns = ledger.list_portfolio_run_summaries(root=portfolio_root) or []
    paper_trades = wins = losses = 0
    realized = unrealized = 0.0
    for prun in pruns:
        rid = prun.get("portfolio_run_id")
        if not rid:
            continue
        for c in ledger.load_closed_trades(rid, portfolio_root) or []:
            paper_trades += 1
            try:
                rp = float(c.get("realized_pnl") or 0.0)
            except (TypeError, ValueError):
                rp = 0.0
            if rp > 0:
                wins += 1
            elif rp < 0:
                losses += 1
        s = ledger.load_summary(rid, portfolio_root) or {}
        realized += float(s.get("realized_pnl") or 0.0)
        unrealized += float(s.get("unrealized_pnl") or 0.0)
    return {
        "runs_found": len(fwd),
        "portfolio_runs": len(pruns),
        "total_ticks": sum(int(r.get("tick_count") or 0) for r in fwd),
        "total_signals": sum(int(r.get("signal_count") or 0) for r in fwd),
        "total_no_trade": sum(int(r.get("no_trade_count") or 0) for r in fwd),
        "paper_trades": paper_trades,
        "wins": wins,
        "losses": losses,
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "has_data": bool(fwd or pruns),
    }


def common_no_trade_reasons(forward_root: Path | str | None = None,
                            limit: int = 5) -> list[tuple[str, int]]:
    """Top no-trade reasons aggregated across forward runs (from no_trade_log)."""
    from src.forward import review as fr
    counts: dict[str, int] = {}
    for p in fr.discover_runs(forward_root):
        for row in fr.load_no_trade_log(p.name, forward_root) or []:
            reason = (row.get("no_trade_reason") or "").strip()
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def latest_best_candidate(output_root: Path | str | None = None) -> dict[str, Any] | None:
    """best_candidate_of_day from the latest EOD summary JSON, if present."""
    base = Path(output_root) if output_root else (_REPO_ROOT / "outputs")
    data = _read_json_file(base / "latest" / "eod_summary.json")
    if not data:
        return None
    bc = data.get("best_candidate_of_day")
    return bc if isinstance(bc, dict) else None


# ══════════════════════════════════════════════════════════════════════════════
# Phase 9H — operator decision layer + Wing Stack + primary/secondary gamma
# (pure: translate raw structure into human-readable operator context; NEVER
# invents data — a missing field reads "unavailable")
# ══════════════════════════════════════════════════════════════════════════════

def fmt_distance(v: Any) -> str:
    """Signed distance in points: 5.0 → '+5', -12.5 → '-12.50'. None → '—'."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{int(x):+d}" if x == int(x) else f"{x:+.2f}"


def _num_or_none(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _wing_entry(label: str, tier: str, strike: Any, spot: float | None) -> dict[str, Any]:
    st = _num_or_none(strike)
    sp = _num_or_none(spot)
    dist = (st - sp) if (st is not None and sp is not None) else None
    return {
        "label": label, "tier": tier, "strike": st,
        "strike_fmt": fmt_strike(st) if st is not None else "—",
        "distance": dist,
        "distance_fmt": fmt_distance(dist) if dist is not None else "—",
        "available": st is not None,
    }


def wing_stack(exposures: Any, spot: Any = None) -> dict[str, Any]:
    """Structured Wing Stack: PUT_CEILING + CALL_FLOOR at the 2K / 5K / 10K tiers,
    plus the nearest wing and the 'primary' wing (the strongest available tier,
    nearest spot). Missing tiers are marked available=False (shown as '—')."""
    ex = exposures
    sp = _num_or_none(spot)
    put_ceilings = [
        _wing_entry("PUT_CEILING 2K", "2k", getattr(ex, "put_ceiling_2k", None), sp),
        _wing_entry("PUT_CEILING 5K", "5k", getattr(ex, "put_ceiling_5k", None), sp),
        _wing_entry("PUT_CEILING 10K", "10k", getattr(ex, "put_ceiling_10k", None), sp),
    ]
    call_floors = [
        _wing_entry("CALL_FLOOR 2K", "2k", getattr(ex, "call_floor_2k", None), sp),
        _wing_entry("CALL_FLOOR 5K", "5k", getattr(ex, "call_floor_5k", None), sp),
        _wing_entry("CALL_FLOOR 10K", "10k", getattr(ex, "call_floor_10k", None), sp),
    ]
    available = [e for e in (put_ceilings + call_floors) if e["available"]]

    nearest = None
    if available and sp is not None:
        nearest = min(available, key=lambda e: abs(e["distance"]))

    # Primary wing = strongest available tier (10K > 5K > 2K), nearest spot within it.
    primary = None
    for tier in ("10k", "5k", "2k"):
        tier_entries = [e for e in available if e["tier"] == tier]
        if tier_entries:
            primary = (min(tier_entries, key=lambda e: abs(e["distance"]))
                       if sp is not None else tier_entries[0])
            break

    return {
        "put_ceilings": put_ceilings,
        "call_floors": call_floors,
        "nearest_wing": nearest,
        "primary_wing": primary,
        "spot": sp,
        "any_available": bool(available),
    }


def primary_secondary_gamma(exposures: Any, spot: Any = None) -> dict[str, Any]:
    """Primary / secondary gamma levels for the prime cards + decision layer.

    Prefers the mapped ZS payload clusters (`gamma_primary` / `gamma_secondary`).
    When absent, DERIVES a display-only primary/secondary from the available
    gamma structure (call_wall / put_wall / gamma_flip) ranked by closeness to
    spot — deterministic. When nothing is available, source='unavailable' and the
    summary says so (never invents a value)."""
    ex = exposures
    sp = _num_or_none(spot)
    gp = _num_or_none(getattr(ex, "gamma_primary", None))
    gs = _num_or_none(getattr(ex, "gamma_secondary", None))

    if gp is not None:
        source, primary, secondary = "payload_cluster", gp, gs
        note = "From ZS gamma clusters (gamma.cluster_primary / cluster_secondary)."
    else:
        # deterministic derivation from available gamma structure
        cands: list[float] = []
        for v in (getattr(ex, "call_wall", None), getattr(ex, "put_wall", None),
                  getattr(ex, "gamma_flip", None)):
            n = _num_or_none(v)
            if n is not None and n not in cands:
                cands.append(n)
        if sp is not None:
            cands.sort(key=lambda v: abs(v - sp))
        else:
            cands.sort(reverse=True)
        if cands:
            source = "derived_from_walls"
            primary = cands[0]
            secondary = cands[1] if len(cands) > 1 else None
            note = "Derived from gamma walls/flip nearest spot (no explicit clusters in payload)."
        else:
            source, primary, secondary = "unavailable", None, None
            note = "Primary/secondary gamma unavailable from current structure payload."

    return {
        "primary": primary, "primary_fmt": fmt_strike(primary) if primary is not None else "—",
        "secondary": secondary,
        "secondary_fmt": fmt_strike(secondary) if secondary is not None else "—",
        "source": source,
        "available": primary is not None,
        "note": note,
    }


# DDOI is intentionally NOT a prime cockpit card (Phase 9H). It is shown only
# under Advanced Structure / raw diagnostics, and only when a value is present.
DDOI_HELP = ("DDOI is a dealer-positioning pin/gravity reference. It is only shown "
             "when available and relevant.")


def ddoi_advanced(exposures: Any) -> dict[str, Any]:
    """DDOI for the Advanced Structure expander only (never a prime card)."""
    v = _num_or_none(getattr(exposures, "ddoi_pin", None))
    if v is None:
        return {"value": None, "value_fmt": "—", "available": False,
                "note": "Unavailable — DDOI is not in the current public ZS payload."}
    return {"value": v, "value_fmt": fmt_strike(v), "available": True,
            "note": "Dealer-positioning pin/gravity reference."}


def _spot_vs_level(spot: float | None, level: float | None, near_pts: float = 8.0) -> str:
    """'below' / 'above' / 'near' a level (or '' when either is missing)."""
    if spot is None or level is None:
        return ""
    diff = spot - level
    if abs(diff) <= near_pts:
        return "near"
    return "above" if diff > 0 else "below"


def operator_decision_layer(*, spot: Any, gamma_regime: Any, da_gex: Any,
                            gamma: dict[str, Any], wings: dict[str, Any],
                            best_eligible: dict[str, Any] | None = None,
                            chain_available: bool = True,
                            wds: dict[str, Any] | None = None) -> dict[str, str]:
    """Translate structure into the 5-part operator summary. Pure; every part is
    guarded so missing data reads 'unavailable' rather than inventing context.

    `gamma` = output of `primary_secondary_gamma`; `wings` = `wing_stack`;
    `wds` = `wing_dominance` (Phase 9J). When a valid dominant 10K WDS wing
    exists it is presented as the PRIMARY structure, and the nearest 2K/5K wing is
    framed as immediate breach risk — NOT the primary structure.
    """
    sp = _num_or_none(spot)
    regime = gamma_regime if isinstance(gamma_regime, str) and gamma_regime else None
    g_primary = gamma.get("primary")
    near = wings.get("nearest_wing")
    primary_wing = wings.get("primary_wing")
    wds = wds or {}
    # ACTIVE dominant requires a VALID corridor (CW1 < spot < PW1) — Phase 10A.
    has_dominant = (wds.get("dominant_wing_side") in ("CALL", "PUT")
                    and wds.get("wds_source") == "true")
    corridor_valid = bool(wds.get("corridor_valid"))
    _has_10k = wds.get("call_w1_strike") is not None or wds.get("put_w1_strike") is not None
    _has_wing_ctx = _has_10k or wds.get("raw_dominant_side") in ("CALL", "PUT")

    # ── Structure Read (corridor status FIRST — only an active corridor is the
    # primary structure; a call floor above spot is NEVER an active floor) ──
    parts: list[str] = []
    parts.append(f"Spot {fmt_price(sp)}." if sp is not None else "Spot unavailable.")
    if _has_wing_ctx:
        parts.append("Structure status: Active corridor." if corridor_valid
                     else "Structure status: Inactive — corridor not formed.")
    if has_dominant:
        parts.append(
            f"Dominant wing is {wds['dominant_wing_label']} at "
            f"{fmt_strike(wds['dominant_wing_strike'])} with WDS {wds['dominant_wing_wds_pct']} — "
            f"Tier {wds['dominant_wing_tier']} ({WDS_TIER_MEANING.get(wds['dominant_wing_tier'], '')}).")
    elif _has_wing_ctx and wds.get("wds_reason"):
        parts.append(wds["wds_reason"])
    if gamma.get("available"):
        rel = _spot_vs_level(sp, g_primary)
        seg = f"Primary gamma {gamma['primary_fmt']}"
        if gamma.get("secondary") is not None:
            seg += f", secondary gamma {gamma['secondary_fmt']}"
        if rel:
            seg += f"; spot is {rel} primary gamma"
        parts.append(seg + ".")
    else:
        parts.append("Primary/secondary gamma unavailable from current structure payload.")
    if near:
        _npts = str(near["distance_fmt"]).lstrip("+-")
        _nd = near.get("distance")
        _dir = ("below spot" if (_nd is not None and _nd < 0)
                else "above spot" if (_nd is not None and _nd > 0) else "from spot")
        if has_dominant:
            parts.append(f"Nearest wing is {near['label']} at {near['strike_fmt']}, only "
                         f"{_npts} pts from spot — immediate breach risk but not the "
                         "primary structure.")
        elif _has_wing_ctx and not corridor_valid:
            parts.append(f"Nearest local wing is {near['label']} at {near['strike_fmt']}, "
                         f"{_npts} pts {_dir} — immediate breach risk, but the full 10K wing "
                         "corridor is not formed.")
        else:
            parts.append(f"Nearest wing: {near['label']} at {near['strike_fmt']} "
                         f"({near['distance_fmt']} pts).")
    parts.append(f"Gamma regime is {regime}." if regime else "Gamma regime unavailable.")
    structure_read = " ".join(parts)

    # ── Trade Bias ──
    if regime == "negative":
        bias = ("Negative gamma regime — structure may be less pinning and moves can "
                "accelerate (more directional). Put-credit candidates need bullish "
                "confirmation; call-credit candidates must respect overhead structure.")
    elif regime == "positive":
        bias = ("Positive gamma regime — structure tends to pin/mean-revert toward gamma "
                "levels, so range-bound credit setups are more favorable. Still respect "
                "the nearest wing on each side.")
    else:
        bias = "Gamma regime unavailable — directional bias cannot be inferred from structure."

    # ── Candidate Risk ──
    if near and near.get("distance") is not None:
        d = abs(near["distance"])
        proximity = ("Spot is close to" if d <= 10 else "Spot has room to")
        risk = (f"{proximity} the nearest wing ({near['label']} {near['strike_fmt']}, "
                f"{near['distance_fmt']} pts).")
        if regime == "negative":
            risk += " Negative gamma can accelerate a breach of that level."
        if has_dominant:
            risk += (f" Primary structure is the dominant {wds['dominant_wing_label']} at "
                     f"{fmt_strike(wds['dominant_wing_strike'])} (WDS {wds['dominant_wing_wds_pct']}, "
                     f"Tier {wds['dominant_wing_tier']}).")
        elif _has_wing_ctx and not corridor_valid:
            risk += " The 10K wing corridor is not formed, so treat this as local risk only."
        elif corridor_valid and primary_wing and primary_wing is not near:
            risk += f" Primary wing: {primary_wing['label']} {primary_wing['strike_fmt']}."
    else:
        risk = "Wing structure unavailable — candidate breach risk cannot be assessed."

    # ── Best Eligible Setup ──
    if best_eligible:
        be = best_eligible
        bits = [str(be.get("side") or "setup")]
        if be.get("short") is not None and be.get("long") is not None:
            bits.append(f"{fmt_strike(be['short'])}/{fmt_strike(be['long'])}")
        tail = []
        if be.get("score") is not None:
            tail.append(f"score {be['score']}")
        if be.get("credit") is not None:
            tail.append(f"credit {fmt_money(be['credit'])}")
        best = " ".join(bits) + (f" — {', '.join(tail)}" if tail else "")
        if be.get("reason"):
            best += f". {be['reason']}"
    elif not chain_available:
        best = "Quote chain unavailable — no eligible setup can be priced this scan."
    else:
        best = "No eligible setup surfaced in the current scan (see Ranked candidates)."

    # ── Why / Why Not ──
    if best_eligible:
        why = ("Why: this side cleared the selector's eligibility + risk gates"
               + (f" ({best_eligible.get('reason')})" if best_eligible.get("reason") else "")
               + ".")
    else:
        why = ("Why not: no candidate cleared the selector gates this scan — check Ranked "
               "candidates for blockers (score threshold, quote validation, side filters).")
    if regime == "negative":
        why += " Negative gamma argues for tighter respect of the nearest wall."
    elif regime == "positive":
        why += " Positive gamma supports range-bound credit capture near structure."

    return {
        "structure_read": structure_read,
        "trade_bias": bias,
        "candidate_risk": risk,
        "best_eligible_setup": best,
        "why_why_not": why,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Phase 9I — quote-chain diagnostics + stats/drawdown math + EOD staleness
# (pure: read-only translation of provider/ledger state into trader-facing copy
# and chartable series; NEVER overclaims a reason it cannot detect)
# ══════════════════════════════════════════════════════════════════════════════

QUOTE_REASON_SIMPLE: dict[str, str] = {
    "chain_available": "Quotes available.",
    "provider_mock": "Using sandbox mock quotes (not live market data).",
    "tasty_config_mock_fallback": "Tasty not configured — using sandbox mock quotes (check .env).",
    "provider_null": "Quotes unavailable: provider is set to manual marks (null).",
    "tasty_config_error": "Quotes unavailable: Tastytrade is not configured (check .env).",
    "tasty_auth_failed": "Quotes unavailable: Tastytrade authentication failed.",
    "root_or_expiry_unresolved": "Quotes unavailable: Tasty could not resolve the root/expiry.",
    "tasty_http_error": "Quotes unavailable: Tastytrade returned an error fetching quotes.",
    "tasty_no_chain": "Quotes unavailable: Tasty returned no chain for the selected expiry/root.",
    "structure_error": "Quotes unavailable: the exposure/structure provider errored.",
    "market_closed_or_stale": "Quotes unavailable: market closed or stale Tasty chain.",
    "unknown": "Quotes unavailable: provider returned no usable chain.",
}


def quote_chain_status(*, resolved_quote_name: Any, quote_status: Any = None,
                       quote_provider_error: Any = None, structure_error: Any = None,
                       chain: Any = None) -> dict[str, Any]:
    """Diagnose WHY a quote chain is (un)available → {available, reason_code,
    simple_reason, advanced}. Deterministic; never overclaims — unknown causes
    fall back to 'provider returned no usable chain'. Pure, never raises."""
    name = str(resolved_quote_name or "").lower()
    last_error = getattr(quote_status, "last_error", None)
    notes = getattr(quote_status, "notes", None)
    connected = getattr(quote_status, "connected", None)
    last_chain_ts = getattr(quote_status, "last_chain_ts", None)
    provider = getattr(quote_status, "provider_name", None) or resolved_quote_name

    advanced = {
        "provider": provider,
        "connected": connected,
        "last_error": last_error,
        "last_chain_ts": str(last_chain_ts) if last_chain_ts is not None else None,
        "notes": notes,
        "quote_provider_error": quote_provider_error,
        "structure_error": structure_error,
        "chain_present": chain is not None,
    }

    if chain is not None:
        # A present chain means quotes ARE usable. If we're on mock because a
        # Tasty config error forced a fallback, say so (still available).
        if name == "mock" and quote_provider_error:
            code = "tasty_config_mock_fallback"
        elif name == "mock":
            code = "provider_mock"
        else:
            code = "chain_available"
        return {"available": True, "reason_code": code,
                "simple_reason": QUOTE_REASON_SIMPLE[code], "advanced": advanced}

    # chain is None → diagnose the cause.
    if name == "null":
        code = "provider_null"
    elif name == "mock":
        code = "provider_mock"
    elif quote_provider_error:
        code = "tasty_config_error"
    elif isinstance(last_error, str) and last_error:
        le = last_error.lower()
        if "auth_failed" in le:
            code = "tasty_auth_failed"
        elif "chain_unresolved" in le:
            code = "root_or_expiry_unresolved"
        elif "quote_fetch_failed" in le:
            code = "tasty_http_error"
        else:
            code = "market_closed_or_stale"
    elif structure_error:
        code = "structure_error"
    elif connected is False and name == "tastytrade":
        code = "tasty_no_chain"
    elif last_chain_ts is None and name == "tastytrade":
        code = "market_closed_or_stale"
    else:
        code = "unknown"

    return {"available": False, "reason_code": code,
            "simple_reason": QUOTE_REASON_SIMPLE.get(code, QUOTE_REASON_SIMPLE["unknown"]),
            "advanced": advanced}


# ── cockpit quote STATE model (distinct buckets, not one "unavailable") ──────
# Classifies the cockpit's ACTUAL fetched chain + provider state into one of nine
# distinct states so the UI never collapses a returned-but-validation-blocked
# chain into a generic "unavailable". PURE — reads already-fetched data; no
# network, no secrets.

COCKPIT_QUOTE_STATES: tuple[str, ...] = (
    "not_configured", "auth_failed", "root_unresolved", "expiration_unavailable",
    "quote_request_skipped", "chain_unavailable", "chain_resolved_quotes_unavailable",
    "chain_returned_missing_required_strikes", "chain_returned_stale",
    "chain_returned_validation_failed", "chain_returned_usable", "mock", "unknown_error",
)

_COCKPIT_QUOTE_LABELS: dict[str, str] = {
    "not_configured": "not configured",
    "auth_failed": "auth failed",
    "root_unresolved": "root unresolved",
    "expiration_unavailable": "expiration unavailable",
    "quote_request_skipped": "quote request skipped / no required strikes",
    "chain_unavailable": "chain unavailable",
    "chain_resolved_quotes_unavailable": "chain resolved / quotes unavailable",
    "chain_returned_missing_required_strikes": "chain returned / missing required strikes",
    "chain_returned_stale": "chain returned / quotes stale",
    "chain_returned_validation_failed": "chain returned / validation blocked",
    "chain_returned_usable": "available",
    "mock": "mock",
    "unknown_error": "unknown error",
}

_GENERIC_NO_CHAIN_BANNER = (
    "Tasty market data unavailable for {symbol}. The market may be closed, quotes "
    "stale, or the symbol unsupported by the market-data engine. Try Sandbox mode "
    "or check during RTH."
)


def _eligible_hint(state: str) -> str:
    if state == "chain_returned_usable":
        return "yes"
    if state in (
        "chain_returned_stale",
        "chain_returned_validation_failed",
        "chain_returned_missing_required_strikes",
        "chain_resolved_quotes_unavailable",
    ):
        return "blocked"
    if state == "mock":
        return "sandbox"
    return "no"


def _cockpit_banner(state: str, symbol: str, top_blocker: Any) -> str | None:
    if state in ("chain_returned_usable", "mock"):
        return None
    if state == "chain_returned_validation_failed":
        blk = f" ({top_blocker})" if top_blocker else ""
        return (
            f"Tasty chain returned for {symbol}, but quote validation blocked usable "
            f"candidates{blk}. No eligible setup could be priced under the current "
            "quote-validation rules."
        )
    if state == "chain_returned_stale":
        return (f"Tasty chain returned for {symbol}, but quotes are stale. Structure "
                "preview only until fresh RTH quotes arrive.")
    if state == "chain_returned_missing_required_strikes":
        return (f"Tasty chain returned for {symbol}, but it did not include every "
                "required strike. No eligible setup can be priced from this payload.")
    if state == "chain_resolved_quotes_unavailable":
        return f"Chain resolved, quotes unavailable for {symbol}."
    if state == "quote_request_skipped":
        return "Quote request skipped — no required strikes. Structure anchors may be missing."
    if state == "not_configured":
        return (f"Tasty is not configured for {symbol}. Add TASTY_* OAuth credentials "
                "to .env (see the 'Why are quotes unavailable?' details).")
    if state == "auth_failed":
        return f"Tasty auth failed / session invalid for {symbol}."
    if state == "root_unresolved":
        return f"Tasty could not resolve the option root for {symbol}."
    if state == "expiration_unavailable":
        return f"Tasty has no matching expiration for {symbol} right now."
    if state == "unknown_error":
        return f"Tasty market data unavailable for {symbol} (unexpected provider state)."
    # chain_unavailable → the original generic banner (this is the ONLY place it shows)
    return _GENERIC_NO_CHAIN_BANNER.format(symbol=symbol)


def cockpit_quote_status(
    *,
    symbol: Any,
    resolved_quote_name: Any,
    chain: Any,
    quote_status: Any = None,
    quote_provider_error: Any = None,
    structure_error: Any = None,
    max_spread_abs: Any = None,
    max_age_seconds: Any = None,
    requested_strikes: Any = None,
    dte: Any = None,
) -> dict[str, Any]:
    """Classify the cockpit's quote state from the ALREADY-FETCHED chain + provider.

    Returns {state, label, available, eligible_hint, banner, details}. `available`
    is True only when there are usable (validation-not-failed, priceable) quotes,
    or for the sandbox mock. Never raises; never echoes secrets (only counts,
    thresholds, root/expiry, and sanitized provider error strings).
    """
    name = str(resolved_quote_name or "").lower()
    last_error = getattr(quote_status, "last_error", None)
    le = str(last_error).lower() if last_error else ""
    notes = str(getattr(quote_status, "notes", "") or "")
    auth_mode = ""
    for part in notes.split(";"):
        part = part.strip()
        if part.startswith("auth_mode="):
            auth_mode = part.split("=", 1)[1].strip()
            break
    auth_configured = bool(auth_mode and auth_mode.lower() not in ("none", "unconfigured"))
    quotes = list(getattr(chain, "quotes", None) or []) if chain is not None else []
    root = getattr(chain, "resolved_root_symbol", None) if chain is not None else None
    expiry = getattr(chain, "expiry", None) if chain is not None else None
    strikes = sorted({
        q.strike for q in quotes if getattr(q, "strike", None) is not None
    })
    requested_strikes_provided = requested_strikes is not None
    try:
        req = sorted({float(s) for s in (requested_strikes or [])})
    except (TypeError, ValueError):
        req = []
    returned = set(strikes)
    missing = [s for s in req if s not in returned]

    failed = [q for q in quotes if getattr(q, "validation_passed", None) is False]
    usable = [
        q for q in quotes
        if getattr(q, "validation_passed", None) is not False
        and getattr(q, "mid", None) is not None
    ]
    blockers: dict[str, int] = {}
    observed_failing_spread: float | None = None
    for q in failed:
        reason = str(getattr(q, "validation_rejection_reason", None) or "unknown")
        key = reason.split("(")[0].strip().lower()
        blockers[key] = blockers.get(key, 0) + 1
        b, a = getattr(q, "bid", None), getattr(q, "ask", None)
        if b is not None and a is not None and "spread" in key:
            observed_failing_spread = max(observed_failing_spread or 0.0, float(a) - float(b))
    top_blocker = max(blockers, key=lambda k: blockers[k]) if blockers else None

    if name in ("mock", "stub"):
        state = "mock"
    elif chain is None:
        if quote_provider_error:
            state = "not_configured"
        elif (requested_strikes_provided and not req) or "no_required_strikes" in le:
            state = "quote_request_skipped"
        elif "auth" in le:
            state = "auth_failed"
        elif "expir" in le:
            state = "expiration_unavailable"
        elif "unresolved" in le or "root" in le:
            state = "root_unresolved"
        elif "quote_fetch" in le or "http" in le:
            state = "chain_unavailable"
        elif name == "null":
            state = "chain_unavailable"
        elif structure_error:
            state = "chain_unavailable"
        elif le:
            state = "unknown_error"
        else:
            state = "chain_unavailable"
    else:
        if requested_strikes_provided and not req:
            state = "quote_request_skipped"
        elif not quotes:
            state = "chain_resolved_quotes_unavailable"
        elif requested_strikes_provided and missing:
            state = "chain_returned_missing_required_strikes"
        elif quotes and not usable and str(top_blocker or "").lower() == "stale":
            state = "chain_returned_stale"
        else:
            state = ("chain_returned_validation_failed" if (quotes and not usable)
                     else "chain_returned_usable")

    details = {
        "quote_provider": resolved_quote_name,
        "auth_mode": auth_mode or None,
        "auth_mode_configured": auth_configured,
        "chain_returned": chain is not None,
        "quote_count": len(quotes),
        "validation_state": state,
        "validation_passed_count": len(usable),
        "validation_failed_count": len(failed),
        "validation_blockers": blockers,
        "top_blocker": top_blocker,
        "max_spread_abs": (float(max_spread_abs) if max_spread_abs is not None else None),
        "max_age_seconds": (float(max_age_seconds) if max_age_seconds is not None else None),
        "observed_failing_spread": (round(observed_failing_spread, 2)
                                    if observed_failing_spread is not None else None),
        "root": root,
        "expiration": expiry,
        "dte": dte,
        "strike_min": strikes[0] if strikes else None,
        "strike_max": strikes[-1] if strikes else None,
        "requested_strikes": req,
        "required_strike_count": len(req),
        "missing_strikes": missing,
        "last_error": last_error,
    }
    return {
        "state": state,
        "label": _COCKPIT_QUOTE_LABELS.get(state, "unknown error"),
        "available": state in ("chain_returned_usable", "mock"),
        "eligible_hint": _eligible_hint(state),
        "banner": _cockpit_banner(state, str(symbol or "—"), top_blocker),
        "details": details,
    }


def build_quote_request(symbol: Any, structure: Any, strategies: Any):  # type: ignore[no-untyped-def]
    """Build the SAME structure-anchored QuoteRequest the scanner uses, so the
    cockpit's Tasty chain fetch supplies required_strikes (the REST provider
    returns no chain without them). Pure; no network."""
    from src.providers.quotes.types import QuoteRequest
    req: set[float] = set()
    for strat in (strategies or {}).values():
        fn = getattr(strat, "required_quote_strikes", None)
        if not callable(fn):
            continue
        try:
            for k in fn(structure, getattr(strat, "default_parameters", {}) or {}) or ():
                if k is not None:
                    req.add(float(k))
        except Exception:        # never fail the render on one strategy's hint
            continue
    spot = getattr(structure, "spot", None)
    if not (isinstance(spot, (int, float)) and spot > 0):
        spot = getattr(getattr(structure, "exposures", None), "maxvol", None)
    return QuoteRequest(
        symbol=symbol,
        expiry=getattr(structure, "expiry", None),
        spot_hint=(float(spot) if isinstance(spot, (int, float)) and spot > 0 else None),
        required_strikes=tuple(sorted(req)),
        spot_hint_source="structure_spot",
    )


# ── equity curve + drawdown math (from closed-trade rows) ────────────────────

def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def equity_curve_from_closed_trades(closed_trades: list[dict] | None) -> list[dict[str, Any]]:
    """Time-ordered cumulative realized P&L. Each point: {closed_at, realized_pnl,
    cumulative}. Sorted by closed_at (rows with no realized_pnl are skipped)."""
    rows: list[tuple[str, float]] = []
    for t in closed_trades or []:
        rp = _f(t.get("realized_pnl"))
        if rp is None:
            continue
        rows.append((str(t.get("closed_at") or ""), rp))
    rows.sort(key=lambda r: r[0])
    out: list[dict[str, Any]] = []
    cum = 0.0
    for closed_at, rp in rows:
        cum += rp
        out.append({"closed_at": closed_at, "realized_pnl": round(rp, 2),
                    "cumulative": round(cum, 2)})
    return out


def drawdown_series(cumulative_values: list[Any]) -> list[dict[str, float]]:
    """Per-point {peak, drawdown} over a cumulative-equity series. drawdown =
    value - running_peak (always <= 0)."""
    out: list[dict[str, float]] = []
    peak: float | None = None
    for v in cumulative_values:
        x = _f(v)
        if x is None:
            out.append({"peak": round(peak, 2) if peak is not None else 0.0, "drawdown": 0.0})
            continue
        peak = x if peak is None else max(peak, x)
        out.append({"peak": round(peak, 2), "drawdown": round(x - peak, 2)})
    return out


def max_drawdown(cumulative_values: list[Any], starting_balance: Any = None) -> dict[str, Any]:
    """Max peak-to-trough drawdown over a cumulative-equity series. When
    ``starting_balance`` is given, percent is computed against peak EQUITY
    (balance + cumulative); otherwise pct is None unless the peak cumulative > 0."""
    base = _f(starting_balance) or 0.0
    peak: float | None = None
    peak_i = 0
    mdd = 0.0
    mdd_peak: float | None = None
    mdd_trough: float | None = None
    pi = ti = 0
    for i, v in enumerate(cumulative_values):
        x = _f(v)
        if x is None:
            continue
        eq = base + x
        if peak is None or eq > peak:
            peak, peak_i = eq, i
        dd = eq - peak
        if dd < mdd:
            mdd, mdd_peak, mdd_trough, pi, ti = dd, peak, eq, peak_i, i
    pct = round(mdd / mdd_peak * 100.0, 2) if (mdd_peak and mdd_peak > 0) else None
    return {
        "max_drawdown": round(mdd, 2),
        "peak_value": round(mdd_peak, 2) if mdd_peak is not None else None,
        "trough_value": round(mdd_trough, 2) if mdd_trough is not None else None,
        "peak_index": pi, "trough_index": ti, "max_drawdown_pct": pct,
    }


def daily_pnl_from_closed_trades(closed_trades: list[dict] | None) -> list[dict[str, Any]]:
    """date (YYYY-MM-DD of closed_at) → summed realized P&L, ordered by date."""
    acc: dict[str, float] = {}
    for t in closed_trades or []:
        rp = _f(t.get("realized_pnl"))
        ca = t.get("closed_at")
        if rp is None or not ca:
            continue
        day = str(ca)[:10]
        acc[day] = acc.get(day, 0.0) + rp
    return [{"date": d, "realized_pnl": round(acc[d], 2)} for d in sorted(acc)]


def pnl_by_profile(closed_trades: list[dict] | None) -> list[dict[str, Any]]:
    """Per-profile realized P&L + win/loss counts, ordered by profile_id."""
    acc: dict[str, float] = {}
    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    for t in closed_trades or []:
        rp = _f(t.get("realized_pnl"))
        if rp is None:
            continue
        pid = str(t.get("profile_id") or "—")
        acc[pid] = acc.get(pid, 0.0) + rp
        if rp > 0:
            wins[pid] = wins.get(pid, 0) + 1
        elif rp < 0:
            losses[pid] = losses.get(pid, 0) + 1
    return [{"profile_id": p, "realized_pnl": round(acc[p], 2),
             "wins": wins.get(p, 0), "losses": losses.get(p, 0)} for p in sorted(acc)]


def trade_outcome_counts(closed_trades: list[dict] | None) -> dict[str, Any]:
    """{wins, losses, flat, total, win_rate%} from closed-trade realized P&L."""
    wins = losses = flat = 0
    for t in closed_trades or []:
        rp = _f(t.get("realized_pnl"))
        if rp is None:
            continue
        if rp > 0:
            wins += 1
        elif rp < 0:
            losses += 1
        else:
            flat += 1
    decided = wins + losses
    return {"wins": wins, "losses": losses, "flat": flat, "total": wins + losses + flat,
            "win_rate": round(wins / decided * 100.0, 1) if decided else 0.0}


def exit_reason_counts(closed_trades: list[dict] | None) -> list[tuple[str, int]]:
    """Exit-reason histogram (take_profit / stop_loss / eod_exit / …), most first."""
    acc: dict[str, int] = {}
    for t in closed_trades or []:
        r = str(t.get("exit_reason") or "unknown")
        acc[r] = acc.get(r, 0) + 1
    return sorted(acc.items(), key=lambda kv: -kv[1])


# ── EOD summary staleness (file mtime vs latest run) ─────────────────────────

def _parse_dt(v: Any) -> datetime | None:
    """Parse an ISO string OR epoch float → naive datetime. None/invalid → None.
    Pure: parses GIVEN values, never reads the clock."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v))
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip().replace("Z", "+00:00")
    if not s:
        return None
    for cand in (s, s[:19]):
        try:
            dt = datetime.fromisoformat(cand)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            continue
    return None


def is_eod_stale(eod_generated_at: Any, latest_run_at: Any) -> bool:
    """True when the EOD summary is missing or predates the latest run. Args may
    be ISO strings, epoch floats, or None."""
    e = _parse_dt(eod_generated_at)
    if e is None:
        return True                      # no EOD summary → stale
    r = _parse_dt(latest_run_at)
    if r is None:
        return False                     # have EOD, no run to compare → fresh enough
    return e < r


def eod_summary_status(output_root: Path | str | None = None,
                       forward_root: Path | str | None = None) -> dict[str, Any]:
    """Read-only: EOD summary presence + staleness vs the latest forward run.
    Returns {exists, generated_at, date, latest_run_at, stale, note}."""
    from src.forward import review as fr
    base = Path(output_root) if output_root else (_REPO_ROOT / "outputs")
    eod_path = base / "latest" / "eod_summary.json"
    exists = eod_path.is_file()
    generated_at = None
    mtime = None
    date = None
    if exists:
        try:
            mtime = eod_path.stat().st_mtime
            generated_at = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            generated_at = None
        date = (_read_json_file(eod_path) or {}).get("date")
    man = fr.load_latest_manifest(forward_root) or {}
    latest_run_at = man.get("started_at") or man.get("ended_at")
    stale = is_eod_stale(mtime, latest_run_at)
    if not exists:
        note = "No EOD summary generated yet."
    elif stale:
        note = "EOD summary is older than the latest local paper run — regenerate to refresh."
    else:
        note = "EOD summary is up to date with the latest run."
    return {"exists": exists, "generated_at": generated_at, "date": date,
            "latest_run_at": latest_run_at, "stale": stale, "note": note}


# ══════════════════════════════════════════════════════════════════════════════
# Phase 9J — true Wing Dominance Score (WDS)
#
# Dan's WDS is NOT a generic tier-strength ("10K=1.0, 5K=0.7"). A 10K wing (W1)
# is only strong if it DOMINATES the adjacent next strike (W2):
#     WSR = W2_volume / W1_volume   (side-specific volume)
#     WDS = 1 - WSR                 (higher = cleaner / more dominant)
# CALL floor → W2 is one strike LOWER than W1; PUT ceiling → W2 is one HIGHER.
# Wingonomics (source of truth) selects W1 exactly as our mapper does
# (call_floor = min strike where CALL vol ≥ 10000; put_ceiling = max strike where
# PUT vol ≥ 10000) but does NOT itself compute WDS — so WDS is implemented per
# Dan's spec, with documented assumptions (see notes.md / reference_notes.md):
#   • W2 = the next AVAILABLE strike in the series (no fixed 5/10-pt assumption).
#   • WSR uses SIDE-SPECIFIC volume (CALL vol for calls, PUT vol for puts).
#   • No clipping: WSR may exceed 1 → WDS negative → Tier 4 (very weak).
#   • Missing W1 or W2 volume → true WDS UNAVAILABLE (never invented).
# ══════════════════════════════════════════════════════════════════════════════

WDS_TIER_MEANING = {
    1: "clean / dominant wing", 2: "usable / strong enough",
    3: "mixed / caution", 4: "weak / avoid or observe",
}


def wds_tier(wds: Any) -> int | None:
    """Tier from a WDS value: ≥0.75 → 1, ≥0.50 → 2, ≥0.30 → 3, else 4. None when
    wds is None (unavailable)."""
    w = _f(wds)
    if w is None:
        return None
    if w >= 0.75:
        return 1
    if w >= 0.50:
        return 2
    if w >= 0.30:
        return 3
    return 4


def wds_pct(wds: Any) -> str:
    """WDS as a percent string: 0.82 → '82%'. None → '—'."""
    w = _f(wds)
    return f"{round(w * 100)}%" if w is not None else "—"


# ── Phase 10C — read the latest LOCAL backtest results (Task G) ───────────────
# Pure file read of the repo-local outputs/backtests/<dir>. No live API, no
# broker, no order preview. Reuses the pure backtesting metrics so the cockpit
# cards match the CLI's printed summary. Missing dir/files degrade gracefully.

def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    import csv as _csv

    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return list(_csv.DictReader(fh))
    except OSError:
        return []


def _bt_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bt_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y"):
            return True
        if s in ("false", "0", "no", "n"):
            return False
    return None


def _bt_side(v: Any) -> str:
    if v == "CALL_CREDIT":
        return "Call Credit"
    if v == "PUT_CREDIT":
        return "Put Credit"
    return str(v or "—")


def _bt_exit(v: Any) -> str:
    return {
        "TP": "Take Profit",
        "SL": "Stop Loss",
        "EOD": "End of Day",
        "SKIPPED": "Skipped",
    }.get(str(v or ""), str(v or "—"))


def _bt_bool_label(v: Any) -> str:
    b = _bt_bool(v)
    if b is True:
        return "Yes"
    if b is False:
        return "No"
    return "—"


def backtest_trade_display_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Friendly, rounded trade-log rows for the Backtests tab."""
    out: list[dict[str, Any]] = []
    for t in trades:
        pnl = _bt_float(t.get("pnl_dollars"))
        score = _bt_float(t.get("score"))
        selector_score = _bt_float(t.get("selector_score"))
        raw_wds = _bt_float(t.get("raw_wds"))
        active_wds = _bt_float(t.get("active_wds"))
        out.append({
            "Date": t.get("date"),
            "Profile": t.get("profile_id"),
            "Symbol": t.get("symbol"),
            "Side": _bt_side(t.get("side")),
            "Short": fmt_strike(t.get("short_strike")),
            "Long": fmt_strike(t.get("long_strike")),
            "Entry": t.get("entry_timestamp"),
            "Exit": t.get("exit_timestamp"),
            "Exit Reason": _bt_exit(t.get("exit_reason")),
            "Credit": fmt_money(t.get("entry_credit_dollars")),
            "Exit Debit": fmt_money(t.get("exit_debit_dollars")),
            "P&L": fmt_money(pnl) if pnl is not None else "—",
            "P&L Raw": pnl,
            "Contracts": t.get("contracts"),
            "Hold Min": t.get("hold_minutes"),
            "Corridor": _bt_bool_label(t.get("corridor_valid")),
            "WDS Tier": t.get("wds_tier") or "—",
            "Active WDS": f"{active_wds:.2f}" if active_wds is not None else "—",
            "Raw WDS": f"{raw_wds:.2f}" if raw_wds is not None else "—",
            "Distance": fmt_price(t.get("distance_from_spot_to_short")),
            "Score": f"{score:.3f}" if score is not None else "—",
            "Selector Score": f"{selector_score:.3f}" if selector_score is not None else "—",
            "_profile": t.get("profile_id"),
            "_side": t.get("side"),
            "_exit_reason": t.get("exit_reason"),
            "_corridor_valid": _bt_bool(t.get("corridor_valid")),
            "_wds_tier": str(t.get("wds_tier") or ""),
        })
    return out


def _reason_counts(
    no_trade_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in no_trade_rows:
        reason = (
            row.get("first_blocker") or row.get("top_selector_reason")
            or row.get("top_risk_reason") or row.get("top_quote_reason")
            or row.get("reason")
        )
        if reason:
            counts[str(reason)] += 1
    for cand in candidates:
        for key in ("selector_blockers", "risk_rejection_type", "quote_quality_reason"):
            raw = cand.get(key)
            if not raw:
                continue
            for item in str(raw).split(";"):
                item = item.strip()
                if item:
                    counts[item] += 1
    return [{"reason": reason, "count": count} for reason, count in counts.most_common(12)]


def backtest_explainability(
    *,
    run_config: dict[str, Any],
    metrics: dict[str, Any],
    trades: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    no_trade_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    counters = run_config.get("counters") if isinstance(run_config.get("counters"), dict) else {}
    valid_entries = counters.get("valid_entry_snapshots") or 0
    total_candidates = counters.get("candidates") or len(candidates)
    total_trades = metrics.get("total_trades") if metrics else len(trades)
    top_reasons = _reason_counts(no_trade_rows, candidates)
    reason_text = top_reasons[0]["reason"] if top_reasons else "limited valid entries"
    profile_count = len(run_config.get("profiles") or [])
    expected_slots = int(valid_entries or 0)
    if profile_count > 0:
        expected_slots *= profile_count
    parts = [
        f"Evaluated {counters.get('dates_evaluated', 0)} dates",
        f"{valid_entries} valid entry snapshots",
        f"{total_candidates} candidates",
        f"{total_trades} selected trades",
    ]
    if expected_slots and int(total_trades or 0) < expected_slots:
        parts.append(f"most non-trade rows point to {reason_text}")
    summary = ". ".join(parts) + "."
    return {
        "summary": summary,
        "top_reasons": top_reasons,
        "low_trade_count": bool(expected_slots and int(total_trades or 0) < expected_slots),
        "expected_trade_slots": expected_slots,
    }


def read_backtest_results(results_dir: Any) -> dict[str, Any]:
    """Read a backtest results directory and return cockpit-renderable summary:

        {available, reason, run_config, metrics, by_profile, trades, ...}

    ``available`` is False (with a friendly ``reason``) when the directory or
    ``trades.csv`` is missing — the UI shows the reason instead of erroring."""
    import json as _json

    out: dict[str, Any] = {
        "available": False, "reason": "", "run_config": {}, "metrics": {},
        "by_profile": [], "by_side": [], "by_exit_reason": [], "by_corridor": [],
        "by_wds_tier": [], "by_day": [], "daily_pnl": [], "equity_curve": [],
        "candidates": [], "trades": [], "trade_rows": [], "no_trade_reasons": [],
        "explainability": {"summary": "", "top_reasons": []},
        "results_dir": str(results_dir),
    }
    try:
        d = Path(results_dir)
    except (TypeError, ValueError):
        out["reason"] = "No results directory configured."
        return out
    if not d.exists() or not d.is_dir():
        out["reason"] = ("No backtest results yet. Run the command below in a "
                         "terminal, then click Refresh.")
        return out
    trades_path = d / "trades.csv"
    if not trades_path.exists():
        out["reason"] = (f"No trades.csv under {d.name}/. Run a backtest, then Refresh "
                         "(a backtest with zero selected trades writes empty reports).")
        return out
    rc_path = d / "run_config.json"
    if rc_path.exists():
        try:
            out["run_config"] = _json.loads(rc_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            out["run_config"] = {}
    trades = _read_csv_rows(trades_path)
    out["trades"] = trades
    out["trade_rows"] = backtest_trade_display_rows(trades)
    out["candidates"] = _read_csv_rows(d / "candidates.csv")
    out["no_trade_reasons"] = _read_csv_rows(d / "no_trade_reasons.csv")
    out["daily_pnl"] = _read_csv_rows(d / "daily_pnl.csv")
    out["equity_curve"] = _read_csv_rows(d / "equity_curve.csv")
    try:
        from src.backtesting import reports as _reports
        out["metrics"] = _reports.metrics(
            trades,
            starting_balance=out["run_config"].get("starting_balance") or 0.0,
            contracts=out["run_config"].get("contracts"),
        )
    except Exception:                                  # never break the cockpit
        out["metrics"] = {"total_trades": len(trades)}
    out["by_profile"] = _read_csv_rows(d / "summary_by_profile.csv")
    out["by_side"] = _read_csv_rows(d / "summary_by_side.csv")
    out["by_exit_reason"] = _read_csv_rows(d / "summary_by_exit_reason.csv")
    out["by_corridor"] = _read_csv_rows(d / "summary_by_corridor.csv")
    out["by_wds_tier"] = _read_csv_rows(d / "summary_by_wds_tier.csv")
    out["by_day"] = _read_csv_rows(d / "summary_by_day.csv")
    out["explainability"] = backtest_explainability(
        run_config=out["run_config"],
        metrics=out["metrics"],
        trades=trades,
        candidates=out["candidates"],
        no_trade_rows=out["no_trade_reasons"],
    )
    out["available"] = True
    return out


def read_backtest_comparison(results_dir: Any) -> dict[str, Any]:
    """Read Phase 10E comparison outputs for the Backtests tab."""
    import json as _json

    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "results_dir": str(results_dir),
        "run_config": {},
        "rankings": [],
        "dynamic_vs_control": [],
        "by_corridor": [],
        "by_wds_tier": [],
        "selected_side_summary": [],
        "dynamic_vs_best_opposite": [],
        "dynamic_failure_summary": [],
        "call_control_edge_summary": [],
        "research_recommendations": [],
        "attribution_narrative": "",
        "control_benchmark_note": "",
        "trades": [],
        "trade_rows": [],
        "narrative": "",
    }
    try:
        directory = Path(results_dir)
    except (TypeError, ValueError):
        out["reason"] = "No comparison results directory configured."
        return out
    rankings_path = directory / "profile_rankings.csv"
    if not rankings_path.is_file():
        out["reason"] = "No comparison results yet. Run a comparison above."
        return out
    config_path = directory / "run_config.json"
    if config_path.is_file():
        try:
            out["run_config"] = _json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["run_config"] = {}
    out["rankings"] = _read_csv_rows(rankings_path)
    out["dynamic_vs_control"] = _read_csv_rows(directory / "dynamic_vs_control.csv")
    out["by_corridor"] = _read_csv_rows(directory / "by_corridor.csv")
    out["by_wds_tier"] = _read_csv_rows(directory / "by_wds_tier.csv")
    out["selected_side_summary"] = _read_csv_rows(directory / "selected_side_summary.csv")
    out["dynamic_vs_best_opposite"] = _read_csv_rows(
        directory / "dynamic_vs_best_opposite.csv"
    )
    out["dynamic_failure_summary"] = _read_csv_rows(
        directory / "dynamic_failure_summary.csv"
    )
    out["call_control_edge_summary"] = _read_csv_rows(
        directory / "call_control_edge_summary.csv"
    )
    out["research_recommendations"] = _read_csv_rows(
        directory / "research_recommendations.csv"
    )
    out["trades"] = _read_csv_rows(directory / "trades.csv")
    out["trade_rows"] = backtest_trade_display_rows(out["trades"])
    narrative_path = directory / "narrative_summary.md"
    if narrative_path.is_file():
        try:
            out["narrative"] = narrative_path.read_text(encoding="utf-8").replace(
                "# Backtest Comparison Summary", ""
            ).strip()
        except OSError:
            out["narrative"] = ""
    attribution_path = directory / "attribution_summary.json"
    if attribution_path.is_file():
        try:
            attribution = _json.loads(attribution_path.read_text(encoding="utf-8"))
            out["attribution_narrative"] = attribution.get("narrative") or ""
            out["control_benchmark_note"] = attribution.get("control_benchmark_note") or ""
        except (OSError, ValueError):
            pass
    out["available"] = True
    return out


def read_backtest_optimization(results_dir: Any) -> dict[str, Any]:
    """Read Phase 10G optimization outputs for the Backtests Optimization Lab."""
    import json as _json

    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "results_dir": str(results_dir),
        "run_config": {},
        "rankings": [],
        "promotion_candidates": [],
        "overfit_warnings": [],
        "train_results": [],
        "validation_results": [],
        "holdout_results": [],
        "narrative": "",
    }
    try:
        directory = Path(results_dir)
    except (TypeError, ValueError):
        out["reason"] = "No optimization results directory configured."
        return out
    rankings_path = directory / "rankings.csv"
    if not rankings_path.is_file():
        out["reason"] = "No optimization results yet. Run an optimization above."
        return out
    config_path = directory / "run_config.json"
    if config_path.is_file():
        try:
            out["run_config"] = _json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["run_config"] = {}
    for key in (
        "rankings", "promotion_candidates", "overfit_warnings",
        "train_results", "validation_results", "holdout_results",
    ):
        out[key] = _read_csv_rows(directory / f"{key}.csv")
    narrative_path = directory / "narrative_summary.md"
    if narrative_path.is_file():
        try:
            out["narrative"] = narrative_path.read_text(encoding="utf-8").replace(
                "# Optimization Research Summary", ""
            ).strip()
        except OSError:
            out["narrative"] = ""
    out["available"] = True
    return out


def read_backtest_learning(results_dir: Any) -> dict[str, Any]:
    """Read Phase 11A research outputs for the Backtests Learning Review."""
    import json as _json

    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "results_dir": str(results_dir),
        "run_config": {},
        "feature_performance_summary": [],
        "no_trade_blocker_summary": [],
        "by_side": [],
        "by_threshold": [],
        "by_entry_window": [],
        "by_wds_tier": [],
        "by_corridor": [],
        "hypotheses": [],
        "learned_parameter_sets": [],
        "profitability_attribution_summary": [],
        "feature_interaction_matrix": [],
        "win_driver_matrix": [],
        "loss_driver_matrix": [],
        "filter_impact_analysis": [],
        "strategy_robustness_scorecard": [],
        "call_only_expansion_results": [],
        "call_only_robustness_results": [],
        "dynamic_repair_results": [],
        "by_archetype": [],
        "by_risk_quality": [],
        "by_credit_pct_of_width": [],
        "by_credit_to_stop_risk": [],
        "by_eod_exception": [],
        "by_regime_compatibility": [],
        "risk_quality_rejection_summary": [],
        "audit": "",
        "hypotheses_markdown": "",
        "profitability_markdown": "",
        "filter_impact_markdown": "",
        "robustness_markdown": "",
        "phase11b_smoke_summary": "",
    }
    try:
        directory = Path(results_dir)
    except (TypeError, ValueError):
        out["reason"] = "No learning-review directory configured."
        return out
    summary_path = directory / "feature_performance_summary.csv"
    if not summary_path.is_file():
        out["reason"] = "No learning review yet. Run the Phase 11A learning CLI."
        return out
    for key in (
        "feature_performance_summary", "no_trade_blocker_summary", "by_side",
        "by_threshold", "by_entry_window", "by_wds_tier", "by_corridor",
        "profitability_attribution_summary", "feature_interaction_matrix",
        "win_driver_matrix", "loss_driver_matrix", "filter_impact_analysis",
        "strategy_robustness_scorecard", "call_only_expansion_results",
        "call_only_robustness_results", "dynamic_repair_results",
        "by_archetype", "by_risk_quality", "by_credit_pct_of_width",
        "by_credit_to_stop_risk", "by_eod_exception",
        "by_regime_compatibility", "risk_quality_rejection_summary",
    ):
        out[key] = _read_csv_rows(directory / f"{key}.csv")
    config_path = directory / "run_config.json"
    hypotheses_path = directory / "generated_strategy_hypotheses.json"
    try:
        if config_path.is_file():
            out["run_config"] = _json.loads(config_path.read_text(encoding="utf-8"))
        if hypotheses_path.is_file():
            payload = _json.loads(hypotheses_path.read_text(encoding="utf-8"))
            out["hypotheses"] = payload.get("hypotheses") or []
            out["learned_parameter_sets"] = payload.get("learned_parameter_sets") or []
    except (OSError, ValueError):
        pass
    for key, filename, heading in (
        ("audit", "backtest_assumption_audit.md", "# Backtest Assumption Audit"),
        (
            "hypotheses_markdown",
            "generated_strategy_hypotheses.md",
            "# Generated Strategy Hypotheses",
        ),
        (
            "profitability_markdown",
            "profitability_attribution_summary.md",
            "# Profitability Attribution Summary",
        ),
        (
            "filter_impact_markdown",
            "filter_impact_analysis.md",
            "# Filter Impact Analysis",
        ),
        (
            "robustness_markdown",
            "strategy_robustness_scorecard.md",
            "# Strategy Robustness Scorecard",
        ),
        (
            "phase11b_smoke_summary",
            "phase11b_smoke_summary.md",
            "# Phase 11B Smoke Summary",
        ),
    ):
        path = directory / filename
        if path.is_file():
            try:
                out[key] = path.read_text(encoding="utf-8").replace(heading, "").strip()
            except OSError:
                out[key] = ""
    out["available"] = True
    return out


def read_optuna_research(results_dir: Any) -> dict[str, Any]:
    """Read research-only Optuna outputs without exposing raw config JSON."""
    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "results_dir": str(results_dir),
        "trials": [],
        "param_importance": [],
        "best_trials": "",
        "robustness_summary": "",
    }
    try:
        directory = Path(results_dir)
    except (TypeError, ValueError):
        out["reason"] = "No Optuna research directory configured."
        return out
    trials_path = directory / "optuna_trials.csv"
    if not trials_path.is_file():
        out["reason"] = "No Optuna research run yet."
        return out
    out["trials"] = _read_csv_rows(trials_path)
    out["param_importance"] = _read_csv_rows(directory / "optuna_param_importance.csv")
    for key, filename, heading in (
        ("best_trials", "optuna_best_trials.md", "# Optuna Best Trials"),
        ("robustness_summary", "optuna_robustness_summary.md", "# Optuna Robustness Summary"),
    ):
        path = directory / filename
        if path.is_file():
            try:
                out[key] = path.read_text(encoding="utf-8").replace(heading, "").strip()
            except OSError:
                out[key] = ""
    out["available"] = True
    return out


def read_backtest_robustness_review(results_dir: Any) -> dict[str, Any]:
    """Read Phase 10H robustness-review outputs for the Optimization Lab."""
    import json as _json

    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "results_dir": str(results_dir),
        "run_config": {},
        "expanded_run_summary": [],
        "split_sensitivity_summary": [],
        "candidate_consistency": [],
        "candidate_vs_control_benchmark": [],
        "freeze_criteria": [],
        "freeze_recommendation": {},
        "narrative": "",
    }
    try:
        directory = Path(results_dir)
    except (TypeError, ValueError):
        out["reason"] = "No robustness-review directory configured."
        return out
    summary_path = directory / "split_sensitivity_summary.csv"
    if not summary_path.is_file():
        out["reason"] = "No robustness review yet. Run the Phase 10H review CLI."
        return out
    config_path = directory / "run_config.json"
    recommendation_path = directory / "freeze_recommendation.json"
    try:
        if config_path.is_file():
            out["run_config"] = _json.loads(config_path.read_text(encoding="utf-8"))
        if recommendation_path.is_file():
            out["freeze_recommendation"] = _json.loads(
                recommendation_path.read_text(encoding="utf-8")
            )
    except (OSError, ValueError):
        pass
    for key in (
        "expanded_run_summary",
        "split_sensitivity_summary",
        "candidate_consistency",
        "candidate_vs_control_benchmark",
        "freeze_criteria",
    ):
        out[key] = _read_csv_rows(directory / f"{key}.csv")
    narrative_path = directory / "narrative_summary.md"
    if narrative_path.is_file():
        try:
            out["narrative"] = narrative_path.read_text(encoding="utf-8").replace(
                "# Optimization Robustness Review", ""
            ).strip()
        except OSError:
            out["narrative"] = ""
    out["available"] = True
    return out


def read_backtest_stress_review(results_dir: Any) -> dict[str, Any]:
    """Read Phase 10I near-miss stress-review outputs for Optimization Lab."""
    import json as _json

    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "results_dir": str(results_dir),
        "candidate_profile_snapshot": {},
        "split_stress_summary": [],
        "slippage_stress_summary": [],
        "account_sizing_stress": [],
        "concentration_summary": [],
        "recommendation": {},
        "narrative": "",
    }
    try:
        directory = Path(results_dir)
    except (TypeError, ValueError):
        out["reason"] = "No stress-review directory configured."
        return out
    snapshot_path = directory / "candidate_profile_snapshot.json"
    if not snapshot_path.is_file():
        out["reason"] = "No near-miss stress review yet. Run the Phase 10I stress CLI."
        return out
    try:
        out["candidate_profile_snapshot"] = _json.loads(
            snapshot_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        out["reason"] = "Near-miss stress snapshot is unreadable."
        return out
    for key in (
        "split_stress_summary",
        "slippage_stress_summary",
        "account_sizing_stress",
        "concentration_summary",
    ):
        out[key] = _read_csv_rows(directory / f"{key}.csv")
    narrative_path = directory / "narrative_summary.md"
    if narrative_path.is_file():
        try:
            text = narrative_path.read_text(encoding="utf-8")
            out["narrative"] = text.split("```json", 1)[0].replace(
                "# Near-Miss Candidate Stress Review", ""
            ).strip()
            if "```json" in text:
                out["recommendation"] = _json.loads(
                    text.split("```json", 1)[1].split("```", 1)[0]
                )
        except (OSError, ValueError):
            pass
    out["available"] = True
    return out


def compute_wds(w1_strike: Any, w1_volume: Any, w2_strike: Any, w2_volume: Any) -> dict[str, Any]:
    """True WDS for ONE wing from its W1 (10K wing) + adjacent W2 strike.
    WSR = W2_volume / W1_volume; WDS = 1 - WSR. ``source`` is 'unavailable' (never
    invents) when W1 strike/volume or W2 strike/volume is missing or W1 vol ≤ 0."""
    w1s, w1v = _f(w1_strike), _f(w1_volume)
    w2s, w2v = _f(w2_strike), _f(w2_volume)
    out = {
        "w1_strike": w1s, "w1_volume": w1v, "w2_strike": w2s, "w2_volume": w2v,
        "wsr": None, "wds": None, "wds_pct": "—", "wds_tier": None,
        "source": "unavailable", "reason": "",
    }
    if w1s is None or w1v is None or w1v <= 0:
        out["reason"] = "10K wing (W1) volume missing or zero — true WDS unavailable."
        return out
    if w2s is None or w2v is None:
        out["reason"] = ("10K wing exists, but true WDS is unavailable because the "
                         "adjacent W2 volume is missing from the current payload.")
        return out
    wsr = w2v / w1v
    wds = 1.0 - wsr
    tier = wds_tier(wds)
    pct_w2 = round(wsr * 100)
    out.update(wsr=round(wsr, 4), wds=round(wds, 4), wds_pct=wds_pct(wds),
               wds_tier=tier, source="true")
    if tier == 1:
        out["reason"] = f"10K wing is dominant because adjacent strike volume is only {pct_w2}% of W1."
    elif tier == 2:
        out["reason"] = f"10K wing is reasonably clean — adjacent strike volume is {pct_w2}% of W1."
    elif tier == 3:
        out["reason"] = f"10K wing is mixed (caution) — adjacent strike volume is {pct_w2}% of W1."
    else:
        out["reason"] = f"10K wing is weak because adjacent strike volume is {pct_w2}% of W1."
    return out


def wing_corridor_status(spot: Any, cw1: Any, pw1: Any) -> dict[str, Any]:
    """Phase 10A — Dan's wing CORRIDOR validity. The structure is ONLY active when
    the call floor (CW1) is below spot AND the put ceiling (PW1) is above spot:

        CW1 < spot < PW1

    A call floor at/above spot is NOT an active floor; a put ceiling at/below spot
    is NOT an active ceiling — either way the corridor is not formed. Returns
    {corridor_valid, cw1, pw1, spot, reason, side_read}. Pure; never raises."""
    sp, c, p = _f(spot), _f(cw1), _f(pw1)
    out = {"corridor_valid": False, "cw1": c, "pw1": p, "spot": sp,
           "reason": "", "side_read": ""}
    if c is None and p is None:
        out["reason"] = "missing CW1 and PW1 (no 10K wings)"
        return out
    if c is None:
        out["reason"] = "missing CW1 (call floor)"
        return out
    if p is None:
        out["reason"] = "missing PW1 (put ceiling)"
        return out
    if sp is None:
        out["reason"] = "spot unavailable"
        return out
    if c >= sp:
        out["reason"] = "CW1 is not below spot."
        out["side_read"] = (f"CALL_FLOOR 10K at {fmt_strike(c)} is above spot, "
                            "so it is not acting as the active floor")
        return out
    if p <= sp:
        out["reason"] = "PW1 is not above spot."
        out["side_read"] = (f"PUT_CEILING 10K at {fmt_strike(p)} is below spot, "
                            "so it is not acting as the active ceiling")
        return out
    out["corridor_valid"] = True
    out["reason"] = "spot is between CW1 and PW1"
    out["side_read"] = "spot is inside the active wing corridor"
    return out


def wing_dominance(exposures: Any, spot: Any = None) -> dict[str, Any]:
    """Operator Wing-Dominance read: per-side raw WDS (call/put), the **corridor
    status** (CW1 < spot < PW1), the ACTIVE dominant wing (only when the corridor
    is valid — never calls a call-floor-above-spot an active floor), the raw
    dominant wing (context, may be inactive), and the NEAREST wing (immediate
    breach risk). Pure; never invents WDS, never claims active structure when the
    corridor is not formed."""
    ex = exposures
    sp = _num_or_none(spot)
    call = compute_wds(getattr(ex, "call_floor_10k", None),
                       getattr(ex, "call_floor_10k_volume", None),
                       getattr(ex, "call_floor_10k_w2_strike", None),
                       getattr(ex, "call_floor_10k_w2_volume", None))
    put = compute_wds(getattr(ex, "put_ceiling_10k", None),
                      getattr(ex, "put_ceiling_10k_volume", None),
                      getattr(ex, "put_ceiling_10k_w2_strike", None),
                      getattr(ex, "put_ceiling_10k_w2_volume", None))

    cw1 = _f(getattr(ex, "call_floor_10k", None))    # primary call wing = call floor
    pw1 = _f(getattr(ex, "put_ceiling_10k", None))   # primary put wing = put ceiling
    corridor = wing_corridor_status(sp, cw1, pw1)
    corridor_valid = corridor["corridor_valid"]

    call_true, put_true = call["source"] == "true", put["source"] == "true"
    if call_true and put_true:
        raw_side = "CALL" if (call["wds"], call["w1_volume"]) >= (put["wds"], put["w1_volume"]) else "PUT"
    elif call_true:
        raw_side = "CALL"
    elif put_true:
        raw_side = "PUT"
    else:
        raw_side = "unavailable"
    raw_dom = call if raw_side == "CALL" else put if raw_side == "PUT" else None
    raw_label = ("CALL_FLOOR 10K" if raw_side == "CALL"
                 else "PUT_CEILING 10K" if raw_side == "PUT" else None)

    # ACTIVE dominant wing exists ONLY inside a valid corridor.
    wds_active = corridor_valid and raw_dom is not None
    active_dom = raw_dom if wds_active else None

    nearest = wing_stack(ex, spot).get("nearest_wing")
    near_dist = (abs(nearest["distance"]) if nearest and nearest.get("distance") is not None
                 else None)

    out: dict[str, Any] = {
        "call_w1_strike": call["w1_strike"], "call_w1_volume": call["w1_volume"],
        "call_w2_strike": call["w2_strike"], "call_w2_volume": call["w2_volume"],
        "call_wsr": call["wsr"], "call_wds": call["wds"], "call_wds_pct": call["wds_pct"],
        "call_wds_tier": call["wds_tier"],
        "put_w1_strike": put["w1_strike"], "put_w1_volume": put["w1_volume"],
        "put_w2_strike": put["w2_strike"], "put_w2_volume": put["w2_volume"],
        "put_wsr": put["wsr"], "put_wds": put["wds"], "put_wds_pct": put["wds_pct"],
        "put_wds_tier": put["wds_tier"],
        # ── corridor validity (Phase 10A) ──
        "corridor_valid": corridor_valid,
        "corridor_reason": corridor["reason"],
        "corridor_side_read": corridor["side_read"],
        "corridor_cw1": cw1, "corridor_pw1": pw1, "corridor_spot": sp,
        "wds_active": wds_active,
        # ── raw WDS dominance (context; may be INACTIVE if corridor invalid) ──
        "raw_wds_source": "true" if raw_dom is not None else "unavailable",
        "raw_dominant_side": raw_side,
        "raw_dominant_label": raw_label if raw_dom is not None else None,
        "raw_dominant_strike": raw_dom["w1_strike"] if raw_dom else None,
        "raw_dominant_wds": raw_dom["wds"] if raw_dom else None,
        "raw_dominant_wds_pct": raw_dom["wds_pct"] if raw_dom else "—",
        "raw_dominant_tier": raw_dom["wds_tier"] if raw_dom else None,
        # ── ACTIVE dominant wing (only inside a valid corridor) ──
        "dominant_wing_side": raw_side if wds_active else "unavailable",
        "dominant_wing_label": None, "dominant_wing_strike": None,
        "dominant_wing_volume": None, "dominant_wing_wds": None,
        "dominant_wing_wds_pct": "—", "dominant_wing_tier": None,
        "nearest_wing_label": nearest["label"] if nearest else None,
        "nearest_wing_strike": nearest["strike"] if nearest else None,
        "nearest_wing_distance_points": near_dist,
        "wds_source": "true" if active_dom is not None else "unavailable",
        "wds_reason": "",
    }
    if active_dom is not None:
        out.update(
            dominant_wing_label=raw_label, dominant_wing_strike=active_dom["w1_strike"],
            dominant_wing_volume=active_dom["w1_volume"], dominant_wing_wds=active_dom["wds"],
            dominant_wing_wds_pct=active_dom["wds_pct"], dominant_wing_tier=active_dom["wds_tier"],
            wds_reason=(f"Active corridor — dominant wing is {raw_label} at "
                        f"{fmt_strike(active_dom['w1_strike'])} with WDS {active_dom['wds_pct']} — "
                        f"{WDS_TIER_MEANING.get(active_dom['wds_tier'], '')} "
                        f"(Tier {active_dom['wds_tier']}). {active_dom['reason']}"),
        )
    elif raw_dom is not None:
        # a 10K WDS could be computed, but the corridor is NOT formed → raw only.
        _detail = corridor["side_read"] or corridor["reason"]
        out["wds_reason"] = (
            f"{_detail.rstrip('.')}. Raw WDS for {raw_label} at "
            f"{fmt_strike(raw_dom['w1_strike'])} is {raw_dom['wds_pct']} "
            "(raw context only — NOT active structure).")
    elif cw1 is not None or pw1 is not None:
        out["wds_reason"] = (call["reason"] if call["w1_strike"] is not None else put["reason"]) \
            or "10K wing exists, but true WDS is unavailable (adjacent W2 volume missing)."
    else:
        out["wds_reason"] = "No qualifying 10K wing in the current structure payload."
    return out


# ── Phase 10C follow-up — local backtest DATA availability (Task D) ───────────
# Pure local file read (no network) so the Backtests UI can show how far back data
# exists per symbol×DTE and drive the "All Data" date range.

def backtest_data_range(symbol: Any, dte: Any) -> dict[str, Any]:
    """{symbol, dte, available, file_count, min_date, max_date} for one symbol×DTE
    bucket ('0DTE' / '1DTE'). Reuses src.backtesting.raw_snapshot_loader (read-only,
    HOME/env paths). Degrades to available=False on any error / empty folder."""
    out: dict[str, Any] = {
        "symbol": str(symbol or "").strip().upper(), "dte": str(dte or "0DTE"),
        "available": False, "file_count": 0, "min_date": None, "max_date": None,
    }
    try:
        from src.backtesting import raw_snapshot_loader as _L
        dates = _L.available_dates(out["symbol"], out["dte"])
    except Exception:                                  # never break the cockpit
        dates = []
    if dates:
        out.update(available=True, file_count=len(dates),
                   min_date=dates[0], max_date=dates[-1])
    return out


def backtest_data_availability(symbol: Any) -> dict[str, dict[str, Any]]:
    """Both DTE buckets for a symbol: {'0DTE': {...}, '1DTE': {...}}."""
    return {d: backtest_data_range(symbol, d) for d in ("0DTE", "1DTE")}


def backtest_range_caption(rng: dict[str, Any]) -> str:
    """One-line availability caption: 'SPX 0DTE: 146 files · 2025-10-31 → 2026-06-04'
    or 'SPX 1DTE: no local data'."""
    sym = rng.get("symbol", "?")
    dte = rng.get("dte", "?")
    if not rng.get("available"):
        return f"{sym} {dte}: no local data"
    return (f"{sym} {dte}: {rng.get('file_count', 0)} files · "
            f"{rng.get('min_date')} → {rng.get('max_date')}")
