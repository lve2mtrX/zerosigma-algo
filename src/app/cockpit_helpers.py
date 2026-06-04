"""Phase 9D — pure cockpit helpers (formatting, spot fallback, provider defaults,
log export, review prompt).

Stdlib + read-only review/ledger modules only. ZERO ``import streamlit`` so every
helper is unit-testable. NOTHING here executes, places, or previews an order — UI
formatting + read-only log export only. Provider "configured" detection checks env
var PRESENCE (never reads or returns secret values).
"""

from __future__ import annotations

import os
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
                            chain_available: bool = True) -> dict[str, str]:
    """Translate structure into the 5-part operator summary. Pure; every part is
    guarded so missing data reads 'unavailable' rather than inventing context.

    `gamma` = output of `primary_secondary_gamma`; `wings` = output of `wing_stack`.
    """
    sp = _num_or_none(spot)
    regime = gamma_regime if isinstance(gamma_regime, str) and gamma_regime else None
    g_primary = gamma.get("primary")
    near = wings.get("nearest_wing")
    primary_wing = wings.get("primary_wing")

    # ── Structure Read ──
    parts: list[str] = []
    parts.append(f"Spot {fmt_price(sp)}." if sp is not None else "Spot unavailable.")
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
        if primary_wing and primary_wing is not near:
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
