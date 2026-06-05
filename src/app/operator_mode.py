"""Phase 9E — Operator Mode helpers (Simple/Advanced, simple→profile mappings,
symbol health, branded labels).

Pure: stdlib only, ZERO ``import streamlit`` and ZERO project imports, so every
helper is unit-testable. NOTHING here executes, places, or previews an order — UX
+ profile-field mapping only.

Visible branding uses "Zσ Strat Tester" (never "Forward Runner" as a tab name).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# ── Simple / Advanced mode ───────────────────────────────────────────────────

SIMPLE_MODE_HELP = (
    "Simple Mode gets you running. Advanced Mode exposes filters, exact DTE "
    "behavior, quote validation rules, and selector constraints."
)
DEFAULT_SIMPLE_MODE = True

DEFAULT_SYMBOL = "SPX"


# ── branded tab / section labels (the rename) ────────────────────────────────

STRAT_TESTER_TAB = "Zσ Strat Tester"
RUN_STRATEGY_TAB = "Run Strategy"   # Phase 10B — action-oriented tab label (Tester = subtitle)
PAPER_PORTFOLIO_TAB = "Paper Portfolio"
LIVE_COCKPIT_TAB = "Live Cockpit"
STRATEGY_BUILDER_TAB = "Zσ Strat Builder"   # Phase 9F rename
STATS_TAB = "Stats / Review"                 # Phase 9F rename (was Logs / Review)
BACKTESTS_TAB = "Backtests"                  # Phase 10C — discoverable local backtests
SETTINGS_TAB = "Settings"

# Phase 9F — branded header subtitle (no "forward runner" wording).
HEADER_TITLE = "ZerσSigma Algo Cockpit"
HEADER_SUBTITLE = "Scanner · Zσ Strat Builder · Zσ Strat Tester · Paper Portfolio · Strategy Stats"


def tab_labels() -> list[str]:
    """The seven cockpit tab labels. Uses the branded 'Zσ Strat Builder' / 'Run
    Strategy' / 'Stats / Review' / 'Backtests' — never 'Forward Runner', 'Logs /
    Review', or 'Runner' as a visible tab name."""
    return [
        f"🛰 {LIVE_COCKPIT_TAB}",
        f"🧱 {STRATEGY_BUILDER_TAB}",
        f"🧪 {RUN_STRATEGY_TAB}",
        f"💼 {PAPER_PORTFOLIO_TAB}",
        f"📈 {BACKTESTS_TAB}",
        f"📊 {STATS_TAB}",
        f"⚙ {SETTINGS_TAB}",
    ]


# ── side preference → profile fields ─────────────────────────────────────────

SIDE_PREFERENCES = ("Both sides", "Calls only", "Puts only", "Observe only")


def side_preference_to_fields(pref: str) -> dict[str, Any]:
    """Map a Simple-Mode side preference to the existing Phase 6 profile fields.

    Returns the allow_* flags (and, for the one-sided / observe presets, a default
    ``daily_selector``). 'Both sides' leaves the selector to the selector-style
    control. The builder lets an explicit selector style override the default."""
    if pref == "Calls only":
        return {"allow_call_credit": True, "allow_put_credit": False,
                "daily_selector": "call_credit_only"}
    if pref == "Puts only":
        return {"allow_call_credit": False, "allow_put_credit": True,
                "daily_selector": "put_credit_only"}
    if pref == "Observe only":
        return {"allow_call_credit": True, "allow_put_credit": True,
                "daily_selector": "no_trade"}
    # Both sides (default)
    return {"allow_call_credit": True, "allow_put_credit": True}


# ── selector style → daily_selector ──────────────────────────────────────────

SELECTOR_STYLES = (
    "Dynamic — balanced both sides", "Best score", "Best credit",
    "Conservative / lowest breach risk", "No trade / observe only",
)

_SELECTOR_STYLE_MAP = {
    "Dynamic — balanced both sides": "balanced_structure_premium_valid",  # Phase 9G
    "Best score": "score_best_valid",
    "Best credit": "best_credit_valid",
    "Conservative / lowest breach risk": "lowest_breach_risk_valid",
    "No trade / observe only": "no_trade",
}


def selector_style_to_selector(style: str) -> str:
    """Map a Simple-Mode selector style to an existing daily_selector mode."""
    return _SELECTOR_STYLE_MAP.get(style, "score_best_valid")


def selector_to_style(daily_selector: str) -> str:
    """Reverse map (for seeding the Simple-Mode control from a loaded profile)."""
    for style, mode in _SELECTOR_STYLE_MAP.items():
        if mode == daily_selector:
            return style
    return "Best score"


def build_simple_fields(*, side_preference: str, selector_style: str) -> dict[str, Any]:
    """Combine side preference + selector style into profile fields.

    The explicit selector style overrides the side preference's default selector,
    EXCEPT 'Observe only' which forces ``daily_selector='no_trade'`` regardless."""
    fields = side_preference_to_fields(side_preference)
    if side_preference != "Observe only":
        fields["daily_selector"] = selector_style_to_selector(selector_style)
    return fields


# ── data source → providers ──────────────────────────────────────────────────

# Conceptual split (Phase 9E clarification):
#   ZerσSigma API = EXPOSURE/structure engine ONLY (DA-GEX/VEX/DEX/CEX/TEX, gamma
#       regime, walls/floors, MaxVol/DDOI, exposure context).
#   Tastytrade    = MARKET-DATA / tradable-instrument engine (quotes, option chain,
#       bid/ask/mid/mark, volume, open interest, contract metadata).
DATA_SOURCE_LIVE = "Live: ZerσSigma exposures + Tasty market data"
DATA_SOURCE_SANDBOX = "Sandbox: Stub exposures + Mock market data"
DATA_SOURCES = (DATA_SOURCE_LIVE, DATA_SOURCE_SANDBOX)

# Prominent-copy display aliases (internal provider names + CLI flags UNCHANGED).
EXPOSURE_SOURCE_LABEL = "Exposure source"        # was "structure provider"
MARKET_DATA_SOURCE_LABEL = "Market data source"  # was "quote provider"


def exposure_engine_label(name: str) -> str:
    """Friendly label for an exposure (structure) provider in prominent UI copy."""
    return {"zerosigma_api": "ZerσSigma exposures (live)",
            "stub": "Stub exposures (sandbox)"}.get(name, name)


def market_data_engine_label(name: str) -> str:
    """Friendly label for a market-data (quote) provider in prominent UI copy."""
    return {"tastytrade": "Tasty market data (live)",
            "mock": "Mock market data (sandbox)",
            "null": "Manual marks"}.get(name, name)


def data_source_to_providers(label: str) -> dict[str, str]:
    """Map a Simple-Mode data source to structure + quote providers."""
    if label == DATA_SOURCE_SANDBOX:
        return {"structure_provider": "stub", "quote_provider": "mock"}
    return {"structure_provider": "zerosigma_api", "quote_provider": "tastytrade"}


def providers_to_data_source(structure_provider: str, quote_provider: str) -> str:
    """Reverse map (for defaulting the Simple-Mode radio)."""
    if structure_provider == "stub" or quote_provider in ("mock", "null"):
        return DATA_SOURCE_SANDBOX
    return DATA_SOURCE_LIVE


# ── symbol normalization + health ────────────────────────────────────────────

def normalize_symbol(raw: Any, default: str = DEFAULT_SYMBOL) -> str:
    """Uppercase + strip a user-typed symbol; blank → default. Arbitrary symbols
    are accepted (availability is reported separately by symbol_health)."""
    if raw is None:
        return default
    s = str(raw).strip().upper()
    return s or default


def exposures_unavailable_warning(symbol: str) -> str:
    """ZerσSigma EXPOSURE engine has no coverage for this symbol (Tasty market
    data may still work)."""
    return (f"ZerσSigma exposures unavailable for {symbol}. Tasty market data may "
            "still work — try Sandbox mode or a symbol with ZerσSigma coverage. "
            "Not every ticker has ZerσSigma exposure support.")


def market_data_unavailable_warning(symbol: str) -> str:
    """Tasty MARKET-DATA engine has no quote chain for this symbol right now."""
    return (f"Tasty market data unavailable for {symbol}. The market may be closed, "
            "quotes stale, or the symbol unsupported by the market-data engine. "
            "Try Sandbox mode or check during RTH.")


def symbol_health(*, symbol: str, accepted: bool = True,
                  market_data_available: bool, exposures_available: bool) -> dict[str, Any]:
    """Compact symbol-health summary for the UI.

    Distinguishes FOUR things: the symbol is accepted; **Tasty market data**
    (quotes/chain/volume/OI) is available; **ZerσSigma exposures**
    (DA-GEX/walls/floors/regime) are available; and overall strategy eligibility
    (needs BOTH market data + exposures). Arbitrary tickers are accepted — Tasty
    may serve quotes even when ZerσSigma exposure coverage is missing."""
    eligible = bool(accepted and market_data_available and exposures_available)
    reason = ""
    if not eligible:
        if not accepted:
            reason = f"{symbol} was not accepted."
        elif not exposures_available and not market_data_available:
            reason = f"No ZerσSigma exposures and no Tasty market data for {symbol}."
        elif not exposures_available:
            reason = exposures_unavailable_warning(symbol)
        elif not market_data_available:
            reason = market_data_unavailable_warning(symbol)
    return {
        "symbol": symbol,
        "accepted": bool(accepted),
        "market_data_available": bool(market_data_available),
        "exposures_available": bool(exposures_available),
        "eligible": eligible,
        "reason": reason,
    }


# ── friendly log-export labels ───────────────────────────────────────────────

LOG_EXPORT_LABELS = {
    "tick_log.jsonl": "Strategy test log",
    "signal_log.jsonl": "Selected trades export",
    "no_trade_log.jsonl": "No-trade reasons export",
    "paper_trade_events.jsonl": "Paper trade events",
    "portfolio_summary.json": "Portfolio summary",
    "reconciliation_report.json": "Reconciliation report",
    "eod_summary.json": "EOD summary",
}


def friendly_log_label(filename: str) -> str:
    """Operator-friendly label for an export file (filename shown under Advanced)."""
    return LOG_EXPORT_LABELS.get(filename, filename)


# ── Phase 9F — button labels (verb-first, consistent) ────────────────────────

BTN_PREVIEW = "👁 Preview Strategy"
BTN_START_TEST = "▶ Start Paper Test"
BTN_STOP_TEST = "■ Stop Test"
BTN_REVIEW = "📄 Review Latest"
BTN_CLEAR_STALE = "🧹 Clear stale test"
BTN_REFRESH = "🔄 Refresh status"
BTN_RECORD_MANUAL = "Record manual paper trade"
BTN_APPLY_SESSION = "Apply local session settings"
BTN_NEW_PROFILE = "Create new profile"
BTN_EDIT_PROFILE = "Edit selected profile"
BTN_CLONE_PROFILE = "Clone selected profile"
BTN_LOAD_PROFILE = "Load selected profile"
BTN_SAVE_PROFILE = "💾 Save profile"
BTN_VALIDATE = "Check Strategy Setup"
BTN_FORCE_STOP = "⏹ Force stop local test process"


def button_labels() -> dict[str, str]:
    """All operator button labels (pure — for tests + reuse)."""
    return {
        "preview": BTN_PREVIEW, "start": BTN_START_TEST, "stop": BTN_STOP_TEST,
        "review": BTN_REVIEW, "clear_stale": BTN_CLEAR_STALE, "refresh": BTN_REFRESH,
        "record_manual": BTN_RECORD_MANUAL, "apply_session": BTN_APPLY_SESSION,
        "new": BTN_NEW_PROFILE, "edit": BTN_EDIT_PROFILE, "clone": BTN_CLONE_PROFILE,
        "load": BTN_LOAD_PROFILE, "save": BTN_SAVE_PROFILE, "validate": BTN_VALIDATE,
    }


def active_profile_display(profile_id: Any) -> str:
    """Display string for the active profile; clear when none."""
    pid = str(profile_id).strip() if profile_id is not None else ""
    if not pid or pid in ("(none)", "None"):
        return "No active profile selected"
    return pid


def runner_busy_message(profile_id: Any, status: Any) -> str:
    """Warning shown when a runner is already active/stopping."""
    who = active_profile_display(profile_id)
    state = str(status or "running")
    return (f"A local paper test is already {state} for {who}. "
            "Stop it before starting another.")


# ── Phase 9G — friendly "Latest test run" label (full run_id kept for Advanced) ─

def strategy_display_name(strategy_id: Any) -> str:
    """'vertical_wing_v1' → 'Vertical Wing'. Strips a trailing _vN and title-cases."""
    s = str(strategy_id or "").strip()
    known = {"vertical_wing_v1": "Vertical Wing"}
    if s in known:
        return known[s]
    s2 = re.sub(r"_v\d+$", "", s)
    return s2.replace("_", " ").title() if s2 else "Strategy"


def short_run_id(run_id: Any) -> str:
    """Compact a long run_id for display ('abcd1234…  ef90'); full id stays in
    Advanced details. Short ids are returned unchanged."""
    rid = str(run_id or "").strip()
    if len(rid) <= 16:
        return rid
    return f"{rid[:8]}…{rid[-4:]}"


def _fmt_started_at(started_at: Any) -> str:
    """Parse an ISO-ish timestamp → 'Jun 2 · 10:31 PM'. Empty/invalid → ''. Pure:
    parses a GIVEN timestamp, never reads the current clock (deterministic)."""
    s = str(started_at or "").strip().replace("Z", "+00:00")
    if not s:
        return ""
    dt: datetime | None = None
    for cand in (s, s[:19]):
        try:
            dt = datetime.fromisoformat(cand)
            break
        except ValueError:
            continue
    if dt is None:
        return ""
    hour12 = dt.strftime("%I").lstrip("0") or "12"
    return f"{dt.strftime('%b')} {dt.day} · {hour12}:{dt.strftime('%M %p')}"


def friendly_run_label(*, run_id: Any = None, profile_name: Any = None,
                       strategy_id: Any = None, started_at: Any = None) -> str:
    """Operator-friendly 'Latest test run' label, e.g. 'Vertical Wing · Jun 2 ·
    10:31 PM'. Prefers profile_name, then the strategy display name; appends the
    parsed start time when available. Falls back to a short run_id, then a clear
    'No test run yet'. The full run_id is shown separately under Advanced."""
    parts: list[str] = []
    name = str(profile_name).strip() if profile_name else ""
    if not name and strategy_id:
        name = strategy_display_name(strategy_id)
    if name:
        parts.append(name)
    when = _fmt_started_at(started_at)
    if when:
        parts.append(when)
    if parts:
        return " · ".join(parts)
    rid = short_run_id(run_id)
    return rid or "No test run yet"


def running_display(active: Any) -> str:
    """'Active: True/False' → operator 'Running: Yes/No'."""
    return "Yes" if bool(active) else "No"


# ── Phase 10B UI hotfix — trader-first status labels (raw IDs → short copy) ───
# Pure 1–2 word converters for the read-only status cards so Simple Mode never
# shows clipped raw enums (TRADE_CALL_CREDIT / chain_returned_validation_failed /
# vertical_wing_v1 / zerosigma_api). Raw values stay available in Advanced.

def provider_short(name: Any) -> str:
    """Compact provider label for status cards: zerosigma_api→'Zσ API',
    tastytrade→'Tasty', mock→'Mock', stub→'Stub', null→'Manual'."""
    n = str(name or "").strip().lower()
    return {"zerosigma_api": "Zσ API", "tastytrade": "Tasty", "mock": "Mock",
            "stub": "Stub", "null": "Manual"}.get(n, str(name) if name else "—")


def decision_label(decision: Any) -> str:
    """Raw decision / side → trader copy. TRADE_CALL_CREDIT / CALL_CREDIT →
    'Call Credit'; TRADE_PUT_CREDIT / PUT_CREDIT → 'Put Credit'; NO_TRADE →
    'No Trade'. Anything else passes through (or '—' when empty)."""
    d = str(decision or "").strip().upper()
    known = {
        "TRADE_CALL_CREDIT": "Call Credit", "CALL_CREDIT": "Call Credit",
        "TRADE_PUT_CREDIT": "Put Credit", "PUT_CREDIT": "Put Credit",
        "NO_TRADE": "No Trade",
    }
    if d in known:
        return known[d]
    return str(decision) if decision not in (None, "", "—") else "—"


def side_label(side: Any) -> str:
    """CALL_CREDIT → 'Call Credit', PUT_CREDIT → 'Put Credit' (alias of decision_label)."""
    return decision_label(side)


def runner_state_label(status: Any) -> str:
    """Runner state → Title Case: stopped→'Stopped', running→'Running',
    stopping→'Stopping', starting→'Starting', stale→'Stale', error→'Error',
    completed→'Completed'."""
    s = str(status or "stopped").strip().lower()
    known = {"stopped": "Stopped", "running": "Running", "stopping": "Stopping",
             "starting": "Starting", "stale": "Stale", "error": "Error",
             "completed": "Completed"}
    return known.get(s, s.title() if s else "Stopped")


_QUOTE_STATE_SHORT = {
    "chain_returned_usable": "Available",
    "chain_unavailable": "No Chain",
    "mock": "Sandbox",
    "not_configured": "Not Configured",
    "auth_failed": "Auth Failed",
    "root_unresolved": "No Root",
    "expiration_unavailable": "No Expiry",
    "unknown_error": "Unknown",
}


def quote_state_label(state: Any, top_blocker: Any = None) -> str:
    """Short 1–2 word card label for a cockpit_quote_status state. The
    validation-blocked state splits on the blocker: stale → 'Stale', else →
    'Validation Blocked'. Keeps the long cockpit_quote_status['label'] untouched."""
    s = str(state or "").strip()
    if s == "chain_returned_validation_failed":
        return "Stale" if str(top_blocker or "").lower() == "stale" else "Validation Blocked"
    return _QUOTE_STATE_SHORT.get(s, "Unknown")


def quote_state_banner(state: Any, symbol: Any, top_blocker: Any = None) -> str | None:
    """Trader-facing banner for a quote state. None when quotes are usable/sandbox."""
    s = str(state or "").strip()
    sym = str(symbol or "—")
    if s == "chain_returned_validation_failed":
        if str(top_blocker or "").lower() == "stale":
            return ("Tasty chain returned, but quotes are stale. Structure preview only — "
                    "live eligibility will re-check during RTH.")
        return ("Tasty chain returned, but quote validation blocked usable candidates. "
                f"Reason: {top_blocker or 'validation'}.")
    if s in ("chain_returned_usable", "mock"):
        return None
    if s == "not_configured":
        return f"Tasty is not configured for {sym}. Add TASTY_* OAuth credentials to .env."
    if s == "auth_failed":
        return f"Tasty auth failed / session invalid for {sym}."
    if s == "root_unresolved":
        return f"Tasty could not resolve the option root for {sym}."
    if s == "expiration_unavailable":
        return f"Tasty has no matching expiration for {sym} right now."
    return (f"Tasty market data unavailable for {sym}. The market may be closed or the "
            "symbol unsupported by the market-data engine. Try during RTH or use Sandbox.")


def stale_quote_mode_banner(symbol: Any) -> str:
    """After-hours / stale-quote sub-text (shown only when stale is the top blocker)."""
    return (f"Structure is live from Zσ, but Tasty quotes for {symbol} are stale. Candidate "
            "pricing is preview-only until fresh RTH quotes arrive.")


def _strike_short(v: Any) -> str:
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return "—"


def candidate_label(side: Any, short_strike: Any, long_strike: Any) -> str:
    """'Put Credit 7550/7545' from side + strikes (drops trailing .0)."""
    return f"{decision_label(side)} {_strike_short(short_strike)}/{_strike_short(long_strike)}"


def candidate_status_label(*, rejected: Any = False, risk_rejection_type: Any = None,
                           quote_state: Any = None, top_blocker: Any = None,
                           eligible_base: Any = None, preset_kind: Any = None) -> str:
    """Trader status pill for a candidate (display only — never changes selection):
    'Observe only' / 'Blocked: stale quotes' / 'Blocked: quote validation' /
    'Blocked: risk cap' / 'Blocked: filters' / 'Eligible'."""
    if str(preset_kind or "").lower() == "observe":
        return "Observe only"
    if str(quote_state or "") == "chain_returned_validation_failed":
        return ("Blocked: stale quotes" if str(top_blocker or "").lower() == "stale"
                else "Blocked: quote validation")
    if risk_rejection_type:
        return "Blocked: risk cap"
    if rejected:
        return "Blocked: filters"
    if eligible_base is False:
        return "Blocked: not eligible"
    return "Eligible"


def header_status_cells(*, strategy: Any, structure: Any, quotes: Any, runner: Any,
                        last_signal: Any, paper_pnl: Any,
                        safety: str = "No Broker") -> list[tuple[str, str]]:
    """Friendly (label, value) pairs for the READ-ONLY header status summary.
    All values must already be short/friendly (use the converters above)."""
    return [
        ("Strategy", str(strategy) if strategy else "—"),
        ("Structure", str(structure) if structure else "—"),
        ("Quotes", str(quotes) if quotes else "—"),
        ("Test Status", str(runner) if runner else "Stopped"),
        ("Last Signal", str(last_signal) if last_signal else "—"),
        ("Paper P&L", str(paper_pnl) if paper_pnl else "$0.00"),
        ("Safety", safety),
    ]


# ── Phase 10C — after-hours DTE preview + trader-facing candidate/test labels ─
# Pure helpers (stdlib only) so Simple Mode never shows raw enums and the
# after-hours quote-roll logic is unit-testable without Streamlit.

PREVIEW_MODE_LIVE = "live_preview"
_AFTER_HOURS_START_HOUR = 17   # 5:00 PM ET — 0DTE quotes are dead after the close


def dte_label(dte: Any) -> str:
    """0 → '0DTE', 1 → '1DTE', None/garbage → '—'."""
    try:
        return f"{int(dte)}DTE"
    except (TypeError, ValueError):
        return "—"


def _et_hour(now_et: Any) -> int | None:
    """Hour-of-day (0–23) from a datetime-like; None if unavailable."""
    try:
        return int(now_et.hour)
    except (TypeError, AttributeError, ValueError):
        return None


def resolve_preview_dte(now_et: Any, profile_target_dte: Any,
                        mode: str = PREVIEW_MODE_LIVE) -> int:
    """The DTE to PREVIEW (display only) for the Live Cockpit / Run-Strategy preview.

    During RTH (and any non-live-preview mode) this is the profile's own target
    DTE. After 17:00 ET and before midnight ET, a 0DTE profile previews the 1DTE
    chain because 0DTE quotes are stale/dead after the cash close. This NEVER
    changes the profile, paper-test, or backtest DTE — preview-only, by design."""
    try:
        base = int(profile_target_dte)
    except (TypeError, ValueError):
        base = 0
    if str(mode) != PREVIEW_MODE_LIVE or base != 0:
        return base
    hour = _et_hour(now_et)
    if hour is None:
        return base
    return 1 if _AFTER_HOURS_START_HOUR <= hour <= 23 else base


def after_hours_preview_active(now_et: Any, profile_target_dte: Any,
                               mode: str = PREVIEW_MODE_LIVE) -> bool:
    """True when the preview DTE has rolled away from the profile DTE (after-hours)."""
    try:
        base = int(profile_target_dte)
    except (TypeError, ValueError):
        base = 0
    return resolve_preview_dte(now_et, base, mode) != base


def after_hours_preview_banner(symbol: Any, profile_target_dte: Any = 0) -> str:
    """Trader banner shown when the preview rolls 0DTE → 1DTE after the close. States
    explicitly that ONLY the preview quote chain rolls — the strategy profile DTE is
    unchanged (no silent mutation of profile / paper-test / backtest DTE)."""
    return (f"Quote chain: 1DTE after-hours preview — 0DTE quotes for {symbol or '—'} are "
            "stale after the 5:00 PM ET close, so the live preview uses the 1DTE chain. "
            f"Profile DTE: {dte_label(profile_target_dte)}. Strategy DTE unchanged.")


def after_hours_quote_detail(active: Any, preview_dte: Any = 1) -> str | None:
    """Quotes-card sub-label: '1DTE quote chain · after-hours preview' when the
    after-hours roll is active; None during RTH (no extra label)."""
    if not active:
        return None
    return f"{dte_label(preview_dte)} quote chain · after-hours preview"


# ── candidate card labels (Simple Mode: friendly only; raw fields → Advanced) ──

def anchor_label(anchor_source: Any) -> str:
    """'put_ceiling_2k' → 'Put Ceiling 2K'; 'call_floor_5k' → 'Call Floor 5K'."""
    s = str(anchor_source or "").strip().lower()
    if not s:
        return "—"
    words = []
    for p in s.split("_"):
        words.append(p.upper() if re.fullmatch(r"\d+k", p) else p.capitalize())
    return " ".join(words)


def candidate_quote_status_label(short_leg: Any = None, long_leg: Any = None, *,
                                 quote_state: Any = None, top_blocker: Any = None) -> str:
    """Per-candidate quote status for Simple Mode: 'Available' / 'Stale' /
    'Validation Blocked' / '—'. Prefers per-leg validation, falling back to the
    cockpit quote state."""
    sp = short_leg.get("validation_passed") if isinstance(short_leg, dict) else None
    lp = long_leg.get("validation_passed") if isinstance(long_leg, dict) else None
    if sp is True and lp is True:
        return "Available"
    if sp is False or lp is False:
        return "Stale" if str(top_blocker or "").lower() == "stale" else "Validation Blocked"
    if quote_state:
        return quote_state_label(quote_state, top_blocker)
    return "—"


def candidate_risk_status_label(risk_rejection_type: Any = None) -> str:
    """'OK' when no risk cap tripped, else 'Blocked: risk cap'."""
    return "Blocked: risk cap" if risk_rejection_type else "OK"


def candidate_blocker_label(*, rejected: Any = False, risk_rejection_type: Any = None,
                            quote_state: Any = None, top_blocker: Any = None,
                            eligible_base: Any = None, preset_kind: Any = None) -> str:
    """Just the BLOCKER reason for a candidate ('stale quotes' / 'risk cap' /
    'quote validation' / 'filters' / 'not eligible' / '—'). '—' when eligible or
    observe-only. Mirrors candidate_status_label but strips the 'Blocked:' verb."""
    status = candidate_status_label(
        rejected=rejected, risk_rejection_type=risk_rejection_type,
        quote_state=quote_state, top_blocker=top_blocker,
        eligible_base=eligible_base, preset_kind=preset_kind)
    return status[len("Blocked: "):] if status.startswith("Blocked: ") else "—"


# ── Phase 10C follow-up — stale-quote decision gating (never fake a live read) ─

# Reason shown when Start Paper Test is disabled because LIVE quotes are stale.
START_TEST_STALE_REASON = ("Cannot start live paper test: quotes are stale. Try again "
                           "during RTH or use Sandbox.")


def decision_headline(*, available: Any, quote_state: Any = None,
                      top_blocker: Any = None) -> dict[str, Any]:
    """Whether the cockpit may show a LIVE 'Decision' or only a PREVIEW candidate.

    Returns {live, title, note}. When quotes are usable → a live Decision with a
    'cleared the gates' note. When quotes are stale / validation-blocked /
    unavailable → a preview-only headline + a plain 'Why not' line that never
    claims the candidate cleared selector/quote/risk eligibility."""
    if available:
        return {"live": True, "title": "Decision",
                "note": "Why: this side cleared selector, quote, and risk gates."}
    s = str(quote_state or "")
    tb = str(top_blocker or "").lower()
    if s == "chain_returned_validation_failed" and tb == "stale":
        return {"live": False, "title": "No Live Decision — Quotes Stale",
                "note": ("Why not: quote validation failed because quotes are stale. This "
                         "candidate is preview-only until fresh RTH quotes arrive.")}
    if s == "chain_returned_validation_failed":
        return {"live": False, "title": "No Live Decision — Quotes Blocked",
                "note": f"Why not: quote validation failed: {top_blocker or 'validation'}."}
    return {"live": False, "title": "No Live Decision — Quotes Unavailable",
            "note": "Why not: no usable quote chain right now — structure preview only."}


# ── Test-status wording (Task B: 'Runner' is never user-facing in Simple Mode) ─

def test_status_label(status: Any) -> str:
    """Friendly paper-test status (alias of runner_state_label) for the 'Test
    Status' card — 'stopped' → 'Stopped', etc."""
    return runner_state_label(status)


def humanize_runner_message(message: Any) -> str:
    """Replace developer 'runner' wording in a control message with 'paper test'."""
    s = str(message or "")
    for a, b in (("a runner", "a paper test"), ("Runner", "Paper test"),
                 ("runner", "paper test")):
        s = s.replace(a, b)
    return s


# ── Backtest discoverability (Task G — local snapshots only, never live/broker) ─

BACKTEST_SYMBOLS = ("SPX", "SPY", "QQQ")
BACKTEST_NOTE = ("Uses local saved snapshots only. No live API calls. No broker "
                 "execution. No order preview.")


def backtest_command(symbol: Any = "SPX", profile: Any = "all-main",
                     latest_days: Any = 20, dte: Any = 0,
                     run_label: Any = "smoke") -> str:
    """The exact read-only CLI to run a local backtest (NOT executed from the UI)."""
    sym = normalize_symbol(symbol)
    prof = str(profile or "all-main").strip() or "all-main"
    try:
        days = int(latest_days)
    except (TypeError, ValueError):
        days = 20
    try:
        d = int(dte)
    except (TypeError, ValueError):
        d = 0
    label = str(run_label or "smoke").strip() or "smoke"
    return (f"python -m scripts.backtest_run --symbol {sym} --profile {prof} "
            f"--latest-days {days} --dte {d} --run-label {label}")


def backtest_default_label(symbol: Any = "SPX", profile: Any = "all-main",
                           mode: Any = "latest") -> str:
    """A filesystem-safe default run label from symbol/profile/date-mode, e.g.
    'spx_all_main_latest'. Used to pre-fill the Backtests 'Run label' field."""
    sym = normalize_symbol(symbol).lower()
    prof = "".join(ch_ if ch_.isalnum() else "_" for ch_ in str(profile or "run").lower())
    m = {"Latest N days": "latest", "Date range": "range", "All data": "all"}.get(
        str(mode), str(mode or "run").lower())
    return f"{sym}_{prof}_{m}".strip("_")


# ── Phase 9H/9I — profile grouping by purpose + run/selection mismatch ───────

# Phase 9I — trader-friendly category labels (was: Primary live paper tests /
# Controls / Research-Observe / Legacy). Main first (Simple Mode default).
MAIN_CATEGORY = "Main Strategies"
PROFILE_CATEGORIES: tuple[str, ...] = (
    "Main Strategies", "Comparison Tests", "Research / Disabled", "Custom",
)
DEFAULT_SIMPLE_CATEGORY = MAIN_CATEGORY


def profile_category(preset_kind: Any) -> str:
    """Map a preset_kind to its operator category. Phase 10C follow-up — a saved
    profile with NO preset_kind (e.g. one Dan just built in Zσ Strat Builder) is
    classified as 'Custom' (reachable via 'Show all saved profiles'), never hidden
    forever under a 'Legacy' label."""
    k = str(preset_kind or "").strip().lower()
    if k == "dynamic":
        return "Main Strategies"
    if k == "control":
        return "Comparison Tests"
    if k in ("regime", "observe"):
        return "Research / Disabled"
    return "Custom"


def group_profiles_by_category(
    summaries: list[dict[str, Any]],
) -> list[tuple[str, list[str]]]:
    """Group profile summaries (each with profile_id + preset_kind) into ordered
    (category, [profile_id...]) buckets. Within a bucket, ids use the dynamic-
    first dropdown order. Empty categories are omitted."""
    buckets: dict[str, list[str]] = {c: [] for c in PROFILE_CATEGORIES}
    for s in summaries:
        pid = s.get("profile_id")
        if not pid:
            continue
        buckets[profile_category(s.get("preset_kind"))].append(pid)
    out: list[tuple[str, list[str]]] = []
    for cat in PROFILE_CATEGORIES:
        ids = order_profiles_for_dropdown(buckets[cat])
        if ids:
            out.append((cat, ids))
    return out


def profiles_in_category(summaries: list[dict[str, Any]], category: str) -> list[str]:
    """Ordered profile ids in one category (dynamic-first)."""
    for cat, ids in group_profiles_by_category(summaries):
        if cat == category:
            return ids
    return []


def run_profile_mismatch(selected_id: Any, latest_run_profile_id: Any) -> dict[str, Any]:
    """Compare the SELECTED profile against the profile that produced the LATEST
    completed run. Returns {mismatch, message}. When they differ, the operator is
    warned that stale results do NOT belong to the selected profile."""
    sel = str(selected_id).strip() if selected_id else ""
    latest = str(latest_run_profile_id).strip() if latest_run_profile_id else ""
    if sel and latest and sel != latest:
        return {
            "mismatch": True,
            "message": (
                f"Latest run is from a different profile (`{latest}`), not the selected "
                f"`{sel}`. Start a new local paper test to generate results for the "
                "selected profile."
            ),
        }
    return {"mismatch": False, "message": None}


def simple_mode_profile_ids(summaries: list[dict[str, Any]], *,
                            show_all: bool = False) -> list[str]:
    """Simple-Mode dropdown ids: ONLY Main Strategies by default (hides legacy +
    comparison + research); the full ordered list when ``show_all`` (the operator
    ticked 'Show comparison and legacy profiles').

    Phase 10C — the per-profile ``enabled`` flag ('Show in main strategy list')
    now CURATES the default Main list: if any Main profile is enabled, only those
    show. If NONE are enabled (the current all-disabled default), the full Main
    list shows — so the list is never empty and the flag stays opt-in/safe."""
    if show_all:
        all_ids = [s.get("profile_id") for s in summaries if s.get("profile_id")]
        return order_profiles_for_dropdown(all_ids)
    main = profiles_in_category(summaries, MAIN_CATEGORY)
    enabled_ids = {s.get("profile_id") for s in summaries if s.get("enabled")}
    curated = [pid for pid in main if pid in enabled_ids]
    return curated or main


# ── Phase 9I — app-vs-profile data-source resolution (never silently mismatch) ─

RUN_SOURCE_APP = "Use app data source for this run"
RUN_SOURCE_PROFILE = "Use the profile's own data source"
RUN_SOURCE_MODES = (RUN_SOURCE_APP, RUN_SOURCE_PROFILE)


def data_source_short(label: Any) -> str:
    """'Live: …' / 'Sandbox: …' → 'Live' / 'Sandbox' (else the part before ':')."""
    s = str(label or "")
    low = s.lower()
    if low.startswith("live"):
        return "Live"
    if low.startswith("sandbox"):
        return "Sandbox"
    return s.split(":")[0].strip() if s else "—"


def resolve_run_source(app_data_source: Any, profile_structure: Any,
                       profile_quote: Any, *, prefer: str = RUN_SOURCE_APP) -> dict[str, Any]:
    """Deterministically resolve which data source a preview/test run will use.

    ``app_data_source`` = the top-controls Live/Sandbox choice (a DATA_SOURCES
    value). ``profile_structure``/``profile_quote`` = the selected profile's
    providers. ``prefer`` = RUN_SOURCE_APP (default — app controls win) or
    RUN_SOURCE_PROFILE. Returns the resolved providers + a mismatch flag + a clear
    message. NEVER silently mismatches: on conflict the message explains which won."""
    app_providers = data_source_to_providers(app_data_source)
    app_src = data_source_short(app_data_source)
    profile_src = data_source_short(
        providers_to_data_source(profile_structure or "stub", profile_quote or "mock"))
    mismatch = app_src != profile_src
    use_app = prefer != RUN_SOURCE_PROFILE

    if use_app:
        structure_provider = app_providers["structure_provider"]
        quote_provider = app_providers["quote_provider"]
        resolved = app_src
        winner = "app"
    else:
        structure_provider = profile_structure or "stub"
        quote_provider = profile_quote or "mock"
        resolved = profile_src
        winner = "profile"

    message = None
    if mismatch:
        message = (
            f"Selected profile is configured for {profile_src}, but app controls are "
            f"{app_src}. This run will use the {resolved} source "
            f"({'app controls' if use_app else 'profile'} win). "
            "Choose which source should win before starting a test."
        )
    return {
        "mismatch": mismatch,
        "winner": winner,
        "data_source": resolved,                 # "Live" | "Sandbox"
        "app_source": app_src,
        "profile_source": profile_src,
        "structure_provider": structure_provider,
        "quote_provider": quote_provider,
        "exposure_label": exposure_engine_label(structure_provider),
        "market_data_label": market_data_engine_label(quote_provider),
        "message": message,
    }


def run_source_status(*, chain_available: bool, mismatch: bool) -> str:
    """Tester readiness badge: 'unavailable' (no quote chain to price candidates),
    'warning' (app/profile source mismatch to resolve), else 'ready'."""
    if not chain_available:
        return "unavailable"
    if mismatch:
        return "warning"
    return "ready"


# ── Phase 9F — preset strategy descriptions ──────────────────────────────────

PRESET_DESCRIPTIONS: dict[str, str] = {
    "vertical_wing_score_best_1dte": (
        "Baseline Vertical Wing profile. Scans both call-credit and put-credit "
        "candidates, then selects the highest-scoring valid setup for 1DTE testing."),
    "vertical_wing_best_credit_1dte": (
        "Credit-focused Vertical Wing profile. Prioritizes the valid candidate with "
        "the best credit after filters."),
    "vertical_wing_call_only_1dte": (
        "Call-credit-only Vertical Wing profile. Tests the call-credit side without "
        "allowing put-credit selections."),
    "vertical_wing_no_trade": (
        "Observation-only profile. Runs the scan and logs candidate data without "
        "selecting a trade."),
    # ── Phase 9G dynamic-first preset stack ──
    "morning_5k_dynamic_tp75": (
        "PRIMARY dynamic preset. Each tick it evaluates BOTH call-credit and "
        "put-credit and picks the better side by the balanced structure/premium/"
        "distance score — never blindly highest premium. Morning entry, 5K bucket, "
        "SL 150% of credit, take 75% profit."),
    "morning_2k_dynamic_no_tp": (
        "PRIMARY dynamic preset. Picks the better side each tick by the balanced "
        "score. Morning entry, 2K bucket, SL 150% of credit, no take-profit."),
    "eod_5k_dynamic_sl150_no_tp": (
        "PRIMARY dynamic preset. Picks the better side by the balanced score at the "
        "end-of-day window (target 15:15 ET). 5K bucket, SL 150% of credit, no "
        "take-profit."),
    "eod_5k_dynamic_sl200_no_tp": (
        "PRIMARY dynamic preset. Same EOD dynamic logic with a wider SL 200% of "
        "credit. 5K bucket, no take-profit."),
    "morning_5k_call_tp75_control": (
        "CONTROL (call-only). Always selects a CALL_CREDIT — the call-only mirror of "
        "the morning 5K dynamic preset, so you can measure what dynamic side-"
        "selection adds. SL 150% of credit, take 75% profit."),
    "morning_2k_call_no_tp_control": (
        "CONTROL (call-only). Call-only mirror of the morning 2K dynamic preset. "
        "SL 150% of credit, no take-profit."),
    "eod_5k_call_sl150_no_tp_control": (
        "CONTROL (call-only). Call-only mirror of the EOD 5K SL150 dynamic preset. "
        "Target 15:15 ET, SL 150% of credit, no take-profit."),
    "eod_5k_call_tp50_control": (
        "CONTROL (call-only). EOD call-only with a wider SL 200% of credit and a 50% "
        "take-profit. Target 15:15 ET, 5K bucket."),
    "regime_put_credit_test": (
        "REGIME test (put-only). Calls disabled, so it only ever selects a "
        "PUT_CREDIT — for studying put-regime behavior next to the dynamic and "
        "call-only presets. SL 150% of credit."),
    "observe_dynamic_5k": (
        "OBSERVE (no-trade). Both sides considered for scoring, but the no_trade "
        "selector never opens a paper position — watch the balanced score with zero "
        "paper risk."),
}

# ── Phase 9G — preset ordering + kind badges (dynamic FIRST in the dropdown) ──

# The canonical dropdown order: dynamic core (primary) first, then call-only
# controls, then the put-regime test, then observe; any other/legacy profiles
# follow, alphabetically. IDs not listed here keep a stable trailing order.
PRESET_ORDER: tuple[str, ...] = (
    "morning_5k_dynamic_tp75",
    "morning_2k_dynamic_no_tp",
    "eod_5k_dynamic_sl150_no_tp",
    "eod_5k_dynamic_sl200_no_tp",
    "morning_5k_call_tp75_control",
    "morning_2k_call_no_tp_control",
    "eod_5k_call_sl150_no_tp_control",
    "eod_5k_call_tp50_control",
    "regime_put_credit_test",
    "observe_dynamic_5k",
)

PRESET_KIND_BADGES = {
    "dynamic": "🟢 Dynamic",
    "control": "🟡 Control",
    "regime": "🔵 Regime",
    "observe": "⚪ Observe",
}


def preset_kind_badge(preset_kind: Any) -> str:
    """Short badge for a preset kind (dynamic/control/regime/observe)."""
    return PRESET_KIND_BADGES.get(str(preset_kind or "").lower(), "")


def order_profiles_for_dropdown(profile_ids: list[str]) -> list[str]:
    """Sort profile ids so the dynamic presets come FIRST (in PRESET_ORDER), then
    any remaining ids alphabetically. Deterministic + stable."""
    ranked = [p for p in PRESET_ORDER if p in profile_ids]
    rest = sorted(p for p in profile_ids if p not in PRESET_ORDER)
    return ranked + rest


def profile_dropdown_label(profile_id: str, profile_name: Any = None,
                           preset_kind: Any = None) -> str:
    """Friendly dropdown label: '🟢 Dynamic · Morning 5K Dynamic — TP75'. Falls
    back to the profile_name or the id when metadata is missing."""
    name = str(profile_name).strip() if profile_name else str(profile_id)
    badge = preset_kind_badge(preset_kind)
    return f"{badge} · {name}" if badge else name


def profile_description(profile_id: str, fields: dict[str, Any] | None = None) -> str:
    """Friendly description for a preset; falls back to a generic one from fields."""
    known = PRESET_DESCRIPTIONS.get(profile_id)
    if known:
        return known
    f = fields or {}
    if f.get("daily_selector") == "no_trade":
        side = "observe only (no trade)"
    elif f.get("allow_call_credit") and not f.get("allow_put_credit"):
        side = "call-credit only"
    elif f.get("allow_put_credit") and not f.get("allow_call_credit"):
        side = "put-credit only"
    else:
        side = "both sides"
    sym = f.get("symbol") or "SPX"
    dte = f.get("target_dte")
    sel = f.get("daily_selector") or "score_best_valid"
    return (f"{f.get('strategy_type') or 'Strategy'} profile on {sym}, target DTE "
            f"{dte}, {side}, selector `{sel}`.")


def side_policy_display(fields: dict[str, Any]) -> str:
    """Human side policy: prefer the profile's explicit `side_policy`, else derive
    from the allow_* flags + selector (CALL only / PUT only / dynamic both sides)."""
    f = fields or {}
    explicit = f.get("side_policy")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if f.get("daily_selector") == "no_trade":
        return "observe only (no trade)"
    if f.get("allow_call_credit") and not f.get("allow_put_credit"):
        return "call only"
    if f.get("allow_put_credit") and not f.get("allow_call_credit"):
        return "put only"
    return "dynamic both sides"


def _pct_of_credit(pct: Any) -> str | None:
    try:
        return f"{round(float(pct) * 100)}% of credit"
    except (TypeError, ValueError):
        return None


def take_profit_display(fields: dict[str, Any]) -> str:
    """TP as friendly copy: 'None' or '75% of credit (credit capture)'."""
    f = fields or {}
    body = _pct_of_credit(f.get("take_profit_pct"))
    if body is None:
        return "None"
    mode = str(f.get("take_profit_mode") or "").replace("_", " ").strip()
    return f"{body} ({mode})" if mode and mode != "none" else body


def stop_loss_display(fields: dict[str, Any]) -> str:
    """SL as friendly copy: '150% of credit (fixed credit multiple)' or '—'."""
    f = fields or {}
    body = _pct_of_credit(f.get("stop_loss_pct"))
    if body is None:
        return "—"
    mode = str(f.get("stop_loss_mode") or "").replace("_", " ").strip()
    return f"{body} ({mode})" if mode else body


def dynamic_exit_status(fields: dict[str, Any]) -> str:
    """Honest status — lifecycle wiring is DEFERRED, so even 'enabled' reads as
    configured-but-not-active so the operator is never misled."""
    f = fields or {}
    policy = str(f.get("dynamic_exit_policy") or "").strip()
    if f.get("dynamic_exit_enabled"):
        tail = f": {policy}" if policy else ""
        return f"Configured{tail} — not active yet (fixed TP/SL still applies)"
    if policy:
        return f"Configured: {policy} — not active yet"
    return "Off — fixed TP/SL exits"


def entry_window_display(fields: dict[str, Any]) -> str:
    """'10:55–11:05 ET' from start/end, or a single bound, or '—'."""
    f = fields or {}
    start = str(f.get("entry_window_start") or "").strip()
    end = str(f.get("entry_window_end") or "").strip()
    if start and end:
        return f"{start}–{end} ET"
    if start or end:
        return f"{start or end} ET"
    return "—"


def threshold_display(fields: dict[str, Any]) -> str:
    """Account-size bucket label, e.g. '5K' / '2K' / '—'."""
    f = fields or {}
    label = str(f.get("threshold_label") or "").strip()
    return label.upper() if label else "—"


def profile_info_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Pure: the full info-card field set for a profile (Builder + Tester). Phase
    9G enriches it with entry window, target time, threshold, side policy, TP/SL,
    and dynamic-exit status while staying backward-compatible (Data source +
    Safety + Designed to test keys unchanged)."""
    f = fields or {}
    selector = f.get("daily_selector") or "score_best_valid"
    target_time = str(f.get("target_time") or "").strip()
    return {
        "Profile": f.get("profile_name") or f.get("profile_id") or "—",
        "Profile ID": f.get("profile_id") or "—",
        "Symbol": f.get("symbol") or "—",
        "Strategy": f.get("strategy_type") or f.get("strategy_id") or "—",
        "Entry window": entry_window_display(f),
        "Target time": f"{target_time} ET" if target_time else "—",
        "Target DTE": f.get("target_dte"),
        "Threshold": threshold_display(f),
        "Side policy": side_policy_display(f),
        "Selector style": selector_to_style(selector),
        "Selector mode": selector,
        "Take profit (TP)": take_profit_display(f),
        "Stop loss (SL)": stop_loss_display(f),
        "Dynamic exits": dynamic_exit_status(f),
        "Risk profile": f.get("risk_profile") or "—",
        "Data source": providers_to_data_source(
            f.get("structure_provider") or "stub",
            f.get("quote_provider") or "mock").split(":")[0],
        "Enabled": bool(f.get("enabled")),
        "Designed to test": profile_description(str(f.get("profile_id") or ""), f),
        "Safety": "local paper / no broker execution",
    }


# ── Phase 9F — sandbox-aware symbol-health view ──────────────────────────────

def is_sandbox(structure_provider: str | None, quote_provider: str | None) -> bool:
    """True when the active data source is the sandbox (stub exposures / mock|null
    market data)."""
    return (structure_provider == "stub") or (quote_provider in ("mock", "null"))


def symbol_health_view(*, symbol: str, sandbox: bool,
                       market_data_available: bool, exposures_available: bool) -> dict[str, Any]:
    """Display-ready symbol health that distinguishes SANDBOX from unavailable LIVE
    data. In sandbox the engines read 'sandbox mock' / 'sandbox stub' / 'sandbox
    eligible' (never an alarming 'unavailable')."""
    if sandbox:
        return {
            "symbol": symbol,
            "market_data": "sandbox mock",
            "exposures": "sandbox stub",
            "eligible": "sandbox eligible",
            "eligible_ok": True,
            "note": "Sandbox uses SPX mock/stub data regardless of ticker.",
            "reason": "",
        }
    h = symbol_health(symbol=symbol, accepted=True,
                      market_data_available=market_data_available,
                      exposures_available=exposures_available)
    return {
        "symbol": symbol,
        "market_data": "available" if h["market_data_available"] else "unavailable",
        "exposures": "available" if h["exposures_available"] else "unavailable",
        "eligible": "yes" if h["eligible"] else "no",
        "eligible_ok": h["eligible"],
        "note": "",
        "reason": h["reason"],
    }
