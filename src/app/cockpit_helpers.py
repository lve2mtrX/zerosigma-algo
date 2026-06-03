"""Phase 9D — pure cockpit helpers (formatting, spot fallback, provider defaults,
log export, review prompt).

Stdlib + read-only review/ledger modules only. ZERO ``import streamlit`` so every
helper is unit-testable. NOTHING here executes, places, or previews an order — UI
formatting + read-only log export only. Provider "configured" detection checks env
var PRESENCE (never reads or returns secret values).
"""

from __future__ import annotations

import os
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
