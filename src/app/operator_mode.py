"""Phase 9E — Operator Mode helpers (Simple/Advanced, simple→profile mappings,
symbol health, branded labels).

Pure: stdlib only, ZERO ``import streamlit`` and ZERO project imports, so every
helper is unit-testable. NOTHING here executes, places, or previews an order — UX
+ profile-field mapping only.

Visible branding uses "Zσ Strat Tester" (never "Forward Runner" as a tab name).
"""

from __future__ import annotations

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
PAPER_PORTFOLIO_TAB = "Paper Portfolio"
LIVE_COCKPIT_TAB = "Live Cockpit"
STRATEGY_BUILDER_TAB = "Zσ Strat Builder"   # Phase 9F rename
STATS_TAB = "Stats / Review"                 # Phase 9F rename (was Logs / Review)
SETTINGS_TAB = "Settings"

# Phase 9F — branded header subtitle (no "forward runner" wording).
HEADER_TITLE = "ZerσSigma Algo Cockpit"
HEADER_SUBTITLE = "Scanner · Zσ Strat Builder · Zσ Strat Tester · Paper Portfolio · Strategy Stats"


def tab_labels() -> list[str]:
    """The six cockpit tab labels. Uses the branded 'Zσ Strat Builder' / 'Zσ Strat
    Tester' / 'Stats / Review' — never 'Forward Runner' or 'Logs / Review' as a
    visible tab name."""
    return [
        f"🛰 {LIVE_COCKPIT_TAB}",
        f"🧱 {STRATEGY_BUILDER_TAB}",
        f"🧪 {STRAT_TESTER_TAB}",
        f"💼 {PAPER_PORTFOLIO_TAB}",
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
    "Best score", "Best credit", "Conservative / lowest breach risk",
    "No trade / observe only",
)

_SELECTOR_STYLE_MAP = {
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

BTN_PREVIEW = "👁 Preview strategy"
BTN_START_TEST = "▶ Start local paper test"
BTN_STOP_TEST = "■ Stop test"
BTN_CLEAR_STALE = "🧹 Clear stale runner"
BTN_REFRESH = "🔄 Refresh status"
BTN_RECORD_MANUAL = "Record manual paper trade"
BTN_APPLY_SESSION = "Apply local session settings"
BTN_NEW_PROFILE = "Create new profile"
BTN_EDIT_PROFILE = "Edit selected profile"
BTN_CLONE_PROFILE = "Clone selected profile"
BTN_LOAD_PROFILE = "Load selected profile"
BTN_SAVE_PROFILE = "💾 Save profile"
BTN_VALIDATE = "Validate strategy"


def button_labels() -> dict[str, str]:
    """All operator button labels (pure — for tests + reuse)."""
    return {
        "preview": BTN_PREVIEW, "start": BTN_START_TEST, "stop": BTN_STOP_TEST,
        "clear_stale": BTN_CLEAR_STALE, "refresh": BTN_REFRESH,
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
}


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


def profile_info_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Pure: the info-card field set for a profile (for the Zσ Strat Builder)."""
    f = fields or {}
    if f.get("daily_selector") == "no_trade":
        side = "Observe only"
    elif f.get("allow_call_credit") and not f.get("allow_put_credit"):
        side = "Calls only"
    elif f.get("allow_put_credit") and not f.get("allow_call_credit"):
        side = "Puts only"
    else:
        side = "Both sides"
    return {
        "Profile": f.get("profile_name") or f.get("profile_id") or "—",
        "Symbol": f.get("symbol") or "—",
        "Strategy": f.get("strategy_type") or f.get("strategy_id") or "—",
        "Target DTE": f.get("target_dte"),
        "Side preference": side,
        "Selector style": selector_to_style(f.get("daily_selector") or "score_best_valid"),
        "Data source": providers_to_data_source(
            f.get("structure_provider") or "stub",
            f.get("quote_provider") or "mock").split(":")[0],
        "Risk profile": f.get("risk_profile") or "—",
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
