"""ZerσSigma Algo Cockpit — Streamlit shell (Phase 9C command-center refresh).

A dark, ZerσSigma-branded, TABBED cockpit (no dominant sidebar):
  - Live Cockpit    : provider status · market/structure · ranked candidates · decision
  - Strategy Builder: Phase 6 run-profile CRUD (create / clone / edit / validate / save)
  - Forward Runner  : Phase 9A safe local start/stop/status controls + Phase 8 review
  - Portfolio Paper : Phase 9B local paper lifecycle review + manual paper desk
  - Logs / Review   : EOD summary + session-config debug
  - Settings        : session risk overrides + read-only config

LOCAL MONITORING / PAPER ACCOUNTING ONLY. The UI never places, previews, or submits
a broker order. The Forward Runner buttons drive ONLY the local Phase 9A process
controller (a background `run_forward` monitor) — no brokerage anywhere.
"""

from __future__ import annotations

import math
import sys
import uuid
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.app import cockpit_helpers as ch  # noqa: E402
from src.app import control_ui  # noqa: E402
from src.app import operator_mode as om  # noqa: E402
from src.app import profile_builder as pb  # noqa: E402
from src.app import ui_helpers as ui  # noqa: E402
from src.app.session_state import SessionConfig  # noqa: E402
from src.config.strategy_profiles import list_profiles as list_run_profiles  # noqa: E402
from src.forward import review as forward_review  # noqa: E402
from src.paper import ledger as portfolio_ledger  # noqa: E402
from src.paper.account import PaperAccount  # noqa: E402
from src.paper.manual_tracker import (  # noqa: E402
    append_equity_point,
    build_manual_trade_record,
    record_manual_trade,
    snapshot_positions,
    unrealized_pnl_dollars,
)
from src.paper.positions import PaperPosition  # noqa: E402
from src.providers.quotes import tasty_diagnostics as tasty_diag  # noqa: E402
from src.providers.quotes.factory import build_quote_provider  # noqa: E402
from src.providers.quotes.tastytrade_provider import (  # noqa: E402
    TastytradeConfigurationError,
    validation_from_env,
)
from src.providers.structure.factory import build_structure_provider  # noqa: E402
from src.providers.structure.stub import StubStructureProvider  # noqa: E402
from src.reporting.config_change_log import (  # noqa: E402
    log_config_change,
    log_session_snapshot,
)
from src.reporting.eod import generate_eod_summary  # noqa: E402
from src.risk.filters import apply_filters  # noqa: E402
from src.risk.limits import (  # noqa: E402
    load_profile,
    planned_loss_dollars,
    theoretical_max_loss_dollars,
)
from src.selector.daily_selector import (  # noqa: E402
    DEFAULT_SELECTOR_MODE,
    SELECTOR_MODES,
    SelectorConfig,
    select_daily_trade,
)
from src.selector.readiness import compute_readiness  # noqa: E402
from src.strategies.registry import load_strategies  # noqa: E402
from src.utils.config import load_config  # noqa: E402
from src.utils.time import now_et  # noqa: E402

# ──────────────────────────────────────────────────────────────────────
# Boot + brand
# ──────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="ZerσSigma Algo Cockpit",
    page_icon="📈",
    layout="wide",
)
st.markdown(ui.brand_css(), unsafe_allow_html=True)

CFG = load_config(REPO_ROOT)
STRATEGIES = load_strategies(CFG)
OUTPUT_ROOT = CFG.output_dir
profile_names = list(CFG.risk_profiles.keys()) or ["(no profiles)"]


def _init_session(profile_name: str) -> None:
    profile = load_profile(CFG.risk_profiles, profile_name)
    base = SessionConfig.from_profile(profile)
    st.session_state["session_config"] = base.clone()
    st.session_state["session_baseline"] = base
    st.session_state["paper_account"] = PaperAccount(starting_balance=base.starting_balance)
    log_session_snapshot(
        OUTPUT_ROOT,
        session_dict=base.to_dict(),
        active_strategy=st.session_state.get("active_strategy"),
        active_risk_profile=profile_name,
        source="session_start",
    )


if "active_strategy" not in st.session_state:
    st.session_state["active_strategy"] = next(iter(STRATEGIES)) if STRATEGIES else None
if "active_profile" not in st.session_state:
    st.session_state["active_profile"] = (
        CFG.active_risk_profile if CFG.active_risk_profile in profile_names else profile_names[0]
    )
if "session_config" not in st.session_state:
    _init_session(st.session_state["active_profile"])


# ──────────────────────────────────────────────────────────────────────
# Branded header (TOP of app, above all controls) — Phase 9F
# ──────────────────────────────────────────────────────────────────────

st.markdown(
    ui.hero(
        ui.brand_title(om.HEADER_TITLE),
        om.HEADER_SUBTITLE,
        right_html=(ui.pill("LOCAL · NO BROKER EXECUTION", "green")
                    + " " + ui.pill(f"exec: {CFG.providers.execution_active}", "ghost")),
    ),
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────────────────────────────
# Mode + controls (under the header) — strategy / symbol / data source
# ──────────────────────────────────────────────────────────────────────

# Phase 9E/9F — Simple/Advanced mode sits in the header strip (visible, not clipped).
_mode_cols = st.columns([1, 4])
simple_mode = _mode_cols[0].toggle(
    "🟢 Simple Mode", value=st.session_state.get("operator_simple_mode", om.DEFAULT_SIMPLE_MODE),
    key="operator_simple_mode", help=om.SIMPLE_MODE_HELP)
_mode_cols[1].caption(om.SIMPLE_MODE_HELP)

with st.expander("⚙  Controls & data source", expanded=not simple_mode):
    cc = st.columns(3)
    with cc[0]:
        new_strategy = st.selectbox(
            "Strategy",
            options=list(STRATEGIES.keys()) or ["(none)"],
            index=(list(STRATEGIES.keys()).index(st.session_state["active_strategy"])
                   if st.session_state["active_strategy"] in STRATEGIES else 0),
        )
        if new_strategy != st.session_state["active_strategy"]:
            st.session_state["active_strategy"] = new_strategy
        # Phase 9E — first-class ticker/symbol drives the Live Cockpit preview.
        symbol_input = st.text_input(
            "Ticker / symbol", value=st.session_state.get("active_symbol", om.DEFAULT_SYMBOL),
            help="Any ticker. SPX has full ZerσSigma exposure coverage; others may be "
                 "Tasty market-data only. Sandbox prices SPX regardless of symbol.")
        st.session_state["active_symbol"] = om.normalize_symbol(symbol_input)
        new_profile = st.selectbox(
            "Risk profile (session default)",
            options=profile_names,
            index=profile_names.index(st.session_state["active_profile"]),
            help="Profiles are TEMPLATES — edit them in Session & Paper Settings.",
        )
        if new_profile != st.session_state["active_profile"]:
            st.session_state["active_profile"] = new_profile
            _init_session(new_profile)
            st.rerun()
        if st.button("Reset to profile defaults"):
            _init_session(st.session_state["active_profile"])
            st.rerun()

    with cc[1]:
        # Phase 9E — Simple Mode: one Live/Sandbox data-source control. Advanced
        # Mode: explicit Exposure-source + Market-data-source dropdowns.
        # ZerσSigma = EXPOSURE engine; Tastytrade = MARKET-DATA engine.
        if simple_mode:
            _ds_default = om.providers_to_data_source(
                "zerosigma_api" if ch.zs_configured() else "stub",
                "tastytrade" if ch.tasty_configured() else "mock")
            data_source = st.radio(
                "Data source", list(om.DATA_SOURCES),
                index=list(om.DATA_SOURCES).index(_ds_default),
                help="Live = ZerσSigma exposures + Tasty market data. "
                     "Sandbox = stub exposures + mock market data (prices SPX regardless of symbol).")
            _dsp = om.data_source_to_providers(data_source)
            chosen_structure, chosen_quote = _dsp["structure_provider"], _dsp["quote_provider"]
            st.caption(f"{om.EXPOSURE_SOURCE_LABEL}: `{om.exposure_engine_label(chosen_structure)}`  ·  "
                       f"{om.MARKET_DATA_SOURCE_LABEL}: `{om.market_data_engine_label(chosen_quote)}`")
        else:
            available_structure = ["zerosigma_api", "stub"]
            _struct_default = ch.default_provider(
                available_structure, preferred="zerosigma_api", sandbox="stub",
                configured=ch.zs_configured())
            chosen_structure = st.selectbox(
                f"{om.EXPOSURE_SOURCE_LABEL} (structure provider)", options=available_structure,
                index=ch.provider_index(available_structure, _struct_default),
                format_func=ch.provider_label,
                help="ZerσSigma exposures = DA-GEX / VEX / DEX / walls / floors / MaxVol "
                     "(needs ZS_API_* in .env). stub = deterministic sandbox.")
            available_quotes = ["tastytrade", "mock", "null"]
            _quote_default = ch.default_provider(
                available_quotes, preferred="tastytrade", sandbox="mock",
                configured=ch.tasty_configured())
            chosen_quote = st.selectbox(
                f"{om.MARKET_DATA_SOURCE_LABEL} (quote provider)", options=available_quotes,
                index=ch.provider_index(available_quotes, _quote_default),
                format_func=ch.provider_label,
                help="Tasty market data = quotes / chain / bid-ask / volume / OI "
                     "(needs TASTY_* in .env). mock = sandbox chain. null = manual marks.")
        st.caption(f"Execution mode: `{CFG.providers.execution_active}`  ·  no live execution")

    with cc[2]:
        _prof_results = [r for r in list_run_profiles() if r.ok and r.profile]
        _prof_options = ["(none)"] + [r.profile.profile_id for r in _prof_results]
        chosen_profile_id = st.selectbox(
            "Active run profile", options=_prof_options, index=0,
            help="Saved run-profiles (profiles/*.yaml). Build/edit them in the Strategy Builder tab.",
        )
        _active_profile = next(
            (r.profile for r in _prof_results if r.profile.profile_id == chosen_profile_id), None,
        )
        if _active_profile is not None:
            if simple_mode:
                _ap_label = om.profile_dropdown_label(
                    _active_profile.profile_id,
                    getattr(_active_profile, "profile_name", None),
                    getattr(_active_profile, "preset_kind", None),
                )
                st.caption(
                    f"{_ap_label} · symbol {om.normalize_symbol(_active_profile.symbol)} · "
                    f"selector {om.selector_to_style(_active_profile.daily_selector)} · "
                    f"dte {ui.dash(_active_profile.target_dte)}"
                )
            else:
                st.caption(
                    f"`{_active_profile.profile_id}` · symbol=`{_active_profile.symbol}` · "
                    f"selector=`{_active_profile.daily_selector}` · dte={_active_profile.target_dte}"
                )
        _sel_yaml = (
            (CFG.scanner.get("selector") or {}).get("daily_trade_selector")
            if isinstance(CFG.scanner, dict) else None
        ) or DEFAULT_SELECTOR_MODE
        _sel_default = _active_profile.daily_selector if _active_profile else _sel_yaml
        if simple_mode:
            chosen_selector = _sel_default if _sel_default in SELECTOR_MODES else DEFAULT_SELECTOR_MODE
        else:
            chosen_selector = st.selectbox(
                "Daily selector", options=list(SELECTOR_MODES),
                index=(list(SELECTOR_MODES).index(_sel_default) if _sel_default in SELECTOR_MODES else 0),
                help="Chooses AT MOST ONE candidate. Selection only — never executes.",
            )


# Acquire snapshots — explicit separation of structure vs quotes.
structure_provider, resolved_structure_name = build_structure_provider(
    CFG, override=chosen_structure,
)
quote_provider_error: str | None = None
try:
    quote_provider, resolved_quote_name = build_quote_provider(
        override=chosen_quote,
        yaml_active=CFG.providers.quotes_active,
        fallback_on_misconfig=True,
    )
except TastytradeConfigurationError as exc:
    quote_provider_error = f"{type(exc).__name__}: {exc}"
    from src.providers.quotes.mock_provider import MockQuoteProvider
    quote_provider, resolved_quote_name = MockQuoteProvider(), "mock"

# Phase 9E — the scanned symbol is the user-selected ticker (default SPX).
SYMBOL = st.session_state.get("active_symbol") or CFG.scanner.get("symbols", ["SPX"])[0]
try:
    structure = structure_provider.get_snapshot(SYMBOL)
    structure_error: str | None = None
except Exception as exc:
    structure_error = f"{type(exc).__name__}: {exc}"
    structure_provider = StubStructureProvider()
    resolved_structure_name = "stub"
    structure = structure_provider.get_snapshot(SYMBOL)
spot_quote = quote_provider.get_spot(SYMBOL)
# Build the SAME structure-anchored QuoteRequest the scanner uses. The Tasty REST
# provider returns NO chain without `required_strikes` (it never pulls whole
# chains), which previously made the cockpit show "market data unavailable" even
# though auth/root/expiry/chain all worked. Mock/stub use the request for
# strike-alignment. (Selection/strategy logic is unchanged.)
quote_request = ch.build_quote_request(SYMBOL, structure, STRATEGIES)
chain = quote_provider.get_option_chain(SYMBOL, expiry=structure.expiry, request=quote_request)
quote_status = quote_provider.status()

# Reconcile the cockpit's quote STATE from the chain it ACTUALLY fetched (no extra
# network) — distinct buckets, never one generic "unavailable".
QUOTE_VALIDATION = validation_from_env()
QUOTE_STATUS = ch.cockpit_quote_status(
    symbol=SYMBOL, resolved_quote_name=resolved_quote_name, chain=chain,
    quote_status=quote_status, quote_provider_error=quote_provider_error,
    structure_error=structure_error,
    max_spread_abs=QUOTE_VALIDATION.max_spread_abs,
    max_age_seconds=QUOTE_VALIDATION.max_age_seconds,
    requested_strikes=quote_request.required_strikes,
    dte=getattr(structure, "dte", None),
)

# Phase 10C — after-hours preview DTE. The Live Cockpit previews 0DTE during RTH;
# after 17:00 ET (pre-midnight) 0DTE quotes are stale/dead, so the PREVIEW rolls
# to 1DTE. This is display/diagnostic-only — the profile, paper-test, and backtest
# DTE are NEVER mutated (see operator_mode.resolve_preview_dte).
_PROFILE_DTE = structure.dte if (structure and structure.dte is not None) else 0
PREVIEW_DTE = om.resolve_preview_dte(now_et(), _PROFILE_DTE)
AFTER_HOURS_PREVIEW = om.after_hours_preview_active(now_et(), _PROFILE_DTE)

# Phase 10C follow-up — stale-quote gating. QUOTE_STALE = the cockpit's quote chain
# returned but failed validation because quotes are STALE (the after-hours case).
# LIVE_QUOTES_STALE additionally requires a LIVE (non-sandbox) source — it disables
# Start Paper Test and downgrades the live "Decision" to a preview. Never loosens
# the underlying validation; it only changes what the UI claims.
QUOTE_STALE = (
    QUOTE_STATUS["state"] == "chain_returned_stale"
    or (
        QUOTE_STATUS["state"] == "chain_returned_validation_failed"
        and str(QUOTE_STATUS["details"].get("top_blocker") or "").lower() == "stale"
    )
)
LIVE_QUOTES_STALE = QUOTE_STALE and not om.is_sandbox(
    resolved_structure_name, resolved_quote_name)

session: SessionConfig = st.session_state["session_config"]
baseline: SessionConfig = st.session_state["session_baseline"]
paper_account: PaperAccount = st.session_state["paper_account"]

# Spot is surfaced from BOTH sides so drift is visible.
quote_spot = chain.spot if chain else (spot_quote.last if spot_quote else None)
structure_spot = structure.spot


def _fmt_quote(q: dict) -> str:
    """'bid / mid / ask' formatting for the candidate table."""
    b = q.get("bid")
    m = q.get("mid")
    a = q.get("ask")
    if b is None or m is None or a is None:
        return "—"
    return f"{b:.2f} / {m:.2f} / {a:.2f}"


def _chart_ready(values: list | tuple) -> bool:
    """True when Streamlit/Vega has enough finite variation to render safely."""
    nums: list[float] = []
    for v in values or []:
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x):
            nums.append(x)
    return len(nums) >= 2 and any(x != nums[0] for x in nums[1:])


# ──────────────────────────────────────────────────────────────────────
# Section renderers (each renders one cockpit panel)
# ──────────────────────────────────────────────────────────────────────

def render_symbol_health() -> None:
    """Phase 9F — compact symbol-health panel. Distinguishes Tasty MARKET DATA from
    ZerσSigma EXPOSURES, and SANDBOX from unavailable LIVE data (sandbox reads
    'sandbox mock' / 'sandbox stub', never an alarming 'unavailable')."""
    sandbox = om.is_sandbox(resolved_structure_name, resolved_quote_name)
    # Phase 10B hotfix — "market data available" means USABLE quotes (validation
    # not failed), not merely a non-None chain. A returned-but-validation-blocked
    # Tasty chain is its own state, not "unavailable".
    market_data_available = QUOTE_STATUS["available"]
    exposures_available = (
        structure_error is None and structure is not None
        and (structure.exposures.da_gex_signed is not None
             or structure.exposures.maxvol is not None)
    )
    view = om.symbol_health_view(
        symbol=SYMBOL, sandbox=sandbox,
        market_data_available=market_data_available, exposures_available=exposures_available)
    _tb = QUOTE_STATUS["details"].get("top_blocker")
    _q_short = om.quote_state_label(QUOTE_STATUS["state"], _tb)
    cols = st.columns(5)
    cols[0].metric("Symbol", view["symbol"])
    # Phase 10B — short, readable quote-state label (no clipped long copy).
    # Phase 10C follow-up — when the preview rolls to 1DTE after-hours, show the
    # quote-chain DTE as a card sub-label ("1DTE quote chain · after-hours preview").
    _quote_detail = om.after_hours_quote_detail(AFTER_HOURS_PREVIEW and not sandbox, PREVIEW_DTE)
    cols[1].metric("Quotes", view["market_data"] if sandbox else _q_short,
                   _quote_detail, delta_color="off")
    cols[2].metric("Exposures", view["exposures"])
    _elig = QUOTE_STATUS["eligible_hint"] if not sandbox else view["eligible"]
    cols[3].metric("Strategy eligible", _elig)
    cols[4].markdown("<div class='zsa-pill-cell'>" + ui.pill("NO BROKER EXECUTION", "green")
                     + "</div>", unsafe_allow_html=True)
    if view["note"]:
        st.caption(view["note"])
    # Precise, trader-facing quote-state banner (stale wording when after-hours).
    _banner = None if sandbox else om.quote_state_banner(QUOTE_STATUS["state"], SYMBOL, _tb,
                                                         after_hours=AFTER_HOURS_PREVIEW)
    if _banner:
        st.warning(_banner)
        if QUOTE_STALE:
            st.caption("🌙 After-hours / stale quote mode — " + om.stale_quote_mode_banner(SYMBOL))
    elif view["reason"]:
        st.warning(view["reason"])
    _render_strategy_synopsis(_active_profile, context="live")
    # Phase 10C — after-hours DTE preview roll (0DTE → 1DTE after the close).
    if AFTER_HOURS_PREVIEW and not sandbox:
        st.info("🌙 " + om.after_hours_preview_banner(SYMBOL, _PROFILE_DTE))
        st.caption(f"Profile DTE: **{om.dte_label(_PROFILE_DTE)}**  ·  Quote chain: "
                   f"**{om.dte_label(PREVIEW_DTE)} after-hours preview**  ·  "
                   "**Strategy DTE unchanged**  ·  Structure: still Zσ structure context. "
                   "Preview-only while quotes are stale.")


def render_provider_status() -> None:
    with st.expander("Exposure + market-data status", expanded=False):
        cols = st.columns(3)
        cols[0].metric(
            "StructureProvider", f"{structure_provider.name}",
            f"context @ {structure.quote_ts.strftime('%H:%M:%S')}",
        )
        cols[1].metric(
            "QuoteProvider", f"{quote_provider.name}",
            (
                f"chain @ {chain.quote_ts.strftime('%H:%M:%S')}" if chain else
                ("spot @ " + spot_quote.ts.strftime("%H:%M:%S") if spot_quote else "no quotes")
            ),
        )
        if chain is not None and chain.resolved_root_symbol:
            cols[1].caption(
                f"root=`{chain.resolved_root_symbol}` (source: {chain.root_resolution_source or '-'})"
            )
        if chosen_quote == "tastytrade" and resolved_quote_name != "tastytrade":
            st.warning(
                "Selected `tastytrade` but provider is not configured "
                "(missing TASTY_OAUTH_* or TASTY_USERNAME/PASSWORD). Running "
                "on the mock chain. See `.env.example` for required variables."
            )
        if quote_provider_error is not None:
            st.warning(f"Quote provider boot error → fell back to mock. `{quote_provider_error}`")
        cols[2].metric("ExecutionProvider", CFG.providers.execution_active, "no live execution")
        if not quote_status.connected:
            st.caption(f"_QuoteProvider notes: {quote_status.notes or 'disconnected'}_")

        if chosen_structure == "zerosigma_api" and resolved_structure_name != "zerosigma_api":
            st.warning(
                "Selected `zerosigma_api` but provider is not configured "
                "(missing ZS_API_AUTH_MODE or credentials). Running on the stub. "
                "See `.env.example` for the required variable names."
            )
        elif structure_error is not None:
            st.warning(
                f"`zerosigma_api` failed at boot → fell back to stub. "
                f"Error: `{structure_error}`. Check `ZS_API_BASE_URL` and auth env vars."
            )
        if resolved_structure_name == "zerosigma_api" and hasattr(structure_provider, "status"):
            provider_status = structure_provider.status()
            st.caption(
                f"auth_mode: **{provider_status.get('auth_mode')}**  ·  "
                f"configured: **{provider_status.get('configured')}**  ·  "
                f"exposure_series_effective: **{provider_status.get('exposure_series_effective')}**"
            )
            if provider_status.get("public_only"):
                st.info(
                    "`public_only` mode — no Authorization header sent. "
                    "`/exposure/series` is skipped, so **Put Ceiling / Call Floor / "
                    "MaxVol will be None**. Switch to `bearer` / `login` / `service_token` "
                    "and set `ZS_API_ENABLE_EXPOSURE_SERIES=true` to populate them."
                )
            missing = (structure.raw or {}).get("missing_fields") or []
            if missing:
                st.caption(f"_Missing structure fields this tick: `{', '.join(missing)}`_")
            with st.expander("zerosigma_api status (no secrets)", expanded=False):
                st.json(provider_status)


def _compute_best_eligible() -> dict | None:
    """Read-only: the top eligible candidate for the operator 'Best Eligible
    Setup'. Mirrors Ranked-candidates generation but extracts a compact dict and
    is fully guarded — any failure returns None (the decision layer then says no
    eligible setup surfaced). Does NOT change scanner/selector math."""
    try:
        if not STRATEGIES or chain is None:
            return None
        strat = STRATEGIES[st.session_state["active_strategy"]]
        params = {**(strat.default_parameters or {}), **session.to_filter_params()}
        cands = strat.generate_candidates(structure, chain, params)
        apply_filters(cands, session.to_filter_params())
        for c in cands:
            strat.score(c, structure, chain, params)
        eligible = [c for c in cands if not getattr(c, "rejected", False)]
        if not eligible:
            return None
        eligible.sort(key=lambda c: -(getattr(c, "score", 0.0) or 0.0))
        c0 = eligible[0]
        long_k = getattr(c0, "long_strike", None)
        if long_k is None:
            long_k = (c0.meta.get("long_leg") or {}).get("strike")
        sc = getattr(c0, "score", None)
        return {
            "side": getattr(c0, "side", None),
            "short": getattr(c0, "short_strike", None),
            "long": long_k,
            "score": round(sc, 4) if isinstance(sc, (int, float)) else None,
            "credit": getattr(c0, "credit", None),
            "reason": "top-scoring eligible candidate this scan",
        }
    except Exception:
        return None


def render_operator_decision() -> None:
    """Phase 9H — operator decision layer: translate raw structure into a human
    summary (Structure Read / Trade Bias / Candidate Risk / Best Eligible Setup /
    Why·Why Not). Never invents data — missing fields read 'unavailable'."""
    st.subheader("🧭 Operator read")
    spot_val, _src = ch.spot_with_source(
        chain.spot if chain else None, structure_spot,
        spot_quote.last if spot_quote else None,
    )
    ex = structure.exposures
    gamma = ch.primary_secondary_gamma(ex, spot_val)
    wings = ch.wing_stack(ex, spot_val)
    wds = ch.wing_dominance(ex, spot_val)   # Phase 9J — dominant 10K WDS wing
    best = _compute_best_eligible()
    layer = ch.operator_decision_layer(
        spot=spot_val, gamma_regime=ex.gamma_regime, da_gex=ex.da_gex_signed,
        gamma=gamma, wings=wings, best_eligible=best, chain_available=chain is not None,
        wds=wds,
    )
    _display_text = om.friendly_text if simple_mode else str
    left, right = st.columns(2)
    left.markdown(f"**Structure Read**  \n{_display_text(layer['structure_read'])}")
    left.markdown(f"**Trade Bias**  \n{_display_text(layer['trade_bias'])}")
    left.markdown(f"**Candidate Risk**  \n{_display_text(layer['candidate_risk'])}")
    # Phase 10B — only say "Best Eligible Setup" when quotes are actually usable.
    # A stale/validation-blocked top candidate is a PREVIEW, not an eligible setup.
    _tb = QUOTE_STATUS["details"].get("top_blocker")
    if best:
        _setup = om.candidate_label(best.get("side"), best.get("short"), best.get("long"))
        _bits = []
        if isinstance(best.get("score"), (int, float)):
            _bits.append(f"score {best['score']:.2f}")
        if best.get("credit") is not None:
            _bits.append(f"credit {ch.fmt_money(best['credit'])}")
        if _bits:
            _setup += " — " + ", ".join(_bits)
        if QUOTE_STATUS["available"]:
            right.markdown(f"**Best Eligible Setup**  \n{_setup}")
        elif QUOTE_STALE:
            right.markdown(f"**Best Candidate Preview — Stale Quotes**  \n{_setup}  \n"
                           "_Blocked: stale quotes — preview pricing only._")
        else:
            right.markdown(f"**Best Candidate Preview — Blocked**  \n{_setup}  \n"
                           f"_Blocked: {om.quote_state_label(QUOTE_STATUS['state'], _tb).lower()}._")
    else:
        right.markdown(
            f"**Best Eligible Setup**  \nNo eligible setup. "
            f"{_display_text(layer['best_eligible_setup'])}")
    right.markdown(f"**Why / Why Not**  \n{_display_text(layer['why_why_not'])}")
    st.caption(
        f"Gamma source: {gamma['source'].replace('_', ' ')} · {gamma['note']}  ·  "
        "Operator read only — no broker execution."
    )


def render_market() -> None:
    st.subheader("Market / structure")
    # Phase 9D — spot fallback (prefer quote spot, fall back to Zσ structure spot).
    spot_val, spot_src = ch.spot_with_source(
        chain.spot if chain else None, structure_spot,
        spot_quote.last if spot_quote else None,
    )
    ex = structure.exposures
    gamma = ch.primary_secondary_gamma(ex, spot_val)
    # ── Prime cards (Phase 9H: Primary/Secondary Gamma replace DDOI) ──
    top = st.columns(6)
    top[0].metric("Spot", ch.fmt_price(spot_val), spot_src)
    top[1].metric("Gamma regime", ch.gamma_regime_badge(ex.gamma_regime, ex.da_gex_signed))
    top[2].metric("DA-GEX", ch.fmt_exposure(ex.da_gex_signed))
    top[3].metric("MaxVol", ch.fmt_strike(ex.maxvol))
    top[4].metric("Primary gamma", gamma["primary_fmt"],
                  gamma["source"].replace("_", " ") if gamma["available"] else None)
    top[5].metric("Secondary gamma", gamma["secondary_fmt"])
    if not gamma["available"]:
        st.caption("Primary/secondary gamma unavailable from current structure payload.")

    # ── Wing Stack (Phase 9H): put ceilings + call floors at 2K/5K/10K ──
    ws = ch.wing_stack(ex, spot_val)
    st.markdown("**Wing Stack** — structural levels by volume threshold")
    pc = st.columns(3)
    for _i, _e in enumerate(ws["put_ceilings"]):
        _label = om.friendly_text(_e["label"]) if simple_mode else _e["label"]
        pc[_i].metric(_label, _e["strike_fmt"],
                      f"{_e['distance_fmt']} pts" if _e["available"] else None)
    cf = st.columns(3)
    for _i, _e in enumerate(ws["call_floors"]):
        _label = om.friendly_text(_e["label"]) if simple_mode else _e["label"]
        cf[_i].metric(_label, _e["strike_fmt"],
                      f"{_e['distance_fmt']} pts" if _e["available"] else None)
    # ── Wing corridor + dominant WDS (Phase 9J/10A) ──
    # Structure is ACTIVE only when CW1 < spot < PW1; a call floor above spot is
    # NOT an active floor. Raw WDS may exist but is not active out of corridor.
    wd = ch.wing_dominance(ex, spot_val)
    st.markdown("**Wing corridor + dominant wing**")
    # Phase 10C — plain-English corridor explainer (CW1/PW1 are always defined).
    st.caption(
        "Corridor is active only when the **10K call floor** is below spot AND the "
        "**10K put ceiling** is above spot — i.e. CW1 (10K call floor) < Spot < "
        "PW1 (10K put ceiling). Outside that band the structure is not yet formed."
    )
    cc = st.columns(4)
    cc[0].metric("10K call floor (CW1)", ch.fmt_strike(wd["corridor_cw1"]))
    cc[1].metric("Spot", ch.fmt_price(spot_val))
    cc[2].metric("10K put ceiling (PW1)", ch.fmt_strike(wd["corridor_pw1"]))
    cc[3].metric("Corridor", "✅ Active" if wd["corridor_valid"] else "⛔ Inactive")
    if wd["wds_active"]:
        _side = wd["dominant_wing_side"]
        _w1v = wd["call_w1_volume"] if _side == "CALL" else wd["put_w1_volume"]
        _w2s = wd["call_w2_strike"] if _side == "CALL" else wd["put_w2_strike"]
        _w2v = wd["call_w2_volume"] if _side == "CALL" else wd["put_w2_volume"]
        _wsr = wd["call_wsr"] if _side == "CALL" else wd["put_wsr"]
        dcols = st.columns(4)
        _dom_label = (om.friendly_text(wd["dominant_wing_label"])
                      if simple_mode else wd["dominant_wing_label"])
        dcols[0].metric(f"Active dominant {_dom_label}",
                        ch.fmt_strike(wd["dominant_wing_strike"]),
                        f"WDS {wd['dominant_wing_wds_pct']} · Tier {wd['dominant_wing_tier']}")
        dcols[1].metric("W1 volume", ch.fmt_count(_w1v))
        dcols[2].metric(f"W2 @ {ch.fmt_strike(_w2s)}", ch.fmt_count(_w2v))
        dcols[3].metric("WSR (W2/W1)", f"{round(_wsr * 100)}%" if _wsr is not None else "—")
        st.caption(om.friendly_text(wd["wds_reason"]) if simple_mode else wd["wds_reason"])
    elif wd["raw_wds_source"] == "true":
        _raw_label = (om.friendly_text(wd["raw_dominant_label"])
                      if simple_mode else wd["raw_dominant_label"])
        st.caption(f"Raw WDS only (corridor inactive — NOT active structure): "
                   f"{_raw_label} {ch.fmt_strike(wd['raw_dominant_strike'])} "
                   f"WDS {wd['raw_dominant_wds_pct']} Tier {wd['raw_dominant_tier']}.  "
                   f"{om.friendly_text(wd['wds_reason']) if simple_mode else wd['wds_reason']}")
    else:
        st.caption(
            f"WDS unavailable — "
            f"{om.friendly_text(wd['wds_reason']) if simple_mode else wd['wds_reason']}")
    near = ws["nearest_wing"]
    _near_label = (om.friendly_text(near["label"]) if simple_mode and near else
                   near["label"] if near else None)
    near_txt = (f"{_near_label} {near['strike_fmt']} ({near['distance_fmt']} pts)"
                if near else "unavailable")
    _dom_label = (om.friendly_text(wd["dominant_wing_label"])
                  if simple_mode and wd["dominant_wing_label"] else wd["dominant_wing_label"])
    _dom_txt = (f"{_dom_label} {ch.fmt_strike(wd['dominant_wing_strike'])} "
                f"(WDS {wd['dominant_wing_wds_pct']}, Tier {wd['dominant_wing_tier']})"
                if wd["wds_active"]
                else "none (corridor inactive)" if wd["raw_wds_source"] == "true" else "unavailable")
    _brd = (f"{wd['nearest_wing_distance_points']:.2f} pts"
            if wd["nearest_wing_distance_points"] is not None else "—")
    st.caption(f"Active dominant wing: **{_dom_txt}**  ·  Nearest local wing (immediate breach "
               f"risk): **{near_txt}** — {_brd} from spot")
    if not (ws["put_ceilings"][2]["available"] or ws["call_floors"][2]["available"]):
        st.caption("10K wings (and true WDS) require upstream exposure volume ≥ 10,000 "
                   "(subscription series); unavailable in sandbox / mock data.")

    # ── Phase 10B hotfix — precise quote STATE (not one generic "unavailable").
    # A returned-but-validation-blocked Tasty chain is NOT "unavailable" — say so.
    if not QUOTE_STATUS["available"]:
        _det = QUOTE_STATUS["details"]
        if QUOTE_STATUS["state"] in (
            "chain_returned_validation_failed",
            "chain_returned_stale",
            "chain_returned_missing_required_strikes",
            "chain_resolved_quotes_unavailable",
        ):
            _tb = _det.get("top_blocker")
            st.warning(om.quote_state_banner(
                QUOTE_STATUS["state"], SYMBOL, _tb, after_hours=AFTER_HOURS_PREVIEW))
            st.caption(
                f"Quotes: **{om.quote_state_label(QUOTE_STATUS['state'], _tb)}** — chain returned "
                f"({_det['quote_count']} quotes · {_det['root'] or '—'} @ "
                f"{_det['expiration'] or '—'}), {_det['validation_failed_count']} failed validation "
                f"· blocker `{_tb or '—'}` (max_spread_abs={_det['max_spread_abs']}, observed worst "
                f"spread {_det['observed_failing_spread']}). Structure preview only."
            )
        else:
            # genuine no-chain / auth / root / config state
            _banner = om.quote_state_banner(
                QUOTE_STATUS["state"], SYMBOL, _det.get("top_blocker"),
                after_hours=AFTER_HOURS_PREVIEW,
            ) or QUOTE_STATUS["banner"]
            st.warning(f"{_banner} Showing Zσ structure context only.")
            _actions = ch.chain_unavailable_actions(
                resolved_quote_name, last_error=getattr(quote_status, "last_error", None))
            for _a in (_actions[:2] if simple_mode else _actions):
                st.caption(f"• {_a}")

    st.caption(
        f"Structure from `{structure.source}` @ {structure.quote_ts.isoformat()}  ·  "
        f"chain from `{chain.provider_name if chain else '—'}` "
        f"@ {chain.quote_ts.isoformat() if chain else '—'}  ·  "
        f"expiry {structure.expiry}  ·  DTE {structure.dte}"
    )

    # ── Phase 10B hotfix — precise Tasty quote diagnostics (READ-ONLY) ──
    # "ZerσSigma structure renders, but Tasty market data is unavailable — why?"
    # Walks config → auth/session → SPX root → expiry/DTE → chain → quote
    # validation and surfaces the exact blocker. Button-gated so it only hits
    # Tasty on demand (not every render). No orders, no order preview, no secrets.
    with st.expander("Quote status & diagnostics (read-only)", expanded=False):
        # The cockpit's ACTUAL quote state — derived from the chain the app already
        # fetched (no extra network). Surfaces the validation config + exact blocker.
        _d = QUOTE_STATUS["details"]
        st.markdown(f"**Cockpit quote state:** `{QUOTE_STATUS['state']}` → "
                    f"**{QUOTE_STATUS['label']}**")
        for _l, _v in (
            ("quote provider", _d["quote_provider"] or resolved_quote_name),
            ("auth mode configured", _d["auth_mode_configured"]),
            ("auth mode", _d["auth_mode"] or "—"),
            ("root", _d["root"] or "—"),
            ("expiry", _d["expiration"] or "—"),
            ("DTE", _d["dte"] if _d["dte"] is not None else "—"),
            ("required strikes count", _d["required_strike_count"]),
            ("required strikes", _d["requested_strikes"] or "—"),
            ("chain returned", _d["chain_returned"]),
            ("quote count", _d["quote_count"]),
            ("validation state", _d["validation_state"]),
            ("validation passed / failed",
             f"{_d['validation_passed_count']} / {_d['validation_failed_count']}"),
            ("top blocker", _d["top_blocker"] or "—"),
            ("strike range", f"{_d['strike_min']} – {_d['strike_max']}"),
            ("max_spread_abs / max_age_s", f"{_d['max_spread_abs']} / {_d['max_age_seconds']}"),
            ("observed worst spread", _d["observed_failing_spread"] or "—"),
            ("missing strikes", _d["missing_strikes"] or "—"),
            ("last sanitized error", _d["last_error"] or "—"),
        ):
            st.text(f"{_l:<28}: {_v}")
        st.caption("max_spread_abs is the `TASTY_QUOTE_MAX_SPREAD_ABS` env cap (per-leg, "
                   "$ absolute). SPX index options can exceed a tight $5 cap — raise it in "
                   ".env if validation is over-blocking.")
        st.divider()
        st.caption(
            "Live read-only network probe (config → auth/session → SPX root → expiry/DTE → "
            "chain → validation). No orders, no order preview, no secrets."
        )
        _dc = st.columns([1, 2])
        # Phase 10C — after-hours, default the probe to the rolled preview DTE (1)
        # so the on-demand Tasty check fetches the fresh 1DTE chain, not dead 0DTE.
        _ddte = int(_dc[0].selectbox("Target DTE", [0, 1],
                                     index=(1 if PREVIEW_DTE == 1 else 0),
                                     key="tasty_diag_dte"))
        _dspot = (structure.spot if getattr(structure, "spot", 0) and structure.spot > 0
                  else None)
        _dc[1].caption(f"ATM probe hint (Zσ spot): {ch.fmt_strike(_dspot) if _dspot else '—'}")
        if st.button("Run Tasty quote diagnostic", key="tasty_diag_btn"):
            try:
                _diag = tasty_diag.diagnose_from_env(
                    symbol=SYMBOL, target_dte=_ddte, spot_hint=_dspot)
            except Exception as _exc:               # never break the cockpit
                _diag = {**tasty_diag._blank_result(SYMBOL, _ddte),
                         "final_status": f"diagnostic error: {type(_exc).__name__}",
                         "blocker": "diagnostic_error"}
            (st.success if _diag.get("blocker") is None else st.warning)(
                _diag.get("final_status") or "—")
            for _lbl, _val in tasty_diag.summary_rows(_diag):
                st.text(f"{_lbl:<24}: {_val}")
            with st.expander("Full sanitized diagnostic (JSON)", expanded=False):
                st.json(_diag, expanded=False)
        else:
            st.caption("Click to probe Tasty now (one read-only round-trip — no orders).")

    # ── Advanced structure / raw diagnostics (walls, flip, DDOI) — ADVANCED
    # MODE ONLY (Phase 9I: removed from the normal trader flow + DDOI never in
    # prime UI). ──
    if not simple_mode:
        with st.expander("Advanced structure / raw diagnostics", expanded=False):
            adv = st.columns(4)
            adv[0].metric("Call wall", ch.fmt_strike(ex.call_wall))
            adv[1].metric("Put wall", ch.fmt_strike(ex.put_wall))
            adv[2].metric("Gamma flip", ch.fmt_strike(ex.gamma_flip))
            ddoi = ch.ddoi_advanced(ex)
            adv[3].metric("DDOI pin", ddoi["value_fmt"])
            st.caption(ddoi["note"])
            st.caption(ch.DDOI_HELP)
            _missing = (structure.raw or {}).get("missing_fields") or []
            if _missing:
                st.caption("Structure fields unavailable from payload: " + ", ".join(_missing))


def _render_candidate_simple(c, rd: dict) -> None:
    """Phase 10C — clean Simple-Mode candidate detail (trader labels, no dev
    jargon: no threshold / gap / edge / bucket / skew / raw breakdown)."""
    _tb = QUOTE_STATUS["details"].get("top_blocker")
    s1 = st.columns(4)
    s1[0].metric("Setup", om.side_label(c.side))
    s1[1].metric("Short / Long",
                 f"{ch.fmt_strike(c.short_strike)} / {ch.fmt_strike(c.long_strike)}")
    s1[2].metric("Score", f"{c.score:.2f}")
    s1[3].metric("Credit", ch.fmt_money(c.credit))
    s2 = st.columns(4)
    s2[0].metric("Quote Status", om.candidate_quote_status_label(
        c.meta.get("short_leg") or {}, c.meta.get("long_leg") or {},
        quote_state=QUOTE_STATUS["state"], top_blocker=_tb))
    s2[1].metric("Risk Status", om.candidate_risk_status_label(rd.get("risk_rejection_type")))
    s2[2].metric("Blocker", om.candidate_blocker_label(
        rejected=c.rejected, risk_rejection_type=rd.get("risk_rejection_type"),
        quote_state=QUOTE_STATUS["state"], top_blocker=_tb,
        eligible_base=rd.get("selector_eligible_base")))
    s2[3].metric("Anchor", om.anchor_label(c.meta.get("anchor_source")))
    _av = c.meta.get("anchor_volume")
    _av_txt = f"{_av:,.0f}" if isinstance(_av, (int, float)) else "—"
    _dist = (f"{c.distance_from_spot:.1f} pts"
             if isinstance(c.distance_from_spot, (int, float)) else "—")
    st.caption(f"Anchor volume: **{_av_txt}**  ·  distance from spot: **{_dist}**")
    st.caption("Full score breakdown, thresholds, and quote diagnostics are in Advanced Mode.")


def _render_candidate_advanced(c, rd: dict) -> None:
    """Advanced-Mode candidate detail — full raw breakdown (kept for diagnostics
    only; never shown in Simple Mode)."""
    etop = st.columns(4)
    etop[0].metric("Score", f"{c.score:.4f}")
    etop[1].metric("Threshold",
                   f"{c.score_threshold:.2f}" if c.score_threshold is not None else "—")
    etop[2].metric("Gap", f"{c.score_gap_to_threshold:+.4f}"
                   if c.score_gap_to_threshold is not None else "—")
    etop[3].metric("Rejection type", c.rejection_type or "—")

    anchor_cols = st.columns(4)
    anchor_cols[0].metric("Anchor", c.meta.get("anchor_source") or "—")
    av = c.meta.get("anchor_volume")
    anchor_cols[1].metric("Anchor volume", f"{av:,.0f}" if isinstance(av, (int, float)) else "—")
    anchor_cols[2].metric("Volume source", c.meta.get("anchor_volume_source") or "—")
    anchor_cols[3].metric("structure_strength_source",
                          c.meta.get("structure_strength_source") or "—")

    if c.weak_components:
        st.markdown("**Weakest components:** " + ", ".join(f"`{w}`" for w in c.weak_components))
    if c.rejection_reasons:
        st.markdown("**Filter reasons:** " + ", ".join(f"`{r}`" for r in c.rejection_reasons))
    short_meta = c.meta.get("short_leg") or {}
    long_meta = c.meta.get("long_leg") or {}
    if any(k in short_meta or k in long_meta
           for k in ("validation_passed", "validation_rejection_reason", "quote_time")):
        qcols = st.columns(2)
        for col, leg_label, leg in (
            (qcols[0], "Short leg", short_meta), (qcols[1], "Long leg", long_meta),
        ):
            passed = leg.get("validation_passed")
            reason = leg.get("validation_rejection_reason")
            qtime = leg.get("quote_time")
            badge = ("—" if passed is None else ("✓ pass" if passed else f"✗ {reason or 'fail'}"))
            col.metric(f"{leg_label} quote", badge, qtime or "")

    if rd:
        st.caption(
            "Phase 4.1: `score_edge` (score − threshold), "
            "`quote_quality_bucket` (good/acceptable/poor/wide/invalid), "
            "`risk_rejection_type` (planned/theoretical cap), "
            "`selector_blockers` (eligibility audit). "
            "Phase 4.2: `bid_ask_quality` is RELATIVE (pct-of-mid) and shares the "
            "SAME cutoffs as the bucket; quote VALIDATION (broker pass/fail per leg) "
            "is separate from the quote QUALITY score. `quote_clock_skew_*` flags a "
            "negative quote age clamped to 0. `strict_target_dte` (CLI scanner only)."
        )
        sc = st.columns(4)
        sc[0].metric(
            "Score edge",
            f"{c.score_edge:+.4f}" if isinstance(c.score_edge, (int, float)) else "—",
            "marginal" if c.marginal_score else ("passed" if c.score_edge_passed else "below"),
        )
        sc[1].metric("Quote bucket", rd.get("quote_quality_bucket") or "—")
        sc[2].metric("Risk type", rd.get("risk_rejection_type") or "—")
        sc[3].metric(
            "Eligible (base)", "yes" if rd.get("selector_eligible_base") else "no",
            rd.get("selector_readiness_note") or "",
        )
        blockers = rd.get("selector_blockers") or []
        if blockers:
            st.markdown("**Selector blockers:** " + ", ".join(f"`{b}`" for b in blockers))
        p42 = st.columns(4)
        p42[0].metric(
            "b/a quality", f"{c.meta.get('bid_ask_quality', 0.0):.2f}",
            c.meta.get("bid_ask_quality_mode") or "—",
        )
        p42[1].metric("b/a reason", c.meta.get("bid_ask_quality_reason") or "—")
        skew_det = c.meta.get("quote_clock_skew_detected")
        p42[2].metric("Clock skew", "yes" if skew_det else ("no" if skew_det is False else "—"))
        skew_s = c.meta.get("quote_clock_skew_seconds")
        p42[3].metric("Skew (s)", f"{skew_s:.2f}" if isinstance(skew_s, (int, float)) else "—")
        q_reason = rd.get("quote_quality_reason")
        if q_reason:
            st.caption(f"Quote-quality reason: `{q_reason}`")
        strict_on = rd.get("strict_target_dte")
        strict_ok = rd.get("strict_target_dte_passed")
        st.caption(
            f"strict_target_dte: `{strict_on}`  ·  passed: `{strict_ok}`  "
            "_(CLI scanner gate; not enforced in this inline preview)_"
        )
    st.json(c.score_breakdown, expanded=False)


def render_candidates() -> None:
    st.subheader("Ranked candidates")
    if not STRATEGIES:
        st.warning("No strategies registered. Check `config/strategies.yaml`.")
        return
    if chain is None:
        st.error("QuoteProvider returned no chain. Cannot build candidates.")
        return

    strat = STRATEGIES[st.session_state["active_strategy"]]
    params = {**(strat.default_parameters or {}), **session.to_filter_params()}
    candidates = strat.generate_candidates(structure, chain, params)
    apply_filters(candidates, session.to_filter_params())
    for c in candidates:
        strat.score(c, structure, chain, params)
    candidates.sort(key=lambda c: -c.score)

    if not candidates:
        st.info("No candidates produced for the current snapshot.")
    else:
        rows = []
        for c in candidates:
            planned = planned_loss_dollars(
                c.credit, c.max_risk, session.default_stop_variant, session.contracts_per_trade,
            )
            theoretical = theoretical_max_loss_dollars(c.max_risk, session.contracts_per_trade)
            short_leg = c.meta.get("short_leg") or {}
            long_leg = c.meta.get("long_leg") or {}
            sp = short_leg.get("validation_passed")
            lp = long_leg.get("validation_passed")
            if sp is None and lp is None:
                quote_badge = "—"
            elif sp is True and lp is True:
                quote_badge = "✓ pass"
            else:
                quote_badge = "✗ fail"
            import os as _os_inline
            try:
                _mse = float(_os_inline.getenv("MIN_SCORE_EDGE", "0.02"))
            except (TypeError, ValueError):
                _mse = 0.02
            readiness = compute_readiness(
                c,
                session=session,
                threshold=(c.score_threshold or session.no_trade_score_threshold or 0.60),
                min_score_edge=_mse,
                target_dte=0,
                available_expiries=([structure.expiry] if structure.expiry else None),
                today_et=now_et().date(),
            )
            c.meta["_readiness"] = dict(readiness)

            # Phase 10C — Simple Mode shows ONLY trader-facing columns (Setup /
            # Score / Credit / Quote Status / Risk Status / Blocker / Anchor /
            # Anchor Vol / Distance). Raw dev fields (b/a quality, gap, edge,
            # bucket, risk_type, rejection internals) move to Advanced Mode.
            _tb_row = QUOTE_STATUS["details"].get("top_blocker")
            _av_row = c.meta.get("anchor_volume")
            row = {
                "Setup": om.candidate_label(c.side, c.short_strike, c.long_strike),
                "Score": round(c.score, 2),
                "Credit": ch.fmt_money(c.credit),
                "Quote Status": om.candidate_quote_status_label(
                    short_leg, long_leg, quote_state=QUOTE_STATUS["state"], top_blocker=_tb_row),
                "Risk Status": om.candidate_risk_status_label(readiness.get("risk_rejection_type")),
                "Blocker": om.candidate_blocker_label(
                    rejected=c.rejected, risk_rejection_type=readiness.get("risk_rejection_type"),
                    quote_state=QUOTE_STATUS["state"], top_blocker=_tb_row,
                    eligible_base=readiness.get("selector_eligible_base")),
                "Anchor": om.anchor_label(c.meta.get("anchor_source")),
                "Anchor Vol": f"{_av_row:,.0f}" if isinstance(_av_row, (int, float)) else "—",
                "Distance": (round(c.distance_from_spot, 1)
                             if isinstance(c.distance_from_spot, (int, float)) else None),
            }
            if not simple_mode:
                row.update({
                    "side": c.side,
                    "short K": c.short_strike,
                    "long K": c.long_strike,
                    "short b/a/m": _fmt_quote(short_leg),
                    "long b/a/m": _fmt_quote(long_leg),
                    "quote": quote_badge,
                    "width": round(c.max_risk + c.credit, 2),
                    "theoretical $": round(theoretical, 0),
                    "planned $": round(planned, 0),
                    "R:R": round(c.reward_risk, 2),
                    "b/a quality": round(c.meta.get("bid_ask_quality", 0.0), 2),
                    "b/a mode": c.meta.get("bid_ask_quality_mode") or "—",
                    "breakeven": round(c.breakeven, 2),
                    "gap": (round(c.score_gap_to_threshold, 3)
                            if c.score_gap_to_threshold is not None else None),
                    "edge": (round(c.score_edge, 4)
                             if isinstance(c.score_edge, (int, float)) else None),
                    "bucket": readiness["quote_quality_bucket"],
                    "bucket_reason": readiness["quote_quality_reason"],
                    "risk_type": readiness["risk_rejection_type"] or "—",
                    "rejection": c.rejection_type or ("rejected" if c.rejected else None),
                    "weak": "; ".join(c.weak_components),
                    "rejection_reasons": "; ".join(c.rejection_reasons),
                })
            rows.append(row)

        _sel_inputs = []
        for c in candidates:
            rd = c.meta.get("_readiness") or {}
            _sel_inputs.append({
                "side": c.side, "score": c.score, "credit": c.credit,
                "distance_from_spot": c.distance_from_spot,
                "short_strike": c.short_strike, "long_strike": c.long_strike,
                "rejected": c.rejected,
                "selector_eligible_base": rd.get("selector_eligible_base"),
                "candidate_passes_trade_filters": rd.get("candidate_passes_trade_filters"),
                "candidate_passes_risk_filters": rd.get("candidate_passes_risk_filters"),
                "candidate_passes_quote_filters": rd.get("candidate_passes_quote_filters"),
                "candidate_passes_score_threshold": rd.get("candidate_passes_score_threshold"),
                "candidate_passes_score_edge": rd.get("candidate_passes_score_edge"),
                "candidate_is_marginal": rd.get("candidate_is_marginal"),
                "quote_validation_passed": (c.meta.get("short_leg") or {}).get("validation_passed"),
                "quote_quality_bucket": rd.get("quote_quality_bucket"),
                "planned_stop_risk_pct": rd.get("planned_stop_risk_pct"),
            })
        _sel = select_daily_trade(
            _sel_inputs, SelectorConfig(mode=chosen_selector),
            gamma_regime=structure.exposures.gamma_regime,
        )
        for i, row in enumerate(rows):
            row["Selected"] = "✅" if _sel.per_row[i]["selected_trade"] else ""
        st.dataframe(rows, use_container_width=True, hide_index=True)

        # Phase 10C follow-up — when quotes are not usable (stale/blocked), the
        # selector result is PREVIEW-ONLY: never render it as a live green selection.
        _live_ok = QUOTE_STATUS["available"]
        if _sel.selected_trade:
            i = _sel.selected_indices[0]
            sc = candidates[i]
            _sel_txt = (f"{om.candidate_label(sc.side, sc.short_strike, sc.long_strike)} "
                        f"(score {sc.score:.2f}, credit {ch.fmt_money(sc.credit)}) — "
                        f"{_sel.per_row[i]['selector_reason']}")
            if _live_ok:
                st.success(f"Daily selector ({_sel.daily_selector_mode}): selected " + _sel_txt)
            else:
                st.warning("Preview Candidate (no live selection — quotes not usable): " + _sel_txt)
        else:
            st.info(
                f"Daily selector ({_sel.daily_selector_mode}): NO_TRADE — "
                f"{_sel.selector_no_trade_reason or _sel.selector_rejection_reason or 'no eligible candidate'}"
            )
        st.caption("Selection only — the daily selector never executes or submits orders.")

        st.caption("Click a candidate to inspect its full score breakdown.")
        for c in candidates:
            sel_badge = "✅ " if c.rejection_type == "selected" else ""
            gap_str = (
                f"gap {c.score_gap_to_threshold:+.4f}"
                if c.score_gap_to_threshold is not None else ""
            )
            _rd_c = c.meta.get("_readiness") or {}
            _status_c = om.candidate_status_label(
                rejected=c.rejected, risk_rejection_type=_rd_c.get("risk_rejection_type"),
                quote_state=QUOTE_STATUS["state"],
                top_blocker=QUOTE_STATUS["details"].get("top_blocker"),
                eligible_base=_rd_c.get("selector_eligible_base"))
            label = (
                f"{sel_badge}{om.candidate_label(c.side, c.short_strike, c.long_strike)}  ·  "
                f"score {c.score:.2f}  ·  credit {ch.fmt_money(c.credit)}  ·  {_status_c}"
                + (f"  ({gap_str})" if not simple_mode and gap_str else "")
            )
            with st.expander(label, expanded=(c.rejection_type == "selected")):
                if simple_mode:
                    _render_candidate_simple(c, _rd_c)
                else:
                    _render_candidate_advanced(c, _rd_c)

    decision = strat.select(candidates, params)
    # Phase 10C follow-up — never render a LIVE "Decision" when quotes are stale or
    # validation-blocked. A stale/blocked state shows a Preview Candidate + a plain
    # "Why not" line (it must NOT claim the side cleared selector/quote/risk gates).
    _dh = om.decision_headline(
        available=QUOTE_STATUS["available"], quote_state=QUOTE_STATUS["state"],
        top_blocker=QUOTE_STATUS["details"].get("top_blocker"))
    st.subheader(_dh["title"])
    if _dh["live"]:
        badge = {"TRADE_CALL_CREDIT": "success", "TRADE_PUT_CREDIT": "success",
                 "NO_TRADE": "warning"}
        getattr(st, badge.get(decision.decision, "info"))(om.decision_label(decision.decision))
        st.write(decision.explanation)
        st.caption(_dh["note"])
    else:
        st.warning(f"Preview Candidate — {om.decision_label(decision.decision)} (preview only)")
        st.caption(_dh["note"])


def _render_profile_info_card(info: dict, *, simple: bool = False) -> None:
    """Shared profile info card (Builder + Tester). Phase 9G — shows side policy,
    entry window, target time, threshold, TP/SL, selector mode + dynamic-exit
    status alongside the basics. Reads the pure ``om.profile_info_fields`` map."""
    if simple:
        grid = (
            "Profile", "Symbol", "Strategy", "Entry window",
            "Target time", "Target DTE", "Threshold", "Side policy",
            "Selector style", "Take profit (TP)", "Stop loss (SL)",
            "Risk profile", "Data source",
        )
    else:
        grid = (
            "Profile", "Profile ID", "Symbol", "Strategy",
            "Entry window", "Target time", "Target DTE", "Threshold",
            "Side policy", "Selector mode", "Take profit (TP)", "Stop loss (SL)",
            "Risk profile", "Data source",
        )
    cols = st.columns(4)
    for _i, _k in enumerate(grid):
        _v = info.get(_k)
        if simple and _k in ("Risk profile", "Selector mode", "Stop loss (SL)"):
            _v = om.friendly_enum_label(_v)
        cols[_i % 4].metric(_k, ui.dash(om.friendly_text(_v) if simple else _v))
    st.caption(f"**Dynamic exits:** {om.friendly_text(info.get('Dynamic exits'))}")
    st.caption(f"**Designed to test:** {om.friendly_text(info.get('Designed to test'))}")
    if simple:
        st.caption("Safety: local paper / no broker execution. Raw profile IDs and JSON are in Advanced.")
    else:
        st.caption(
            f"Enabled: `{info.get('Enabled')}`  ·  Safety: {info.get('Safety')}  ·  "
            "TP/SL shown is the preset's intent; the paper lifecycle currently applies "
            "the PAPER_* env values (per-profile wiring deferred).")


def _load_profile_context(profile_id: object) -> dict | None:
    pid = str(profile_id or "").strip()
    if not pid or pid in ("(none)", "(no profiles)", "all-main", "all", "—"):
        return None
    d, errs = pb.load_dict_for_edit(pid)
    return d if d and not errs else None


def _profile_contexts(profile_ids: list[str]) -> list[dict]:
    rows: list[dict] = []
    for pid in profile_ids:
        d = _load_profile_context(pid)
        if d:
            rows.append(d)
    return rows


def _render_strategy_synopsis(profile: object, *, context: str) -> None:
    st.markdown("**Strategy Synopsis**")
    st.info(om.strategy_synopsis(profile, context=context))
    if not simple_mode and profile:
        with st.expander("Strategy mechanics", expanded=False):
            for bullet in om.strategy_mechanics_bullets(profile):
                st.markdown(f"- {bullet}")


def render_strategy_builder() -> None:
    st.subheader("🧱 Zσ Strat Builder")
    st.info(
        "Build, save, and test local strategy profiles. Profiles define what to scan "
        "and how the selector chooses a candidate. No orders are placed."
    )
    st.caption("CONFIG / SELECTION ONLY — no execution, no orders, no broker calls.")

    summaries = pb.list_summaries()
    _ok_summaries = [s for s in summaries if s.get("ok")]
    valid_ids = [s["profile_id"] for s in _ok_summaries]
    _builder_summ = {s["profile_id"]: s for s in _ok_summaries}

    def _builder_label(pid: str) -> str:
        s = _builder_summ.get(pid, {})
        return om.profile_dropdown_label(pid, s.get("profile_name"), s.get("preset_kind"))

    # ── A. Preset strategy profiles + selected-profile info card ──
    # Phase 9I — Simple Mode shows only Main Strategies; a checkbox reveals
    # comparison + legacy. Advanced Mode shows all profiles.
    st.markdown("**Preset strategy profiles**")
    if simple_mode and valid_ids:
        _b_show_all = st.checkbox(
            "Show all saved profiles", value=False, key="builder_show_all",
            help="Off = only your Main Strategies. On = every saved profile "
                 "(comparison · research · custom).")
        _builder_options = om.simple_mode_profile_ids(_ok_summaries, show_all=_b_show_all) or valid_ids
    else:
        _builder_options = om.order_profiles_for_dropdown(valid_ids)
    sel_id = st.selectbox("Select a profile", options=_builder_options or ["(none)"],
                          format_func=_builder_label if _builder_options else str,
                          key="builder_select")
    sel_dict = None
    if valid_ids and sel_id in valid_ids:
        sel_dict, _serrs = pb.load_dict_for_edit(sel_id)
    if sel_dict:
        _render_strategy_synopsis(sel_dict, context="builder")
        _render_profile_info_card(om.profile_info_fields(sel_dict), simple=simple_mode)
    with st.expander("All profiles (table)", expanded=False):
        if summaries:
            st.dataframe(
                [{"profile_id": s.get("profile_id"), "name": s.get("profile_name"),
                  "strategy": s.get("strategy_id"), "selector": s.get("daily_selector"),
                  "dte": s.get("target_dte"), "enabled": s.get("enabled"),
                  "valid": s.get("ok"), "hash": s.get("profile_hash")} for s in summaries],
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("No profiles found in profiles/.")

    # ── B/C. Build or edit (clear buttons — no radio-first) ──
    st.markdown("**Build or edit**")
    bc = st.columns(3)
    if bc[0].button(om.BTN_NEW_PROFILE, key="builder_new"):
        _new = pb.new_template_dict("new_profile")
        _new["symbol"] = st.session_state.get("active_symbol", om.DEFAULT_SYMBOL)
        st.session_state["builder_dict"] = _new
        st.session_state.pop("builder_built", None)
        st.session_state.pop("builder_errors", None)
    if bc[1].button(om.BTN_EDIT_PROFILE, key="builder_edit", disabled=not sel_dict):
        st.session_state["builder_dict"] = sel_dict
        st.session_state.pop("builder_built", None)
        st.session_state.pop("builder_errors", None)
    if bc[2].button(om.BTN_CLONE_PROFILE, key="builder_clone", disabled=not sel_dict):
        d, errs = pb.clone_dict(sel_id, f"{sel_id}_copy")
        if d is not None:
            st.session_state["builder_dict"] = d
            st.session_state.pop("builder_built", None)
            st.session_state.pop("builder_errors", None)
        else:
            st.error("Clone failed: " + "; ".join(errs))
    if not sel_dict:
        st.caption("_Select a valid preset above to enable Edit / Clone._")

    base = st.session_state.get("builder_dict")
    if not base:
        st.info("Create a strategy recipe by choosing its entry window, side policy, selector, TP, and SL.")
        st.info("Choose **Create new profile**, or pick a preset above and **Edit** / **Clone** it.")
        return
    _render_strategy_synopsis(base, context="builder")

    def _show_result_and_save() -> None:
        built = st.session_state.get("builder_built")
        errs = st.session_state.get("builder_errors")
        if built is None:
            return
        if errs:
            st.error("Validation failed — fix these before saving:")
            for e in errs:
                st.markdown(f"- `{e}`")
        else:
            st.success(f"Valid ✓  ·  deterministic profile hash: `{pb.hash_for(built)}`")
            st.json(built, expanded=False)
        st.markdown("**Save**")
        sc = st.columns([2, 1])
        overwrite = sc[0].checkbox(
            "Overwrite existing profile", value=False, key="builder_overwrite",
            help="Required to replace a profile file that already exists.")
        target = pb.resolve_profile_target(str(built.get("profile_id") or "new_profile"))
        sc[0].caption(f"saves to: `{target}`")
        if sc[1].button("💾 Save profile", type="primary", key="builder_save", disabled=bool(errs)):
            ok, msg, phash = pb.save_profile(built, overwrite=overwrite)
            if ok:
                st.success(f"Saved ✓  {msg}  ·  hash `{phash}`")
                st.session_state.pop("builder_built", None)
                st.session_state.pop("builder_errors", None)
            else:
                st.error(msg)

    # ── Simple Mode: compact builder mapping to existing profile fields ──
    if simple_mode:
        _cur_id = str(base.get("profile_id") or "my_strategy")
        if base.get("daily_selector") == "no_trade":
            _cur_side = "Observe only"
        elif base.get("allow_call_credit", True) and not base.get("allow_put_credit", True):
            _cur_side = "Calls only"
        elif base.get("allow_put_credit", True) and not base.get("allow_call_credit", True):
            _cur_side = "Puts only"
        else:
            _cur_side = "Both sides"
        with st.form("simple_profile_editor"):
            r1 = st.columns(2)
            s_name = r1[0].text_input(
                "Profile name", value=base.get("profile_name") or _cur_id.replace("_", " ").title())
            s_symbol = r1[1].text_input(
                "Ticker / symbol",
                value=base.get("symbol") or st.session_state.get("active_symbol", om.DEFAULT_SYMBOL),
                help="SPX has full ZerσSigma exposure coverage; other tickers may be "
                     "Tasty market-data only.")
            r2 = st.columns(2)
            s_type = r2[0].text_input("Strategy type",
                                      value=base.get("strategy_type") or "vertical_credit_spread")
            s_dte = r2[1].number_input("Target DTE", value=int(base.get("target_dte") or 0),
                                       step=1, min_value=0)
            r3 = st.columns(2)
            s_side = r3[0].radio("Side preference", list(om.SIDE_PREFERENCES),
                                 index=list(om.SIDE_PREFERENCES).index(_cur_side))
            s_style = r3[1].radio(
                "Selector style", list(om.SELECTOR_STYLES),
                index=list(om.SELECTOR_STYLES).index(
                    om.selector_to_style(base.get("daily_selector") or "score_best_valid")))
            r4 = st.columns(2)
            # Phase 10C — this radio sets the profile's OWN default source. The
            # confusing part was it read "Data source: Sandbox" even when the app
            # runs Live. Relabel it + show the CURRENT app run source + warn on
            # mismatch (the app source wins for live previews + paper tests).
            _profile_ds = om.providers_to_data_source(
                base.get("structure_provider") or "stub", base.get("quote_provider") or "mock")
            _ds_idx = list(om.DATA_SOURCES).index(_profile_ds)
            s_ds = r4[0].radio(
                "Profile default data source", list(om.DATA_SOURCES), index=_ds_idx,
                help="The profile's OWN default. Live previews + paper tests use the APP "
                     "data source (top controls) unless you run the profile on its own "
                     "source in Advanced Mode.")
            _app_ds_now = om.providers_to_data_source(chosen_structure, chosen_quote)
            r4[0].caption(
                f"Current run source: **{om.data_source_short(_app_ds_now)}** · "
                f"Structure: {om.exposure_engine_label(chosen_structure)} · "
                f"Quotes: {om.market_data_engine_label(chosen_quote)}")
            if om.data_source_short(_app_ds_now) != om.data_source_short(_profile_ds):
                r4[0].caption(
                    f"⚠ This profile was created with {om.data_source_short(_profile_ds)} "
                    f"defaults, but the current app source is {om.data_source_short(_app_ds_now)}. "
                    "Live previews + paper tests use the app source.")
            s_risk = r4[1].selectbox(
                "Risk profile", options=profile_names,
                index=(profile_names.index(base.get("risk_profile"))
                       if base.get("risk_profile") in profile_names else 0))
            # ── Phase 9G — Simple-Mode exit management (SL 150/200/custom, TP None/50/75/custom) ──
            st.markdown("_Exit management_")
            r5 = st.columns(2)
            _sl_labels = [lbl for lbl, _ in pb.STOP_LOSS_PRESETS]
            _cur_sl = base.get("stop_loss_pct")
            _sl_idx = 0 if _cur_sl in (None, 1.5) else (1 if _cur_sl == 2.0 else 2)
            s_sl_choice = r5[0].radio("Stop loss", _sl_labels, index=_sl_idx,
                                      help="Stop at this multiple of the credit. 150% / 200% / custom.")
            s_sl_custom = r5[0].number_input(
                "Custom SL (× credit)", value=float(_cur_sl) if _cur_sl else 1.50,
                step=0.25, min_value=0.0, help="Used only when Stop loss = Custom.")
            _tp_labels = [lbl for lbl, _ in pb.TAKE_PROFIT_PRESETS]
            _cur_tp = base.get("take_profit_pct")
            _tp_idx = (0 if _cur_tp is None else 1 if _cur_tp == 0.5
                       else 2 if _cur_tp == 0.75 else 3)
            s_tp_choice = r5[1].radio("Take profit", _tp_labels, index=_tp_idx,
                                      help="Take profit at this fraction of credit, or None.")
            s_tp_custom = r5[1].number_input(
                "Custom TP (fraction of credit)", value=float(_cur_tp) if _cur_tp else 0.50,
                step=0.05, min_value=0.0, max_value=1.0, help="Used only when Take profit = Custom.")
            # Phase 10C — "Validate strategy" was unclear. Rename to "Check Strategy
            # Setup" + explain what it does (it never runs or trades).
            st.caption("**Check Strategy Setup** validates profile fields, side rules, DTE, "
                       "TP/SL, data-source compatibility, and required parameters. It does "
                       "not run or trade — it only checks the profile before you save.")
            s_validate = st.form_submit_button(om.BTN_VALIDATE)
        try:
            from src.paper.models import PaperLifecycleConfig
            _plc = PaperLifecycleConfig.from_env()
            st.caption(
                "Your profile's TP/SL is saved as metadata. The paper lifecycle still "
                "applies the PAPER_* env values at test time (per-profile TP/SL wiring is "
                f"deferred): contracts={_plc.contracts} · TP={_plc.take_profit_pct} · "
                f"SL={_plc.stop_loss_pct} · EOD={_plc.eod_exit_time if _plc.exit_on_eod else 'off'}")
        except Exception:
            pass
        if s_validate:
            _pid = _cur_id if _cur_id not in ("", "new_profile") else (
                "".join((c if c.isalnum() else "_") for c in s_name.lower()).strip("_")
                or "my_strategy")
            _sl_map = dict(pb.STOP_LOSS_PRESETS)
            _tp_map = dict(pb.TAKE_PROFIT_PRESETS)
            _sl_pct = _sl_map.get(s_sl_choice) if s_sl_choice != "Custom" else float(s_sl_custom)
            _tp_pct = (float(s_tp_custom) if s_tp_choice == "Custom"
                       else _tp_map.get(s_tp_choice))
            vals = {
                "profile_id": _pid, "profile_name": s_name,
                "symbol": om.normalize_symbol(s_symbol), "strategy_type": s_type,
                # Phase 10C — preserve the profile's own 'Show in main strategy list'
                # flag instead of silently forcing it True on every Simple save.
                "target_dte": int(s_dte), "risk_profile": s_risk,
                "enabled": bool(base.get("enabled", False)),
                "stop_loss_pct": _sl_pct, "stop_loss_mode": "fixed_credit_multiple",
                "take_profit_pct": _tp_pct,
                "take_profit_mode": "none" if _tp_pct is None else "credit_capture",
                **om.build_simple_fields(side_preference=s_side, selector_style=s_style),
                **om.data_source_to_providers(s_ds),
            }
            built = pb.build_profile_dict(vals, base=base)
            st.session_state["builder_dict"] = built
            st.session_state["builder_built"] = built
            st.session_state["builder_errors"] = pb.validate_dict(built)
        _show_result_and_save()
        return

    # ── Advanced Mode: full detailed form (basics + advanced expanders) ──
    st.markdown(f"**Editing `{base.get('profile_id', '?')}`** — basics shown; advanced behind expanders")
    vals: dict = {}

    def _field(col, f) -> None:  # type: ignore[no-untyped-def]
        name, kind = f["name"], f["kind"]
        cur = base.get(name)
        wkey = f"builder_f_{name}"
        fhelp = f.get("help")
        if kind == "bool":
            vals[name] = col.checkbox(f["label"], value=bool(cur), key=wkey, help=fhelp)
        elif kind == "int":
            vals[name] = col.number_input(
                f["label"], value=int(cur) if isinstance(cur, (int, float)) else 0,
                step=1, key=wkey, help=fhelp)
        elif kind == "select":
            opts = f.get("options") or []
            idx0 = opts.index(cur) if cur in opts else 0
            vals[name] = col.selectbox(f["label"], options=opts, index=idx0, key=wkey, help=fhelp)
        elif kind == "optfloat":
            vals[name] = col.text_input(
                f["label"], value="" if cur is None else str(cur), key=wkey,
                help=fhelp or "number, or blank for none")
        else:  # opttext / str
            vals[name] = col.text_input(
                f["label"], value="" if cur is None else str(cur), key=wkey, help=fhelp)

    with st.form("profile_editor"):
        st.markdown("_Basics_")
        _bcols = st.columns(2)
        for _i, _f in enumerate(pb.basic_fields()):
            _field(_bcols[_i % 2], _f)
        for _group in pb.ADVANCED_GROUPS:
            _gf = pb.advanced_group_fields(_group)
            if not _gf:
                continue
            with st.expander(_group, expanded=False):
                _acols = st.columns(2)
                for _i, _f in enumerate(_gf):
                    _field(_acols[_i % 2], _f)
        validated = st.form_submit_button("Validate & compute hash")

    if validated:
        built = pb.build_profile_dict(vals, base=base)
        st.session_state["builder_dict"] = built
        st.session_state["builder_built"] = built
        st.session_state["builder_errors"] = pb.validate_dict(built)
    _show_result_and_save()


def render_forward_runner() -> None:
    st.subheader("🧪 Run Strategy — local paper test")
    st.caption("Zσ Strat Tester · run a saved strategy as a local paper test.")
    st.markdown(ui.pill(control_ui.EXECUTION_BANNER, "green"), unsafe_allow_html=True)
    # Phase 10B — prominent "Run a Strategy" step panel (Simple-Mode call-to-action).
    st.markdown("#### ▶ Run a Strategy")
    st.markdown(
        "**1. Choose strategy** (dropdown below) → **2. Confirm data source** (Live / Sandbox) → "
        "**3. Preview Strategy** (one paper tick) → **4. Start Paper Test** → "
        "**5. Stop Test / Review Latest**"
    )
    st.caption("Paper test only. No broker execution. No order preview.")
    st.divider()
    # Phase 9E — active profile / symbol / data source context.
    _tester_prof = next((r.profile for r in list_run_profiles()
                         if r.ok and r.profile and r.profile.profile_id == chosen_profile_id), None)
    _tc = st.columns(3)
    _tc[0].metric("Active profile", om.active_profile_display(chosen_profile_id))
    _tc[1].metric("Symbol", (_tester_prof.symbol if _tester_prof else SYMBOL))
    # Phase 9I — this is the APP data source (top controls). The resolved
    # source for the actual run is shown in the "Data source for this run" panel
    # below (which reconciles app vs profile and warns on mismatch).
    _tc[2].metric("App data source",
                  om.data_source_short(om.providers_to_data_source(chosen_structure, chosen_quote)))
    # Phase 10C — after-hours DTE preview note for the SELECTED profile. The
    # profile's own target DTE is shown unchanged; only the live PREVIEW rolls.
    _run_profile_dte = (_tester_prof.target_dte
                        if (_tester_prof and _tester_prof.target_dte is not None) else 0)
    if om.after_hours_preview_active(now_et(), _run_profile_dte):
        st.info("🌙 " + om.after_hours_preview_banner(
            (_tester_prof.symbol if _tester_prof else SYMBOL), _run_profile_dte))
        _pc = st.columns(2)
        _pc[0].metric("Profile DTE", om.dte_label(_run_profile_dte))
        _pc[1].metric("Quote chain", f"{om.dte_label(om.resolve_preview_dte(now_et(), _run_profile_dte))} after-hours preview")
        st.caption(f"Strategy DTE unchanged. Preview-only: the paper test still runs your "
                   f"profile's own DTE ({om.dte_label(_run_profile_dte)}) unless you "
                   "explicitly change it in the profile.")

    _hb = forward_review.load_latest_heartbeat() or {}
    view = control_ui.status_view(control_ui.get_status())
    # Friendly "Latest test run" label; the full run_id lives in Advanced details.
    _latest_run_label = om.friendly_run_label(
        run_id=view["run_id"] or _hb.get("run_id"),
        profile_name=(_tester_prof.profile_name if _tester_prof else None),
        strategy_id=(_tester_prof.strategy_id if _tester_prof else None),
        started_at=_hb.get("started_at") or _hb.get("latest_tick_time"))
    if simple_mode:
        # Phase 10C — Simple Mode says "Test Status" (never "Runner"/PID/run_id);
        # shows a friendly state + Running Yes/No + the latest test run label.
        mcols = st.columns(3)
        mcols[0].metric("Test Status", om.test_status_label(view["status"]))
        mcols[1].metric("Active paper test", om.running_display(view["active"]))
        mcols[2].metric("Latest test run", _latest_run_label)
    else:
        mcols = st.columns(4)
        mcols[0].metric("Test Status", om.test_status_label(view["status"]))
        mcols[1].metric("Active paper test", om.running_display(view["active"]))
        mcols[2].metric("PID", str(view["pid"] or "—"))
        mcols[3].metric("Latest test run", _latest_run_label)
    if view["stale"]:
        st.warning("Local paper test state is STALE (process not alive). Use "
                   "**🧹 Clear stale test** below.")
    if view["active"] or view.get("status") in ("running", "starting", "stopping"):
        st.warning("⚠ " + om.runner_busy_message(
            view.get("profile_id") or chosen_profile_id, view.get("status")))
    # Phase 9D — latest decision + open paper P&L at a glance.
    _rs_hb = _hb
    _rs_ps = portfolio_ledger.load_summary("latest") or {}
    _rs_sel = (f"{om.decision_label(_rs_hb.get('latest_decision'))} (selected)"
               if _rs_hb.get("selected_trade")
               else om.decision_label(_rs_hb.get("latest_decision")))
    st.caption(
        f"Latest decision: **{_rs_sel}**  ·  open paper trades: "
        f"**{_rs_ps.get('open_trade_count', 0)}**  ·  realized P&L: "
        f"**{ch.fmt_money(_rs_ps.get('realized_pnl', 0.0))}**  ·  total P&L: "
        f"**{ch.fmt_money(_rs_ps.get('total_pnl', 0.0))}**"
    )

    _all_summaries = pb.list_summaries()
    _runner_summaries = {s["profile_id"]: s for s in _all_summaries if s.get("ok")}
    _summaries_list = list(_runner_summaries.values())
    _all_runner_profiles = om.order_profiles_for_dropdown(list(_runner_summaries))
    # Phase 10C follow-up — invalid profiles are not runnable, but never let them
    # silently disappear: surface their ids + a "fix in Builder" pointer.
    _invalid_ids = [s.get("profile_id") for s in _all_summaries if not s.get("ok")]
    if _invalid_ids:
        st.caption(f"⚠ {len(_invalid_ids)} saved profile(s) have validation errors and are "
                   f"hidden here: {', '.join(str(i) for i in _invalid_ids)}. Fix them in the "
                   "🧱 Zσ Strat Builder (the 'All profiles' table shows the reasons).")

    def _runner_label(pid: str) -> str:
        s = _runner_summaries.get(pid, {})
        return om.profile_dropdown_label(pid, s.get("profile_name"), s.get("preset_kind"))

    # ── Phase 9I/10C — Simple Mode shows ONLY Main Strategies by default; a checkbox
    # reveals every saved profile incl. CUSTOM ones Dan builds. Advanced = all. ──
    if simple_mode:
        _show_all = st.checkbox(
            "Show all saved profiles", value=False, key="runner_show_all",
            help="Off = only your Main Strategies. On = every saved profile you've made "
                 "(comparison · research · custom).")
        runner_profiles = (om.simple_mode_profile_ids(_summaries_list, show_all=_show_all)
                           or _all_runner_profiles)
    else:
        runner_profiles = _all_runner_profiles

    rc = st.columns([2, 1, 1]) if simple_mode else st.columns([2, 1, 1, 1])
    sel_profile = rc[0].selectbox(
        "Strategy profile", options=runner_profiles or ["(no profiles)"],
        format_func=_runner_label if runner_profiles else str, key="runner_profile")
    interval = rc[1].number_input(
        "Scan every (seconds)", value=60.0, step=10.0, min_value=0.0, key="runner_interval",
        help="How often the local paper tester checks for a new signal.")
    once = rc[2].checkbox("Single scan", value=False, key="runner_once",
                          help="Run exactly one scan, then stop.")
    if simple_mode:
        max_ticks = 0   # 'Stop after scans' is an Advanced-only control
    else:
        max_ticks = rc[3].number_input(
            "Stop after scans", value=0, step=1, min_value=0, key="runner_max_ticks",
            help="Stop automatically after this many scans (0 = run until you stop it).")
    st.caption(f"Scan every: **{int(interval)} seconds**"
               + ("  ·  single scan then stop" if once else ""))
    market_hours_only = st.checkbox("Market hours only (RTH)", value=False, key="runner_mho")
    # ── Selected profile (Phase 9H section) ──
    _sel_known = bool(runner_profiles and sel_profile in _runner_summaries)
    _sel_runner_dict = None
    if _sel_known:
        st.markdown(f"**Selected profile:** {_runner_label(sel_profile)}")
        _sel_runner_dict, _ = pb.load_dict_for_edit(sel_profile)
        if _sel_runner_dict:
            _render_strategy_synopsis(_sel_runner_dict, context="run")
            with st.expander("Selected profile details", expanded=simple_mode):
                _render_profile_info_card(
                    om.profile_info_fields(_sel_runner_dict), simple=simple_mode)

    # ── Phase 9I — App vs Profile data source for THIS run (never silently mismatch) ──
    _ovr_struct = _ovr_quote = None
    _run_structure_name, _run_quote_name = resolved_structure_name, resolved_quote_name
    if _sel_known and _sel_runner_dict:
        _app_ds = om.providers_to_data_source(chosen_structure, chosen_quote)
        # Simple Mode: app data source wins by default (explicit, see caption).
        # Advanced Mode: an explicit toggle lets the profile's own source win.
        _prefer = om.RUN_SOURCE_APP if simple_mode else st.radio(
            "Data source for this run", list(om.RUN_SOURCE_MODES), index=0,
            horizontal=True, key="runner_src_prefer",
            help="Which source wins when the app controls and the profile disagree.")
        _run = om.resolve_run_source(
            _app_ds, _sel_runner_dict.get("structure_provider"),
            _sel_runner_dict.get("quote_provider"), prefer=_prefer)
        _run_structure_name = _run["structure_provider"]
        _run_quote_name = _run["quote_provider"]
        _status = om.run_source_status(chain_available=chain is not None,
                                       mismatch=_run["mismatch"])
        _badge = {"ready": "✅ ready", "warning": "⚠ warning", "unavailable": "⛔ unavailable"}
        _sc = st.columns(4)
        _sc[0].metric("Data source", _run["data_source"])
        _sc[1].metric("Exposure source", _run["exposure_label"])
        _sc[2].metric("Market data source", _run["market_data_label"])
        _sc[3].metric("Status", _badge[_status])
        if _run["mismatch"]:
            st.warning("⚠ " + _run["message"])
            if simple_mode:
                st.caption("Simple Mode runs this test on the **app** data source. Switch to "
                           "Advanced Mode to run on the profile's own source instead.")
        # Pass provider overrides ONLY when the app source wins (else profile decides).
        if _run["winner"] == "app":
            _ovr_struct, _ovr_quote = _run["structure_provider"], _run["quote_provider"]

    # ── Latest completed test + mismatch warning (Phase 9H) ──
    _latest_man = forward_review.load_latest_manifest() or {}
    _latest_pid = _latest_man.get("profile_id")
    _latest_pname = _latest_man.get("profile_name") or _latest_pid or "—"
    st.markdown("**Latest completed test**")
    if _latest_pid:
        st.caption(f"Profile: `{_latest_pname}`  ·  status: {_latest_man.get('status') or '—'}  ·  "
                   f"{_latest_run_label}")
    else:
        st.caption("No completed local paper test yet.")
    _mismatch = om.run_profile_mismatch(sel_profile if _sel_known else None, _latest_pid)
    if _mismatch["mismatch"]:
        st.warning("⚠ " + _mismatch["message"])

    can, why = control_ui.can_start(control_ui.get_status())
    _tb = QUOTE_STATUS["details"].get("top_blocker")
    _run_sandbox = om.is_sandbox(_run_structure_name, _run_quote_name)
    _profile_dte = (
        _sel_runner_dict.get("target_dte")
        if _sel_runner_dict else _run_profile_dte
    )
    _quote_chain_dte = om.quote_chain_dte(getattr(chain, "expiry", None), now_et())
    _local_paper_mode = om.local_paper_execution_mode(CFG.providers.execution_active)
    _readiness = om.paper_test_readiness(
        runner_can_start=can,
        runner_reason=om.humanize_runner_message(why),
        selected_profile_valid=bool(_sel_known and _sel_runner_dict),
        local_paper_mode=_local_paper_mode,
        structure_available=bool(structure is not None and structure_error is None)
        or _run_structure_name == "stub",
        required_strikes=quote_request.required_strikes,
        quote_state=QUOTE_STATUS["state"],
        top_blocker=_tb,
        sandbox=_run_sandbox,
        profile_dte=_profile_dte,
        quote_chain_dte=_quote_chain_dte,
    )
    _can_start = bool(_readiness["can_start"])
    st.markdown("**Paper-test readiness**")
    _ready_cards = st.columns(5)
    _ready_cards[0].metric("Profile DTE", om.dte_label(_profile_dte))
    _ready_cards[1].metric(
        "Quote Chain DTE",
        om.dte_label(_quote_chain_dte if _quote_chain_dte is not None else PREVIEW_DTE),
    )
    _ready_cards[2].metric(
        "Required Strikes", str(_readiness["required_strike_count"])
    )
    _ready_cards[3].metric(
        "Quote Provider Status",
        f"Quotes: {_readiness['quote_label']}",
    )
    _ready_cards[4].metric(
        "Start Paper Test", "Enabled" if _can_start else "Disabled"
    )
    st.caption(
        "Required strikes: "
        + (
            ", ".join(f"{float(strike):g}" for strike in quote_request.required_strikes)
            if quote_request.required_strikes else "none"
        )
    )
    if _can_start:
        st.success(_readiness["reason"])
    else:
        st.warning(f"Cannot start paper test: {_readiness['reason']}")
        st.info(
            "Next action: " + om.readiness_next_action(
                reason=_readiness["reason"],
                quote_state=QUOTE_STATUS["state"],
                top_blocker=_tb,
                structure_available=bool(structure is not None and structure_error is None)
                or _run_structure_name == "stub",
            )
        )
        if _readiness["preview_only"]:
            st.info(
                "Preview only — cannot start live paper test until quotes are fresh/usable."
            )
    _tasty_authenticated = (
        True if QUOTE_STATUS["details"].get("chain_returned") else (
            False if QUOTE_STATUS["state"] in ("not_configured", "auth_failed") else None
        )
    )
    _startup_rows = om.morning_startup_checklist(
        app_source_live=not _run_sandbox,
        symbol=SYMBOL,
        structure_available=bool(structure is not None and structure_error is None)
        or _run_structure_name == "stub",
        tasty_configured=ch.tasty_configured(),
        tasty_authenticated=_tasty_authenticated,
        selected_profile_id=sel_profile if _sel_known else None,
        profile_dte=_profile_dte,
        quote_chain_dte=_quote_chain_dte,
        required_strikes=quote_request.required_strikes,
        quote_state=QUOTE_STATUS["state"],
        top_blocker=_tb,
        start_enabled=_can_start,
        local_paper_only=_local_paper_mode,
    )
    st.markdown("**Morning Startup Checklist**")
    st.dataframe(_startup_rows, width="stretch", hide_index=True)
    from src.backtesting import forward_readiness as _FR

    _candidate_report = _FR.load_forward_readiness()
    st.markdown("**This week's forward-paper candidates**")
    _candidate_cols = st.columns(2)
    for _idx, _card in enumerate((_candidate_report.get("profiles") or [])[:2]):
        with _candidate_cols[_idx % 2].container(border=True):
            st.markdown(f"**{_card.get('profile_name') or _card.get('profile_id')}**")
            st.caption(f"{_card.get('role') or 'Benchmark'} · not production-approved")
            st.write(_card.get("why_included") or "Forward-paper benchmark.")
            st.caption(
                f"Start: ${float(_card.get('starting_account_suggestion') or 10000):,.0f} "
                f"/ {int(_card.get('contracts') or 1)} contract"
            )
            st.caption("Watch live: " + "; ".join(_card.get("what_to_watch_live") or []))
    with st.expander("Advanced — RTH diagnostics commands", expanded=False):
        for _cmd in om.rth_diagnostic_commands(SYMBOL, sel_profile, _profile_dte):
            st.code(_cmd, language="powershell")
        st.caption("Read-only diagnostics. No secrets, no broker execution, no order preview.")
    with st.expander("EOD Review Checklist", expanded=False):
        for _item in om.eod_review_checklist():
            st.markdown(f"- {_item}")
    bcols = st.columns(6)
    if bcols[0].button(om.BTN_REFRESH, key="runner_refresh"):
        st.rerun()
    if bcols[1].button(om.BTN_PREVIEW, disabled=not runner_profiles or not can,
                       key="runner_preview",
                       help="Runs a single local paper-test tick (no broker)."):
        ok, msg, pid = control_ui.start_runner(
            sel_profile, once=True, market_hours_only=bool(market_hours_only),
            structure_provider=_ovr_struct, quote_provider=_ovr_quote)
        _pv = "Preview launched (preview-only — live quotes are blocked). " if (
            ok and _readiness["preview_only"]
        ) \
            else ("Preview launched. " if ok else "")
        (st.success if ok else st.error)(_pv + str(msg) + (f" (pid {pid})" if pid else ""))
        if ok:
            st.rerun()
    if bcols[2].button(om.BTN_START_TEST, type="primary",
                       disabled=not runner_profiles or not _can_start, key="runner_start"):
        ok, msg, pid = control_ui.start_runner(
            sel_profile, interval_seconds=float(interval), once=bool(once),
            max_ticks=(int(max_ticks) or None), market_hours_only=bool(market_hours_only),
            structure_provider=_ovr_struct, quote_provider=_ovr_quote)
        (st.success if ok else st.error)(f"{msg}" + (f" (pid {pid})" if pid else ""))
        if ok:
            st.rerun()
    if bcols[3].button(om.BTN_STOP_TEST, key="runner_stop"):
        ok, msg = control_ui.stop_runner(force=bool(st.session_state.get("runner_force", False)))
        (st.success if ok else st.warning)(msg)
        st.rerun()
    if bcols[4].button(om.BTN_REVIEW, key="runner_review"):
        st.info("Latest test review is shown below ↓ (read-only — no broker, no orders).")
    if bcols[5].button(om.BTN_CLEAR_STALE, key="runner_cleanup"):
        ok, msg = control_ui.cleanup()
        (st.success if ok else st.warning)(msg)
        st.rerun()
    if not can:
        st.caption(f"_Start / Preview disabled: {om.humanize_runner_message(why)}_")
    # Phase 10C — force-stop terminates the stored OS process; it is an
    # Advanced-only affordance. In Simple Mode it stays hidden (force=False).
    if not simple_mode:
        st.checkbox(
            om.BTN_FORCE_STOP + " — use only if a graceful stop fails",
            value=False, key="runner_force",
        )

    # Phase 9I — terminal commands are an ADVANCED-only affordance (Simple Mode
    # is button-driven only; full run id + PID live here too).
    if not simple_mode:
        with st.expander("Advanced details / terminal commands", expanded=False):
            st.caption(
                f"Full run id: `{view['run_id'] or _hb.get('run_id') or '—'}`  ·  "
                f"PID: `{view['pid'] or '—'}`  ·  scan interval: `{int(interval)}s`  ·  "
                f"stop after scans: `{int(max_ticks) or '∞'}`  ·  "
                f"status: `{view['status']}`")
            _ctl_profile = sel_profile if runner_profiles else "vertical_wing_score_best_1dte"
            st.caption("Exact command (copy into a terminal — equivalent to the buttons):")
            st.code(
                control_ui.safe_command(
                    _ctl_profile, interval_seconds=float(interval), once=bool(once),
                    market_hours_only=bool(market_hours_only),
                    structure_provider=_ovr_struct, quote_provider=_ovr_quote) + "\n"
                "python -m scripts.control_forward stop\n"
                "python -m scripts.control_forward cleanup-stale\n"
                "python -m scripts.review_forward --latest",
                language="powershell",
            )

    st.divider()
    st.markdown("**Strategy test review (read-only)**")
    _fwd_runs = forward_review.discover_runs()
    _fwd_hb = forward_review.load_latest_heartbeat()
    if _fwd_hb:
        st.caption(
            f"**Latest heartbeat** — run `{_fwd_hb.get('run_id', '—')}` · "
            f"status={_fwd_hb.get('status', '—')} · tick {_fwd_hb.get('tick_id', '—')} @ "
            f"{_fwd_hb.get('latest_tick_time', '—')} · "
            f"decision={_fwd_hb.get('latest_decision', '—')} · "
            f"selected_trade={_fwd_hb.get('selected_trade', False)}"
        )
    if not _fwd_runs:
        st.info("No forward runs yet. Start one above (or via the copy commands).")
    else:
        _run_ids = [p.name for p in _fwd_runs]
        _chosen_run = st.selectbox("Run", options=_run_ids, index=0, key="fwd_run_sel",
                                   help="Discovered forward runs (newest first).")
        _summary = forward_review.summarize_run(_chosen_run)
        if _summary is None:
            st.warning(f"Could not summarize run {_chosen_run}.")
        else:
            m = st.columns(5)
            m[0].metric("Ticks", _summary["tick_count"])
            m[1].metric("Signals", _summary["signal_count"])
            m[2].metric("Dup signals", _summary["duplicate_signal_count"])
            m[3].metric("No-trade", _summary["no_trade_count"])
            m[4].metric("Errors", _summary["error_count"])
            st.caption(
                f"status=`{_summary.get('status')}` · profile=`{_summary.get('profile_id')}` "
                f"(hash `{_summary.get('profile_hash')}`) · selector=`{_summary.get('daily_selector')}` · "
                f"target_dte={_summary.get('target_dte')} · quotes=`{_summary.get('quote_provider')}` · "
                f"no_execution={_summary.get('no_execution', True)}"
            )
            st.caption(
                f"started={_summary.get('started_at')} → ended={_summary.get('ended_at')} · "
                f"latest_decision={_summary.get('latest_decision')} · "
                f"latest_selected_trade={_summary.get('latest_selected_trade')}"
            )
            st.caption(f"run folder: `{_summary.get('run_path')}`")
            _sigs = forward_review.load_signal_log(_chosen_run)
            if _sigs:
                st.markdown("**Selected signals**")
                st.dataframe(_sigs, use_container_width=True, hide_index=True)
            _nts = forward_review.load_no_trade_log(_chosen_run)
            if _nts:
                st.markdown("**No-trade reasons**")
                st.dataframe(_nts[-25:], use_container_width=True, hide_index=True)
            _ticks = forward_review.load_tick_log(_chosen_run)
            if _ticks:
                st.markdown("**Tick log (latest 25)**")
                st.dataframe(
                    [{k: t.get(k) for k in ("tick_id", "status", "scanner_return_code",
                                             "post_selector_decision", "selected_trade",
                                             "duplicate_selected_signal", "selector_no_trade_reason",
                                             "tick_finished_at")}
                     for t in _ticks[-25:]],
                    use_container_width=True, hide_index=True,
                )


def render_portfolio() -> None:
    st.subheader("💼 Zσ Paper Portfolio")
    st.markdown(ui.pill("LOCAL PAPER ACCOUNTING ONLY — NO BROKER EXECUTION", "green"),
                unsafe_allow_html=True)
    st.caption(
        "Open/closed paper trades + P&L from your strategy test runs (TP / SL / EOD "
        "exits across profiles). No broker orders, no order preview, no live execution."
    )
    _pf_man = portfolio_ledger.load_manifest("latest")
    if not _pf_man:
        st.info(
            "**No portfolio run yet.** To populate open paper trades + P&L here:\n\n"
            "1. Build/enable a profile in the **Strategy Builder** tab.\n"
            "2. Run a paper portfolio (commands below), or start a monitor from the "
            "**Run Strategy** tab.\n"
            "3. A selected signal opens a simulated paper trade; TP / SL / EOD exits close it."
        )
    else:
        _pf_summ = portfolio_ledger.load_summary("latest") or {}
        st.caption(
            f"latest: {_pf_man.get('portfolio_run_id')}  ·  status={_pf_man.get('status')}  ·  "
            f"profiles={', '.join(_pf_man.get('profiles') or [])}"
        )
        _pc = st.columns(5)
        _pc[0].metric("Open", _pf_summ.get("open_trade_count", 0))
        _pc[1].metric("Closed", _pf_summ.get("closed_trade_count", 0))
        _pc[2].metric("Realized P&L", ui.fmt_money(_pf_summ.get("realized_pnl", 0.0)))
        _pc[3].metric("Unrealized P&L", ui.fmt_money(_pf_summ.get("unrealized_pnl", 0.0)))
        _pc[4].metric("Total P&L", ui.fmt_money(_pf_summ.get("total_pnl", 0.0)))
        _pc2 = st.columns(4)
        _pc2[0].metric("Wins", _pf_summ.get("wins", 0))
        _pc2[1].metric("Losses", _pf_summ.get("losses", 0))
        _pc2[2].metric("Dup skipped", _pf_summ.get("duplicate_skipped_count", 0))
        _pc2[3].metric("Blocked", _pf_summ.get("blocked_by_limits_count", 0))

        _pf_cols = ("paper_trade_id", "profile_id", "side", "short_strike", "long_strike",
                    "entry_credit", "current_mark", "unrealized_pnl", "realized_pnl",
                    "exit_reason", "ticks_held")
        _open_rows = portfolio_ledger.load_open_trades("latest")
        _closed_rows = portfolio_ledger.load_closed_trades("latest")
        _pf_profile_ids = set(_pf_man.get("profiles") or [])
        _pf_profile_ids.update(str(r.get("profile_id")) for r in (_open_rows + _closed_rows)
                               if r.get("profile_id"))
        _pf_profile_rows = _profile_contexts(om.order_profiles_for_dropdown(list(_pf_profile_ids)))
        if _pf_profile_rows:
            with st.expander("Strategy context", expanded=False):
                st.info(om.multi_strategy_synopsis(_pf_profile_rows, context="portfolio"))
                for _p in _pf_profile_rows:
                    st.caption(om.strategy_one_line(_p))
        st.markdown("**Open paper trades & unrealized P&L**")
        if _open_rows:
            st.dataframe([{k: r.get(k) for k in _pf_cols} for r in _open_rows],
                         use_container_width=True, hide_index=True)
        else:
            st.caption("No open paper trades. Start a portfolio forward run or wait for a selected signal.")
        if _closed_rows:
            st.markdown("**Closed paper trades**")
            st.dataframe([{k: r.get(k) for k in _pf_cols} for r in _closed_rows],
                         use_container_width=True, hide_index=True)
            # Realized P&L by closed trade (lightweight bar chart)
            _pnl_rows = [
                {"trade": r.get("paper_trade_id"), "realized_pnl": float(r.get("realized_pnl") or 0.0)}
                for r in _closed_rows
            ]
            if _pnl_rows:
                st.bar_chart(_pnl_rows, x="trade", y="realized_pnl")
        _pf_events = portfolio_ledger.load_events("latest")
        if _pf_events:
            st.markdown("**Trade events (latest 25)**")
            st.dataframe(
                [{k: e.get(k) for k in ("timestamp", "event_type", "profile_id",
                                         "paper_trade_id", "reason")}
                 for e in _pf_events[-25:]],
                use_container_width=True, hide_index=True,
            )
        _pf_rec = portfolio_ledger.load_reconciliation("latest")
        if _pf_rec:
            if _pf_rec.get("ok"):
                st.success("Local reconciliation OK — no issues. "
                           f"(broker_position_reconciliation: {_pf_rec.get('broker_position_reconciliation')})")
            else:
                st.warning(f"Reconciliation found {len(_pf_rec.get('issues', []))} issue(s): "
                           f"{_pf_rec.get('issues')}")

    _pf_profiles = ",".join(_pf_man.get("profiles") or ["vertical_wing_score_best_1dte", "vertical_wing_no_trade"]) \
        if _pf_man else "vertical_wing_score_best_1dte,vertical_wing_no_trade"
    # Phase 9I — Simple Mode is button-driven (no terminal blocks); Advanced keeps
    # the exact copy-paste commands.
    if simple_mode:
        _pf_btns = st.columns(2)
        if _pf_btns[0].button("🔄 Refresh portfolio", key="pf_refresh",
                              help="Re-read the latest local paper portfolio files."):
            st.rerun()
        if _pf_btns[1].button("🧾 Reconcile local paper ledger", key="pf_reconcile",
                              help="Re-read the latest local reconciliation report (read-only)."):
            st.rerun()
        st.caption("Starting a new local paper portfolio (and regenerating reconciliation) is an "
                   "Advanced action — switch to Advanced Mode for the exact commands. "
                   "Local paper accounting only — no broker execution.")
    else:
        st.markdown("**Run a local paper portfolio (copy into a terminal — the UI never launches it):**")
        st.code(
            f"python -m scripts.run_portfolio_forward --profiles {_pf_profiles} --once\n"
            "python -m scripts.review_portfolio_forward --latest\n"
            "python -m scripts.review_portfolio_forward --reconcile latest",
            language="powershell",
        )


def render_manual_desk() -> None:
    st.subheader("Manual local paper entry")
    st.caption("Manual entries are local records only — written to CSV + the in-memory "
               "PaperAccount. No brokerage, no execution.")
    with st.form("manual_trade"):
        cols = st.columns(4)
        side = cols[0].selectbox("Side", ["CALL_CREDIT", "PUT_CREDIT"])
        short_strike = cols[1].number_input("Short strike", value=5815.0, step=5.0)
        long_strike = cols[2].number_input("Long strike", value=5820.0, step=5.0)
        credit = cols[3].number_input("Credit ($)", value=0.60, step=0.05, format="%.2f")
        cols2 = st.columns(4)
        contracts = cols2[0].number_input(
            "Contracts", value=int(session.contracts_per_trade), step=1, min_value=1)
        stop = cols2[1].selectbox(
            "Stop variant",
            ["BASELINE_CASH_SETTLE", "SL_100_PERCENT_LOSS",
             "SL_150_PERCENT_LOSS", "SL_200_PERCENT_LOSS"],
            index=["BASELINE_CASH_SETTLE", "SL_100_PERCENT_LOSS",
                   "SL_150_PERCENT_LOSS", "SL_200_PERCENT_LOSS"].index(session.default_stop_variant),
        )
        profit_target = cols2[2].number_input(
            "Profit target (fraction)",
            value=(session.profit_targets[0] if session.profit_targets else 0.50),
            step=0.05, format="%.2f",
        )
        notes = cols2[3].text_input("Notes", value="")
        submit_trade = st.form_submit_button(om.BTN_RECORD_MANUAL)
    st.caption(
        "Manual entries are local records only. They do not sync with Tastytrade or "
        "any brokerage."
    )

    if submit_trade:
        ts = now_et()
        entry_spot = quote_spot if quote_spot is not None else structure.spot
        record = build_manual_trade_record(
            ts=ts,
            strategy_id=st.session_state["active_strategy"] or "manual",
            side=side, symbol=SYMBOL, expiry=structure.expiry or "",
            short_strike=short_strike, long_strike=long_strike,
            credit=credit, contracts=int(contracts),
            entry_spot=entry_spot, stop_variant=stop, profit_target=profit_target, notes=notes,
        )
        record_manual_trade(OUTPUT_ROOT, row=record)
        pos = PaperPosition(
            position_id=uuid.uuid4().hex[:8],
            strategy_id=record["strategy_id"], side=side, symbol=SYMBOL,
            expiry=structure.expiry or "",
            short_strike=short_strike, long_strike=long_strike,
            credit=credit, contracts=int(contracts),
            entry_time=ts, entry_spot=entry_spot, stop_variant=stop,
            profit_targets=[profit_target] if profit_target else [],
            source="manual", notes=notes,
        )
        paper_account.open_position(pos)
        snapshot_positions(OUTPUT_ROOT, paper_account.open_positions + paper_account.closed_positions)
        append_equity_point(OUTPUT_ROOT, ts.isoformat(), paper_account.equity)
        st.toast(f"Recorded manual trade {pos.position_id}")
        st.rerun()

    st.markdown("**Open tracked positions**")
    if not paper_account.open_positions:
        st.info("No open positions yet.")
    else:
        for p in paper_account.open_positions:
            cols = st.columns([1, 1, 1, 1, 2])
            cols[0].write(f"**{p.position_id}** · {p.side}")
            cols[1].write(f"{p.short_strike}/{p.long_strike} · {p.contracts}×")
            mark = cols[2].number_input(
                "Current mark", key=f"mark_{p.position_id}",
                value=float(p.current_mark or p.credit), step=0.05, format="%.2f",
            )
            if mark != (p.current_mark or 0):
                paper_account.update_mark(p.position_id, mark, now_et())
            cols[3].metric(
                "Unrealized $", f"{unrealized_pnl_dollars(p.credit, mark, p.contracts):,.0f}",
            )
            if cols[4].button("Close at this mark", key=f"close_{p.position_id}"):
                paper_account.close_position(p.position_id, mark, now_et(), reason="manual")
                snapshot_positions(
                    OUTPUT_ROOT, paper_account.open_positions + paper_account.closed_positions,
                )
                append_equity_point(OUTPUT_ROOT, now_et().isoformat(), paper_account.equity)
                st.toast(f"Closed {p.position_id}")
                st.rerun()

    pnl_cols = st.columns(3)
    pnl_cols[0].metric("Realized P&L", f"${paper_account.realized_pnl:,.2f}")
    pnl_cols[1].metric("Unrealized P&L", f"${paper_account.unrealized_pnl:,.2f}")
    pnl_cols[2].metric("Equity", f"${paper_account.equity:,.2f}")
    if paper_account.equity_curve:
        eq_rows = [{"ts": ts.isoformat(), "equity": eq} for ts, eq in paper_account.equity_curve]
        st.line_chart(eq_rows, x="ts", y="equity")


def render_backtest_comparison() -> None:
    """Phase 10E research-only strategy comparison dashboard."""
    from src.backtesting import comparison as _C

    st.divider()
    st.markdown("#### Compare Strategies")
    st.caption(
        "Compare profiles over the same local dates, symbol, DTE, and fixed sizing. "
        "Rankings and promotion labels are research-only and never change execution."
    )

    saved = [row for row in pb.list_summaries() if row.get("ok")]
    saved_by_id = {row["profile_id"]: row for row in saved}
    saved_ids = om.order_profiles_for_dropdown(list(saved_by_id))

    def _profile_label(profile_id: str) -> str:
        row = saved_by_id.get(profile_id, {})
        return om.profile_dropdown_label(
            profile_id, row.get("profile_name"), row.get("preset_kind")
        )

    top = st.columns([1, 1, 2])
    cmp_symbol = top[0].selectbox(
        "Comparison symbol", list(om.BACKTEST_SYMBOLS), key="cmp_symbol"
    )
    availability = ch.backtest_data_availability(cmp_symbol)
    dte_options = [0] + ([1] if availability["1DTE"]["available"] else [])
    cmp_dte = int(top[1].selectbox("Comparison DTE", dte_options, key="cmp_dte"))
    cmp_group = top[2].selectbox(
        "Profile group",
        ["Main Dynamic", "Controls", "All Main", "Custom", "Selected profiles"],
        index=2,
        key="cmp_group",
    )
    group_alias = {
        "Main Dynamic": "dynamic-only",
        "Controls": "controls-only",
        "All Main": "all-main",
        "Custom": "custom",
    }
    if cmp_group == "Selected profiles":
        cmp_profile_request: str | list[str] = st.multiselect(
            "Profiles to compare",
            saved_ids,
            default=list(_C.PRIMARY_PROFILES),
            format_func=_profile_label,
            key="cmp_selected_profiles",
        )
    else:
        cmp_profile_request = group_alias[cmp_group]
    cmp_profiles = _C.resolve_comparison_profiles(cmp_profile_request)
    if cmp_profiles:
        st.caption(
            f"{len(cmp_profiles)} profile(s): "
            + ", ".join(_profile_label(profile_id) for profile_id in cmp_profiles)
        )
    else:
        st.warning("No valid profiles are available for this comparison group.")

    dte_bucket = om.dte_label(cmp_dte)
    data_range = availability.get(dte_bucket) or ch.backtest_data_range(cmp_symbol, dte_bucket)
    st.caption("Local data — " + ch.backtest_range_caption(data_range))
    cmp_mode = st.radio(
        "Comparison date mode",
        ["Latest N days", "Date range", "All data"],
        horizontal=True,
        key="cmp_date_mode",
    )
    cmp_start = cmp_end = None
    cmp_latest_days = 0
    cmp_range_ok = bool(data_range.get("available"))
    if cmp_mode == "Latest N days":
        cmp_latest_days = int(st.number_input(
            "Comparison latest N days", min_value=1, value=20, step=1, key="cmp_latest_days"
        ))
    elif cmp_mode == "Date range" and data_range.get("available"):
        import datetime as _dt

        min_date = _dt.date.fromisoformat(data_range["min_date"])
        max_date = _dt.date.fromisoformat(data_range["max_date"])
        date_cols = st.columns(2)
        cmp_start = str(date_cols[0].date_input(
            "Comparison start", value=min_date, min_value=min_date, max_value=max_date,
            key="cmp_start",
        ))
        cmp_end = str(date_cols[1].date_input(
            "Comparison end", value=max_date, min_value=min_date, max_value=max_date,
            key="cmp_end",
        ))
        cmp_range_ok = cmp_start <= cmp_end
        if not cmp_range_ok:
            st.warning("Comparison start date is after the end date.")
    elif cmp_mode == "All data" and data_range.get("available"):
        st.caption(
            f"Using all {data_range['file_count']} available files from "
            f"{data_range['min_date']} to {data_range['max_date']}."
        )

    sizing_cols = st.columns([2, 1, 1])
    cmp_preset = sizing_cols[0].selectbox(
        "Comparison sizing preset",
        list(om.BACKTEST_SIZING_PRESETS),
        index=list(om.BACKTEST_SIZING_PRESETS).index("Standard paper"),
        key="cmp_sizing_preset",
    )
    preset_balance, preset_contracts = om.BACKTEST_SIZING_PRESETS[cmp_preset]
    cmp_balance = float(sizing_cols[1].number_input(
        "Comparison balance", min_value=1.0, value=float(preset_balance),
        step=500.0, key=f"cmp_balance_{cmp_preset}",
    ))
    cmp_contracts = int(sizing_cols[2].number_input(
        "Comparison contracts", min_value=1, value=int(preset_contracts),
        step=1, key=f"cmp_contracts_{cmp_preset}",
    ))
    cmp_label = st.text_input(
        "Comparison run label",
        value=f"{cmp_symbol.lower()}_{cmp_group.lower().replace(' ', '_')}_compare",
        key="cmp_run_label",
    )

    actions = st.columns([1, 1, 2])
    run_comparison = actions[0].button(
        "▶ Run Comparison",
        type="primary",
        disabled=not (cmp_profiles and cmp_range_ok),
        key="cmp_run",
    )
    if actions[1].button("🔄 Refresh Latest Comparison", key="cmp_refresh"):
        st.rerun()
    actions[2].caption("Research-only output; no live API, broker, order preview, or execution.")

    if run_comparison:
        from datetime import datetime as _datetime

        from src.backtesting.replay_runner import run_backtest as _run_backtest

        with st.spinner(
            f"Comparing {len(cmp_profiles)} profiles over {cmp_symbol} {dte_bucket} local data..."
        ):
            try:
                result = _run_backtest(
                    symbol=cmp_symbol,
                    profile_ids=cmp_profiles,
                    start=cmp_start,
                    end=cmp_end,
                    dte=cmp_dte,
                    latest_days=cmp_latest_days,
                    run_label=cmp_label,
                    starting_balance=cmp_balance,
                    contracts=cmp_contracts,
                )
                result.run_config["comparison_profile_group"] = cmp_group
                stamp = _datetime.now().strftime("%Y-%m-%d_%H%M%S")
                _C.write_comparison_reports(
                    result,
                    [_C.comparison_latest_dir(),
                     _C.comparison_run_dir(stamp, f"{cmp_symbol}_{cmp_label}")],
                    stamp=stamp,
                )
                st.success(
                    f"Comparison complete — {len(cmp_profiles)} profiles, "
                    f"{result.counters.get('selected_trades', 0)} selected trades."
                )
            except Exception as exc:
                st.error(f"Comparison failed: {type(exc).__name__}: {exc}")
        st.rerun()

    latest = ch.read_backtest_comparison(_C.comparison_latest_dir())
    st.markdown("**Latest Comparison**")
    st.caption(f"Reading: `{latest['results_dir']}`")
    if not latest["available"]:
        st.info(latest["reason"])
    else:
        rankings = latest["rankings"]

        def _number(row, key, default=0.0):
            try:
                return float(row.get(key))
            except (TypeError, ValueError):
                return default

        active = [row for row in rankings if _number(row, "total_trades") > 0]
        if active:
            best_expectancy = max(active, key=lambda row: _number(row, "expectancy_dollars"))
            best_drawdown = min(active, key=lambda row: _number(row, "max_drawdown_pct"))
            best_pf = max(active, key=lambda row: _number(row, "profit_factor"))
            best_return = max(active, key=lambda row: _number(row, "return_pct"))
            cards = st.columns(4)
            cards[0].metric(
                "Best Expectancy",
                ch.fmt_money(best_expectancy.get("expectancy_dollars")),
                best_expectancy.get("profile_name"),
            )
            cards[1].metric(
                "Best Drawdown",
                ch.fmt_pct(best_drawdown.get("max_drawdown_pct"), as_fraction=False),
                best_drawdown.get("profile_name"),
            )
            cards[2].metric(
                "Best Profit Factor",
                best_pf.get("profit_factor"),
                best_pf.get("profile_name"),
            )
            cards[3].metric(
                "Best Return",
                ch.fmt_pct(best_return.get("return_pct"), as_fraction=False),
                best_return.get("profile_name"),
            )
        if latest.get("narrative"):
            st.info(latest["narrative"])
        st.caption(
            str(latest.get("run_config", {}).get("ranking_method") or _C.RANKING_METHOD)
        )
        ranking_columns = [
            "rank", "profile_name", "profile_kind", "promotion_status", "ranking_score",
            "total_trades", "win_rate", "total_pnl_dollars", "return_pct",
            "max_drawdown_dollars", "max_drawdown_pct", "profit_factor",
            "expectancy_dollars", "max_consecutive_losses",
        ]
        st.dataframe(
            [{key: row.get(key) for key in ranking_columns} for row in rankings],
            width="stretch",
            hide_index=True,
        )
        st.markdown("**Dynamic vs Control**")
        if latest["dynamic_vs_control"]:
            st.dataframe(latest["dynamic_vs_control"], width="stretch", hide_index=True)
        else:
            st.caption("More data will appear after comparison runs.")
        impact_tabs = st.tabs(["Corridor Impact", "WDS Tier Impact"])
        for tab, rows in zip(
            impact_tabs, [latest["by_corridor"], latest["by_wds_tier"]], strict=False
        ):
            with tab:
                if rows:
                    st.dataframe(rows, width="stretch", hide_index=True)
                else:
                    st.caption("More data will appear after comparison runs.")
        st.markdown("### Why did dynamic underperform?")
        if latest.get("attribution_narrative"):
            st.info(latest["attribution_narrative"])
        else:
            st.info("Insufficient attribution data. Need opposite-side simulation.")
        if latest.get("control_benchmark_note"):
            st.caption(latest["control_benchmark_note"])
        side_rows = latest.get("selected_side_summary") or []
        side_lookup = {str(row.get("selected_side")): row for row in side_rows}
        call_row = side_lookup.get("CALL_CREDIT", {})
        put_row = side_lookup.get("PUT_CREDIT", {})
        groups = {
            str(row.get("profile_group")): row
            for row in latest.get("dynamic_vs_control") or []
        }
        attribution_cards = st.columns(4)
        attribution_cards[0].metric(
            "Dynamic Call Credit",
            f"{int(_number(call_row, 'trades'))} trades",
            ch.fmt_money(call_row.get("total_pnl_dollars")),
        )
        attribution_cards[1].metric(
            "Dynamic Put Credit",
            f"{int(_number(put_row, 'trades'))} trades",
            ch.fmt_money(put_row.get("total_pnl_dollars")),
        )
        attribution_cards[2].metric(
            "Dynamic vs Controls P&L",
            ch.fmt_money(groups.get("dynamic", {}).get("total_pnl_dollars")),
            "Controls " + ch.fmt_money(groups.get("control", {}).get("total_pnl_dollars")),
        )
        total_selected = sum(_number(row, "trades") for row in side_rows)
        opposite_available = sum(_number(row, "opposite_available") for row in side_rows)
        attribution_cards[3].metric(
            "Best Opposite Available",
            (
                f"{opposite_available / total_selected * 100:.0f}%"
                if total_selected else "—"
            ),
            f"{int(opposite_available)} of {int(total_selected)} selections",
        )
        attribution_tabs = st.tabs([
            "Selected Side Split", "Top Failure Buckets",
            "Call-Control Edge", "Research Recommendations",
        ])
        attribution_tables = [
            side_rows,
            latest.get("dynamic_failure_summary") or [],
            latest.get("call_control_edge_summary") or [],
            latest.get("research_recommendations") or [],
        ]
        for tab, rows in zip(attribution_tabs, attribution_tables, strict=False):
            with tab:
                if rows:
                    st.dataframe(rows, width="stretch", hide_index=True)
                else:
                    st.caption("More data will appear after attribution runs.")
        st.markdown("**Trade Logs by Profile**")
        by_profile: dict[str, list[dict]] = {}
        for row in latest["trade_rows"]:
            by_profile.setdefault(str(row.get("_profile") or "Unknown"), []).append(row)
        if not by_profile:
            st.caption("More data will appear after comparison runs.")
        for profile_id, rows in by_profile.items():
            with st.expander(f"{_profile_label(profile_id)} — {len(rows)} trades"):
                st.dataframe(
                    [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows],
                    width="stretch",
                    hide_index=True,
                )

    with st.expander("Advanced — comparison CLI command", expanded=False):
        st.code(
            om.backtest_compare_command(
                cmp_symbol,
                cmp_profile_request,
                cmp_latest_days,
                cmp_dte,
                cmp_label,
                cmp_balance,
                cmp_contracts,
            ),
            language="bash",
        )


def render_learning_review() -> None:
    """Phase 11A readable research evidence and learned-grid recommendations."""
    from src.backtesting import learning as _L

    st.divider()
    st.markdown("#### Learning Review")
    st.caption(
        "Research-only evidence from historical trades, candidates, and skipped days. "
        "Low sample sizes and apparent feature edges require chronological validation."
    )
    latest = ch.read_backtest_learning(_L.research_latest_dir())
    actions = st.columns([1, 3])
    if actions[0].button("Refresh Learning Review", key="learning_refresh"):
        st.rerun()
    actions[1].caption(f"Reading: `{latest['results_dir']}`")
    if not latest["available"]:
        st.info(latest["reason"])
        st.code(
            "python -m scripts.backtest_learn --symbol SPX --dte 0 --all-data "
            "--profiles all-main --starting-balance 10000 --contracts 1 "
            "--run-label learn_spx_all_main",
            language="bash",
        )
        return
    config = latest.get("run_config") or {}
    cards = st.columns(4)
    cards[0].metric(
        "Trades Studied",
        (config.get("source_counters") or {}).get("selected_trades", 0),
    )
    cards[1].metric(
        "Candidates Studied",
        (config.get("source_counters") or {}).get("candidates", 0),
    )
    cards[2].metric("Hypotheses", config.get("hypothesis_count", 0))
    cards[3].metric("Learned Grid Variants", config.get("learned_parameter_set_count", 0))
    st.warning(
        "Feature buckets describe historical association, not causality. "
        "The learned grid remains bounded and must survive validation and holdout."
    )
    summary = latest["feature_performance_summary"]

    def _number(row, key, default=-1e12):
        try:
            return float(row.get(key))
        except (TypeError, ValueError):
            return default

    supported = [
        row for row in summary
        if str(row.get("low_sample_warning")).lower() not in {"true", "1", "yes"}
    ]
    best = sorted(
        supported or summary,
        key=lambda row: (
            -_number(row, "expectancy_dollars"),
            -_number(row, "trade_count", 0),
            str(row.get("feature")),
            str(row.get("bucket")),
        ),
    )[:8]
    worst = sorted(
        supported or summary,
        key=lambda row: (
            _number(row, "expectancy_dollars"),
            -_number(row, "trade_count", 0),
            str(row.get("feature")),
            str(row.get("bucket")),
        ),
    )[:8]
    filters = latest.get("filter_impact_analysis") or []
    best_filters = sorted(
        filters,
        key=lambda row: (
            -_number(row, "expectancy_delta_dollars"),
            -_number(row, "trades_kept", 0),
            str(row.get("filter")),
        ),
    )
    worst_filters = list(reversed(best_filters))
    interactions = sorted(
        latest.get("feature_interaction_matrix") or [],
        key=lambda row: (
            -abs(_number(row, "total_pnl_dollars", 0)),
            -_number(row, "trade_count", 0),
            str(row.get("feature")),
        ),
    )
    tabs = st.tabs([
        "What is making money?",
        "What is losing money?",
        "Best filters by impact",
        "Worst filters / false positives",
        "Strongest feature interactions",
        "Call-only expansion results",
        "Dynamic repair results",
        "Robustness scorecard",
        "Recommended next test",
        "Warnings",
    ])
    with tabs[0]:
        st.dataframe(latest.get("win_driver_matrix") or best, width="stretch", hide_index=True)
    with tabs[1]:
        st.dataframe(latest.get("loss_driver_matrix") or worst, width="stretch", hide_index=True)
    with tabs[2]:
        st.dataframe(best_filters[:15], width="stretch", hide_index=True)
    with tabs[3]:
        st.dataframe(worst_filters[:15], width="stretch", hide_index=True)
    with tabs[4]:
        st.dataframe(interactions[:30], width="stretch", hide_index=True)
    with tabs[5]:
        expansion = latest.get("call_only_expansion_results") or []
        robustness = latest.get("call_only_robustness_results") or []
        if expansion or robustness:
            st.markdown("**Expansion grid**")
            st.dataframe(expansion, width="stretch", hide_index=True)
            st.markdown("**Stricter robustness grid**")
            st.dataframe(robustness, width="stretch", hide_index=True)
        else:
            st.caption("Run the Phase 11B call-only optimization smokes to populate this review.")
    with tabs[6]:
        dynamic = latest.get("dynamic_repair_results") or []
        if dynamic:
            st.dataframe(dynamic, width="stretch", hide_index=True)
        else:
            st.caption("Run the Phase 11B dynamic-repair smoke to populate this review.")
    with tabs[7]:
        scorecard = latest.get("strategy_robustness_scorecard") or []
        if scorecard:
            st.dataframe(scorecard, width="stretch", hide_index=True)
        else:
            st.caption("No robustness scorecard is available.")
    with tabs[8]:
        st.info(
            "Recommended sequence: run the bounded call-only expansion, confirm the strongest "
            "family in the stricter robustness grid, then treat dynamic repair as research only."
        )
        st.code(
            "python -m scripts.backtest_optimize --symbol SPX --dte 0 --all-data "
            "--grid learned_call_only_expansion --starting-balance 10000 --contracts 1 "
            "--max-combinations 96 --run-label call_only_expansion_spx",
            language="bash",
        )
        if latest.get("phase11b_smoke_summary"):
            st.markdown(latest["phase11b_smoke_summary"])
    with tabs[9]:
        warning_rows = [
            row for row in latest.get("strategy_robustness_scorecard") or []
            if row.get("warnings")
        ]
        if warning_rows:
            st.dataframe(warning_rows, width="stretch", hide_index=True)
        else:
            st.caption(
                "No generated warnings. Continue to review low sample, one-day concentration, "
                "month concentration, side concentration, possible overfit, and filtered-too-hard risk."
            )
        with st.expander("No-trade blockers and replay assumptions", expanded=False):
            st.dataframe(latest["no_trade_blocker_summary"], width="stretch", hide_index=True)
            if latest.get("audit"):
                st.markdown(latest["audit"])


def render_optimization_lab() -> None:
    """Phase 10G research-only optimization controls and latest results."""
    from src.backtesting import optimization as _O

    st.divider()
    st.markdown("#### Optimization Lab")
    st.caption(
        "Optimization is research only. It does not change live strategy behavior. "
        "Ranking uses train and validation only; holdout is shown separately."
    )
    top = st.columns([1, 1, 2, 1])
    symbol = top[0].selectbox(
        "Optimization symbol", list(om.BACKTEST_SYMBOLS), key="opt_symbol"
    )
    availability = ch.backtest_data_availability(symbol)
    dte_options = [0] + ([1] if availability["1DTE"]["available"] else [])
    dte = int(top[1].selectbox("Optimization DTE", dte_options, key="opt_dte"))
    grid = top[2].selectbox(
        "Grid selection",
        [
            "core_morning", "core_eod", "dynamic_selector_experiments",
            "controls_baseline", "learned_hypotheses", "learned_call_only_expansion",
            "learned_call_only_robustness", "learned_dynamic_repair",
            "custom_selected_profiles",
        ],
        key="opt_grid",
    )
    max_combinations = int(top[3].number_input(
        "Max combinations", min_value=1, max_value=200, value=12, step=1,
        key="opt_max_combinations",
    ))
    custom_profiles: list[str] = []
    if grid == "custom_selected_profiles":
        custom_profiles = st.multiselect(
            "Custom selected profiles",
            om.order_profiles_for_dropdown([
                row["profile_id"] for row in pb.list_summaries() if row.get("ok")
            ]),
            key="opt_custom_profiles",
        )

    dte_bucket = om.dte_label(dte)
    data_range = availability.get(dte_bucket) or ch.backtest_data_range(symbol, dte_bucket)
    st.caption("Local data — " + ch.backtest_range_caption(data_range))
    date_mode = st.radio(
        "Optimization date mode",
        ["Latest N days", "Date range", "All data"],
        horizontal=True,
        key="opt_date_mode",
    )
    start = end = None
    latest_days = 0
    range_ok = bool(data_range.get("available"))
    if date_mode == "Latest N days":
        latest_days = int(st.number_input(
            "Optimization latest N days", min_value=3, value=60, step=1,
            key="opt_latest_days",
        ))
    elif date_mode == "Date range" and data_range.get("available"):
        import datetime as _dt

        minimum = _dt.date.fromisoformat(data_range["min_date"])
        maximum = _dt.date.fromisoformat(data_range["max_date"])
        dates = st.columns(2)
        start = str(dates[0].date_input(
            "Optimization start", value=minimum, min_value=minimum, max_value=maximum,
            key="opt_start",
        ))
        end = str(dates[1].date_input(
            "Optimization end", value=maximum, min_value=minimum, max_value=maximum,
            key="opt_end",
        ))
        range_ok = start <= end
    elif date_mode == "All data" and data_range.get("available"):
        st.caption(
            f"Using all {data_range['file_count']} available files from "
            f"{data_range['min_date']} to {data_range['max_date']}."
        )

    split_mode = st.selectbox(
        "Split mode",
        ["Chronological 60/20/20", "Custom chronological percentages"],
        key="opt_split_mode",
    )
    train_pct, validation_pct, holdout_pct = 60, 20, 20
    if split_mode == "Custom chronological percentages":
        split_cols = st.columns(3)
        train_pct = int(split_cols[0].number_input(
            "Train %", min_value=1, max_value=98, value=60, step=1, key="opt_train_pct"
        ))
        validation_pct = int(split_cols[1].number_input(
            "Validation %", min_value=1, max_value=98, value=20, step=1,
            key="opt_validation_pct",
        ))
        holdout_pct = int(split_cols[2].number_input(
            "Holdout %", min_value=1, max_value=98, value=20, step=1,
            key="opt_holdout_pct",
        ))
        if train_pct + validation_pct + holdout_pct != 100:
            st.warning("Train, validation, and holdout percentages must total 100.")
            range_ok = False

    sizing = st.columns([1, 1, 2])
    balance = float(sizing[0].number_input(
        "Optimization starting balance", min_value=1.0, value=10000.0, step=500.0,
        key="opt_balance",
    ))
    contracts = int(sizing[1].number_input(
        "Optimization contracts", min_value=1, value=1, step=1, key="opt_contracts"
    ))
    run_label = sizing[2].text_input(
        "Optimization run label", value=f"opt_{grid}", key="opt_run_label"
    )
    custom_ok = grid != "custom_selected_profiles" or bool(custom_profiles)
    actions = st.columns([1, 1, 2])
    run_clicked = actions[0].button(
        "▶ Run Optimization",
        type="primary",
        disabled=not (range_ok and custom_ok),
        key="opt_run",
    )
    if actions[1].button("🔄 Refresh Latest Optimization", key="opt_refresh"):
        st.rerun()
    actions[2].caption("No live API, broker, order preview, profile mutation, or execution.")

    if run_clicked:
        config = _O.OptimizationConfig(
            symbol=symbol,
            dte=dte,
            start=start,
            end=end,
            latest_days=latest_days,
            all_data=date_mode == "All data",
            starting_balance=balance,
            contracts=contracts,
            grid=grid,
            run_label=run_label,
            max_combinations=max_combinations,
            profile_ids=tuple(custom_profiles),
            train_pct=train_pct,
            validation_pct=validation_pct,
            holdout_pct=holdout_pct,
        )
        with st.spinner(
            f"Optimizing {max_combinations} deterministic variants over local {symbol} data..."
        ):
            try:
                result = _O.run_optimization(config)
                run_id = str(result.run_config["optimizer_run_id"])
                _O.write_optimization_reports(
                    result,
                    [_O.optimization_latest_dir(), _O.optimization_run_dir(run_id)],
                )
                st.success(
                    f"Optimization complete — {len(result.rankings)} variants, "
                    f"{len(result.promotion_candidates)} forward-paper candidate(s)."
                )
            except Exception as exc:
                st.error(f"Optimization failed: {type(exc).__name__}: {exc}")
        st.rerun()

    latest = ch.read_backtest_optimization(_O.optimization_latest_dir())
    st.markdown("**Latest Optimization**")
    st.caption(f"Reading: `{latest['results_dir']}`")
    if not latest["available"]:
        st.info(latest["reason"])
        return
    rankings = latest["rankings"]

    def _number(row, key, default=0.0):
        try:
            return float(row.get(key))
        except (TypeError, ValueError):
            return default

    if latest.get("narrative"):
        st.info(latest["narrative"])
    split_dates = latest.get("run_config", {}).get("split_dates") or {}
    split_cards = st.columns(3)
    for card, label, key in zip(
        split_cards, ["Train", "Validation", "Holdout"], ["train", "validation", "holdout"],
        strict=True,
    ):
        values = split_dates.get(key) or []
        card.metric(label, f"{len(values)} sessions", f"{values[0]} → {values[-1]}" if values else "—")
    if rankings:
        research_rankings = [
            row for row in rankings
            if row.get("promotion_status") not in {"Benchmark Control", "Comparison Baseline"}
        ]
        best = (latest["promotion_candidates"] or research_rankings or rankings)[0]
        benchmarks = [
            row for row in rankings if row.get("promotion_status") == "Benchmark Control"
        ]
        best_benchmark = benchmarks[0] if benchmarks else {}
        cards = st.columns(5)
        cards[0].metric("Best Robust Candidate", best.get("profile_id"), best.get("promotion_status"))
        cards[1].metric(
            "Validation Expectancy", ch.fmt_money(best.get("validation_expectancy_dollars"))
        )
        cards[2].metric("Holdout Expectancy", ch.fmt_money(best.get("holdout_expectancy_dollars")))
        cards[3].metric("Overfit Warnings", str(len(latest["overfit_warnings"])))
        cards[4].metric(
            "Benchmark Comparison",
            best_benchmark.get("profile_id") or "No control in grid",
            (
                ch.fmt_money(best_benchmark.get("validation_expectancy_dollars"))
                if best_benchmark else "Run controls_baseline"
            ),
        )
    st.markdown("**Ranked Candidates**")
    st.dataframe(rankings, width="stretch", hide_index=True)
    tabs = st.tabs(["Promotion Labels", "Overfit Warnings", "Split Results"])
    with tabs[0]:
        if latest["promotion_candidates"]:
            st.dataframe(latest["promotion_candidates"], width="stretch", hide_index=True)
        else:
            st.caption("No profile cleared forward-paper promotion rules.")
    with tabs[1]:
        if latest["overfit_warnings"]:
            st.dataframe(latest["overfit_warnings"], width="stretch", hide_index=True)
        else:
            st.caption("No overfit warnings were generated.")
    with tabs[2]:
        split_tabs = st.tabs(["Train", "Validation", "Holdout"])
        for tab, rows in zip(
            split_tabs,
            [latest["train_results"], latest["validation_results"], latest["holdout_results"]],
            strict=True,
        ):
            with tab:
                st.dataframe(rows, width="stretch", hide_index=True)

    from src.backtesting import robustness_review as _R

    st.markdown("**Robustness Review**")
    review = ch.read_backtest_robustness_review(_R.robustness_latest_dir())
    st.caption(f"Reading: `{review['results_dir']}`")
    if not review["available"]:
        st.caption(review["reason"])
        return
    if review.get("narrative"):
        st.info(review["narrative"])
    recommendation = review.get("freeze_recommendation") or {}
    review_cards = st.columns(4)
    review_cards[0].metric(
        "Frozen Profile Recommendation",
        recommendation.get("recommendation") or "No recommendation",
    )
    review_cards[1].metric(
        "Freeze Criteria",
        (
            f"{recommendation.get('passed_criteria', 0)}/"
            f"{recommendation.get('total_criteria', 0)}"
        ),
    )
    review_cards[2].metric(
        "Candidate Hash",
        recommendation.get("parameter_hash") or "—",
    )
    review_cards[3].metric(
        "Profile Frozen",
        "No — research continues",
        "No automatic profile writes",
    )
    review_tabs = st.tabs([
        "Split Sensitivity",
        "Candidate vs Controls",
        "Freeze Criteria",
        "Expanded Runs",
    ])
    with review_tabs[0]:
        st.dataframe(
            review["split_sensitivity_summary"], width="stretch", hide_index=True
        )
        with st.expander("Candidate consistency across splits", expanded=False):
            st.dataframe(
                review["candidate_consistency"], width="stretch", hide_index=True
            )
    with review_tabs[1]:
        st.dataframe(
            review["candidate_vs_control_benchmark"], width="stretch", hide_index=True
        )
    with review_tabs[2]:
        st.dataframe(review["freeze_criteria"], width="stretch", hide_index=True)
        if recommendation.get("freeze_eligible"):
            st.success(
                "Candidate is robust enough to freeze as a disabled research profile. "
                "This is not production approval."
            )
        else:
            st.warning(
                "No profile was frozen. Continue research before forward paper."
            )
    with review_tabs[3]:
        st.dataframe(review["expanded_run_summary"], width="stretch", hide_index=True)

    from src.backtesting import stress_review as _S

    st.markdown("**Near-Miss Candidate Review**")
    stress = ch.read_backtest_stress_review(_S.stress_latest_dir())
    st.caption(f"Reading: `{stress['results_dir']}`")
    if not stress["available"]:
        st.caption(stress["reason"])
        return
    if stress.get("narrative"):
        st.info(stress["narrative"])
    stress_recommendation = stress.get("recommendation") or {}
    stress_cards = st.columns(4)
    stress_cards[0].metric(
        "Final Recommendation",
        stress_recommendation.get("recommendation") or "No recommendation",
    )
    stress_cards[1].metric(
        "Stress Criteria",
        (
            f"{stress_recommendation.get('passed_criteria', 0)}/"
            f"{stress_recommendation.get('total_criteria', 0)}"
        ),
    )
    snapshot = stress.get("candidate_profile_snapshot") or {}
    stress_cards[2].metric("Candidate Hash", snapshot.get("parameter_hash") or "—")
    stress_cards[3].metric(
        "Profile Frozen",
        "Eligible, disabled research only"
        if stress_recommendation.get("freeze_eligible")
        else "No — near-miss remains research",
    )
    stress_tabs = st.tabs([
        "Split Stress",
        "Fill Stress",
        "Account Stress",
        "Concentration Check",
        "Final Recommendation",
    ])
    with stress_tabs[0]:
        st.dataframe(stress["split_stress_summary"], width="stretch", hide_index=True)
    with stress_tabs[1]:
        st.dataframe(stress["slippage_stress_summary"], width="stretch", hide_index=True)
        st.caption(
            "Fill stress uses a conservative post-trade entry-credit deduction; "
            "it is not lifecycle resimulation."
        )
    with stress_tabs[2]:
        st.dataframe(stress["account_sizing_stress"], width="stretch", hide_index=True)
    with stress_tabs[3]:
        st.dataframe(stress["concentration_summary"], width="stretch", hide_index=True)
    with stress_tabs[4]:
        if stress_recommendation.get("freeze_eligible"):
            st.success(
                "Stress criteria passed. Candidate may be frozen as a disabled "
                "research profile; this is not production approval."
            )
        else:
            st.warning(
                "Candidate did not clear every stress criterion. No profile should be frozen."
            )


def render_backtests() -> None:
    """Phase 10C follow-up — usable LOCAL backtests: pick symbol / saved profile (incl.
    custom) / DTE / date mode (Latest N · Date range · All data), see how far back local
    data goes, RUN in-process (read-only replay with a spinner), and read results. The
    CLI is a secondary fallback in an Advanced expander. Never live, never a brokerage."""
    st.subheader("📈 Backtests — local historical replay")
    st.caption(om.BACKTEST_NOTE)
    st.markdown(ui.pill("LOCAL SNAPSHOTS · NO LIVE API · NO BROKER", "green"),
                unsafe_allow_html=True)
    st.markdown("#### ▶ Run a Backtest")

    # ── Saved profiles (incl. CUSTOM ones Dan builds); invalid surfaced, not hidden ──
    _all_bt = pb.list_summaries()
    _bt_ok = [s for s in _all_bt if s.get("ok")]
    _bt_summ = {s["profile_id"]: s for s in _bt_ok}
    _bt_show_all = st.checkbox(
        "Show all saved profiles", value=True, key="bt_show_all",
        help="On = every saved profile incl. custom ones you build. Off = Main Strategies only.")
    _bt_saved = (om.order_profiles_for_dropdown(list(_bt_summ)) if _bt_show_all
                 else (om.simple_mode_profile_ids(_bt_ok) or list(_bt_summ)))
    _bt_profiles = ["all-main", "all", *_bt_saved]

    def _bt_label_fn(pid: str) -> str:
        if pid in ("all-main", "all"):
            return pid
        s = _bt_summ.get(pid, {})
        return om.profile_dropdown_label(pid, s.get("profile_name"), s.get("preset_kind"))

    _bt_invalid = [s.get("profile_id") for s in _all_bt if not s.get("ok")]
    if _bt_invalid:
        st.caption(f"⚠ {len(_bt_invalid)} invalid profile(s) hidden: "
                   f"{', '.join(str(i) for i in _bt_invalid)} — fix in 🧱 Zσ Strat Builder.")

    # ── Symbol / profile / DTE (1DTE shown only when local data exists) ──
    c1 = st.columns([1, 2, 1])
    bt_symbol = c1[0].selectbox("Symbol", list(om.BACKTEST_SYMBOLS), index=0, key="bt_symbol")
    bt_profile = c1[1].selectbox(
        "Strategy profile", _bt_profiles, index=0, format_func=_bt_label_fn, key="bt_profile",
        help="'all-main' = 4 primary presets · 'all' adds controls · or pick a saved "
             "profile (incl. your custom ones).")
    if bt_profile in ("all-main", "all"):
        from src.backtesting.replay_runner import resolve_profiles as _resolve_bt_profiles
        _bt_profile_rows = _profile_contexts(_resolve_bt_profiles(bt_profile))
        st.markdown("**Strategy Synopsis**")
        st.info(om.multi_strategy_synopsis(_bt_profile_rows, context="backtest"))
        with st.expander("Profiles included", expanded=False):
            for _p in _bt_profile_rows:
                st.caption(om.strategy_one_line(_p))
    else:
        _bt_profile_row = _load_profile_context(bt_profile)
        if _bt_profile_row:
            _render_strategy_synopsis(_bt_profile_row, context="backtest")
    _avail = ch.backtest_data_availability(bt_symbol)
    _dte_opts = [0] + ([1] if _avail["1DTE"]["available"] else [])
    bt_dte = int(c1[2].selectbox(
        "DTE", _dte_opts, index=0, key="bt_dte",
        help=("0DTE supported; 1DTE shown because local 1DTE data exists for this symbol."
              if _avail["1DTE"]["available"]
              else "0DTE only — no local 1DTE data for this symbol.")))
    _dte_bucket = om.dte_label(bt_dte)
    _rng = _avail.get(_dte_bucket) or ch.backtest_data_range(bt_symbol, _dte_bucket)

    # ── How far back local data goes (drives "All data") ──
    st.caption("Local data — " + ch.backtest_range_caption(_avail["0DTE"])
               + "  ·  " + ch.backtest_range_caption(_avail["1DTE"]))

    # ── Date mode: Latest N days / Date range / All data ──
    bt_mode = st.radio("Date mode", ["Latest N days", "Date range", "All data"],
                       horizontal=True, key="bt_mode")
    _start = _end = None
    _latest_days = 0
    _range_ok = True
    if bt_mode == "Latest N days":
        _latest_days = int(st.number_input("Latest N days", value=20, min_value=1, step=1,
                                           key="bt_days"))
    elif bt_mode == "Date range":
        if _rng["available"]:
            import datetime as _dt
            _mn = _dt.date.fromisoformat(_rng["min_date"])
            _mx = _dt.date.fromisoformat(_rng["max_date"])
            _dc = st.columns(2)
            _s = _dc[0].date_input("Start date", value=_mn, min_value=_mn, max_value=_mx,
                                   key="bt_start")
            _e = _dc[1].date_input("End date", value=_mx, min_value=_mn, max_value=_mx,
                                   key="bt_end")
            _start, _end = str(_s), str(_e)
            try:
                from src.backtesting import raw_snapshot_loader as _L
                _in = [d for d in _L.available_dates(bt_symbol, _dte_bucket)
                       if _start <= d <= _end]
            except Exception:
                _in = []
            if _start > _end:
                st.warning("Start date is after end date.")
                _range_ok = False
            elif not _in:
                st.warning(f"No {bt_symbol} {_dte_bucket} data files between {_start} and {_end}.")
                _range_ok = False
            else:
                st.caption(f"{len(_in)} data file(s) in range {_start} → {_end}.")
        else:
            st.warning(f"No local {bt_symbol} {_dte_bucket} data to pick a range from.")
            _range_ok = False
    else:  # All data
        if _rng["available"]:
            _start, _end = _rng["min_date"], _rng["max_date"]
            st.caption(f"Available data for {bt_symbol} {_dte_bucket}: {_rng['min_date']} → "
                       f"{_rng['max_date']}, {_rng['file_count']} files.")
        else:
            st.warning(f"No local {bt_symbol} {_dte_bucket} data available.")
            _range_ok = False

    bt_label = st.text_input(
        "Run label", value=om.backtest_default_label(bt_symbol, bt_profile, bt_mode),
        key="bt_label")

    # ── Fixed sizing (Phase 10D-B): account-adjusted reports only, no risk sizing ──
    _preset_names = list(om.BACKTEST_SIZING_PRESETS)
    _default_preset = "Standard paper"
    if "bt_sizing_preset_prev" not in st.session_state:
        st.session_state["bt_sizing_preset_prev"] = _default_preset
        _bal, _qty = om.BACKTEST_SIZING_PRESETS[_default_preset]
        st.session_state.setdefault("bt_starting_balance", _bal)
        st.session_state.setdefault("bt_contracts", _qty)
    bt_preset = st.selectbox(
        "Sizing preset", _preset_names, index=_preset_names.index(_default_preset),
        key="bt_sizing_preset",
        help="Fixed-size replay presets. This does not enable risk-based sizing.")
    if st.session_state.get("bt_sizing_preset_prev") != bt_preset:
        _bal, _qty = om.BACKTEST_SIZING_PRESETS[bt_preset]
        st.session_state["bt_starting_balance"] = _bal
        st.session_state["bt_contracts"] = _qty
        st.session_state["bt_sizing_preset_prev"] = bt_preset
    _sz = st.columns(2)
    bt_starting_balance = float(_sz[0].number_input(
        "Starting Balance", value=float(st.session_state.get("bt_starting_balance", 10000.0)),
        min_value=1.0, step=500.0, key="bt_starting_balance"))
    bt_contracts = int(_sz[1].number_input(
        "Contracts / Lots", value=int(st.session_state.get("bt_contracts", 1)),
        min_value=1, step=1, key="bt_contracts"))
    st.caption(
        f"Backtest sizing: {ch.fmt_money(bt_starting_balance)} starting balance · "
        f"{bt_contracts} contract{'s' if bt_contracts != 1 else ''} per spread. "
        "Fixed sizing only; risk-based sizing is deferred.")

    # ── Run (in-process, spinner) / Refresh ──
    _runnable = bool(_rng["available"]) and _range_ok
    rcol = st.columns([1, 1, 2])
    _run = rcol[0].button("▶ Run Backtest", type="primary", key="bt_run", disabled=not _runnable)
    if rcol[1].button("🔄 Refresh Latest Results", key="bt_refresh"):
        st.rerun()
    if not _runnable:
        rcol[2].caption("_Run disabled — no local data for the current symbol / DTE / range._")
    if _run:
        from datetime import datetime as _datetime

        from src.backtesting import mappers as _M
        from src.backtesting import reports as _R
        from src.backtesting.replay_runner import resolve_profiles, run_backtest
        _profs = resolve_profiles(bt_profile)
        with st.spinner(f"Running backtest — {bt_symbol} {_dte_bucket} · {bt_mode} · "
                        f"{len(_profs)} profile(s)… local CPU, read-only, no live calls."):
            try:
                _res = run_backtest(symbol=bt_symbol, profile_ids=_profs, start=_start,
                                    end=_end, dte=bt_dte, latest_days=_latest_days,
                                    run_label=bt_label,
                                    starting_balance=bt_starting_balance,
                                    contracts=bt_contracts)
                _stamp = _datetime.now().strftime("%Y-%m-%d_%H%M%S")
                _R.write_reports(_res, [_M.latest_dir(),
                                        _M.run_dir(_stamp, f"{bt_symbol}_{bt_label}")],
                                 stamp=_stamp)
                _c = _res.counters
                st.success(f"Backtest complete — {_c.get('selected_trades', 0)} trades from "
                           f"{_c.get('dates_evaluated', 0)} dates "
                           f"({_c.get('candidates', 0)} candidates). Results below ↓")
            except Exception as _exc:                # never crash the cockpit
                st.error(f"Backtest failed: {type(_exc).__name__}: {_exc}")
        st.rerun()
    st.divider()

    # ── Latest results — reads outputs/backtests/latest only (never live) ──
    from src.backtesting import mappers as _bt_mappers
    st.markdown("#### 📄 Latest Results")
    _results = ch.read_backtest_results(_bt_mappers.latest_dir())
    st.caption(f"Reading: `{_results['results_dir']}`")
    if not _results["available"]:
        st.info(_results["reason"])
    else:
        _rcfg = _results.get("run_config") or {}
        _m = _results.get("metrics") or {}
        _ctx = []
        if _rcfg.get("symbol"):
            _ctx.append(f"symbol {_rcfg.get('symbol')}")
        if _rcfg.get("profiles"):
            _ctx.append(f"profiles {_rcfg.get('profiles')}")
        if _rcfg.get("stamp"):
            _ctx.append(str(_rcfg.get("stamp")))
        if _ctx:
            st.caption(" · ".join(_ctx))
        _contracts = _m.get("contracts") or _rcfg.get("contracts") or 1
        _starting_balance = _m.get("starting_balance") or _rcfg.get("starting_balance")
        _ending_balance = _m.get("ending_balance")
        _ret = _m.get("return_pct")
        _dd = _m.get("max_drawdown_dollars")
        _dd_pct = _m.get("max_drawdown_pct")
        _wr = _m.get("win_rate")
        st.caption(
            f"Account-adjusted result for {ch.fmt_money(_starting_balance)} starting balance · "
            f"{_contracts} contract{'s' if int(_contracts or 1) != 1 else ''}.")
        k = st.columns(4)
        k[0].metric("Ending Balance", ch.fmt_money(_ending_balance))
        k[1].metric("Total P&L", ch.fmt_money(_m.get("total_pnl_dollars", 0.0)))
        k[2].metric("Return %", ch.fmt_pct(_ret, as_fraction=False, decimals=2))
        k[3].metric("Max Drawdown %", ch.fmt_pct(_dd_pct, as_fraction=False, decimals=2))
        kk = st.columns(4)
        kk[0].metric("Profit Factor", _m.get("profit_factor", "—"))
        kk[1].metric("Expectancy", ch.fmt_money(_m.get("expectancy_dollars")))
        kk[2].metric("Trades", _m.get("total_trades", 0))
        kk[3].metric("Win Rate", f"{_wr * 100:.0f}%" if isinstance(_wr, (int, float)) else "—")
        km = st.columns(5)
        km[0].metric("Starting Balance", ch.fmt_money(_starting_balance))
        km[1].metric("Contracts", _contracts)
        km[2].metric("Max Drawdown $", ch.fmt_money(_dd) if isinstance(_dd, (int, float)) else "—")
        km[3].metric("Avg Win / Loss", f"{ch.fmt_money(_m.get('avg_win_dollars'))} / "
                     f"{ch.fmt_money(_m.get('avg_loss_dollars'))}")
        km[4].metric("TP / SL / EOD",
                     f"{_m.get('tp_count', 0)} / {_m.get('sl_count', 0)} / {_m.get('eod_count', 0)}")

        _explain = _results.get("explainability") or {}
        st.markdown("**Run Summary**")
        st.info(om.backtest_run_narrative(
            run_config=_rcfg, metrics=_m, explainability=_explain))
        if _explain.get("summary"):
            st.info(_explain["summary"])

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        st.markdown("**Charts**")
        _eq = _results.get("equity_curve") or []
        _eq_chart = []
        for _r in _eq:
            _trade_index = _num(_r.get("trade_index"))
            _equity = _num(_r.get("account_equity"))
            _drawdown = _num(_r.get("drawdown_dollars"))
            if _trade_index is not None and _equity is not None:
                _eq_chart.append({
                    "trade_index": int(_trade_index),
                    "account_equity": _equity,
                    "drawdown_dollars": _drawdown,
                })
        _eq_vals = [r.get("account_equity") for r in _eq_chart]
        if _chart_ready(_eq_vals):
            st.line_chart(_eq_chart, x="trade_index", y="account_equity", use_container_width=True)
        else:
            st.caption("More data will appear after runs.")
        _dd_vals = [r.get("drawdown_dollars") for r in _eq_chart]
        if _chart_ready(_dd_vals):
            st.line_chart(_eq_chart, x="trade_index", y="drawdown_dollars", use_container_width=True)
        _daily = _results.get("daily_pnl") or []
        _daily_chart = []
        for _r in _daily:
            _pnl = _num(_r.get("pnl_dollars"))
            if _pnl is not None:
                _daily_chart.append({"date": _r.get("date"), "pnl_dollars": _pnl})
        _daily_vals = [r.get("pnl_dollars") for r in _daily_chart]
        if _chart_ready(_daily_vals):
            st.bar_chart(_daily_chart, x="date", y="pnl_dollars", use_container_width=True)

        st.markdown("**Trades**")
        _trade_rows = _results.get("trade_rows") or []
        if not _trade_rows:
            st.info("No trades selected in this run. Review skipped days below.")
        else:
            _tf = st.columns(5)
            _profiles = sorted({str(r.get("_profile") or "") for r in _trade_rows if r.get("_profile")})
            _sides = sorted({str(r.get("_side") or "") for r in _trade_rows if r.get("_side")})
            _exits = sorted({str(r.get("_exit_reason") or "") for r in _trade_rows if r.get("_exit_reason")})
            _corridors = ["Yes", "No"]
            _tiers = sorted({str(r.get("_wds_tier") or "") for r in _trade_rows if r.get("_wds_tier")})
            _sel_profiles = _tf[0].multiselect("Profile", _profiles, default=_profiles, key="bt_f_profile")
            _sel_sides = _tf[1].multiselect("Side", _sides, default=_sides, key="bt_f_side")
            _sel_exits = _tf[2].multiselect("Exit", _exits, default=_exits, key="bt_f_exit")
            _sel_corridors = _tf[3].multiselect("Corridor", _corridors, default=_corridors,
                                                key="bt_f_corridor")
            _sel_tiers = _tf[4].multiselect("WDS", _tiers, default=_tiers, key="bt_f_wds")
            _filtered = [
                r for r in _trade_rows
                if str(r.get("_profile") or "") in _sel_profiles
                and str(r.get("_side") or "") in _sel_sides
                and str(r.get("_exit_reason") or "") in _sel_exits
                and str(r.get("Corridor") or "") in _sel_corridors
                and str(r.get("_wds_tier") or "") in _sel_tiers
            ]
            _show = [
                {k: v for k, v in r.items() if not k.startswith("_") and k != "P&L Raw"}
                for r in _filtered
            ]
            st.dataframe(_show, use_container_width=True, hide_index=True)

        st.markdown("**Why Trades Did Not Fire**")
        _top = _explain.get("top_reasons") or []
        if _top:
            st.dataframe(_top, use_container_width=True, hide_index=True)
        _no_trade = _results.get("no_trade_reasons") or []
        if _no_trade:
            _cols = [
                "date", "profile_id", "entry_target", "status", "reason", "first_blocker",
                "candidate_count", "eligible_candidate_count", "risk_filtered_count",
                "quote_filtered_count", "score_filtered_count", "selector_filtered_count",
                "top_selector_reason", "top_risk_reason", "top_quote_reason",
            ]
            st.dataframe([{c: r.get(c) for c in _cols} for r in _no_trade],
                         use_container_width=True, hide_index=True)
        else:
            st.caption("More data will appear after runs.")

        st.markdown("**Breakdowns**")
        _break_tabs = st.tabs(["Profile", "Side", "Exit", "Corridor", "WDS", "Day"])
        _breaks = [
            _results.get("by_profile") or [],
            _results.get("by_side") or [],
            _results.get("by_exit_reason") or [],
            _results.get("by_corridor") or [],
            _results.get("by_wds_tier") or [],
            _results.get("by_day") or [],
        ]
        for _tab, _rows in zip(_break_tabs, _breaks, strict=False):
            with _tab:
                if _rows:
                    st.dataframe(_rows, use_container_width=True, hide_index=True)
                else:
                    st.caption("More data will appear after runs.")
    st.caption("Historical simulation only — no broker, no order preview, no execution.")

    render_backtest_comparison()
    render_learning_review()
    render_optimization_lab()

    # ── Advanced — CLI equivalent (secondary fallback; not the main workflow) ──
    with st.expander("Advanced — CLI command (optional, runs the same thing)", expanded=False):
        _cli_days = _latest_days if bt_mode == "Latest N days" else 0
        st.code(om.backtest_command(
            bt_symbol, bt_profile, _cli_days, bt_dte, bt_label,
            bt_starting_balance, bt_contracts),
                language="bash")
        if bt_mode != "Latest N days" and _start and _end:
            st.code(f"python -m scripts.backtest_run --symbol {bt_symbol} --profile {bt_profile} "
                    f"--start {_start} --end {_end} --dte {bt_dte} --run-label {bt_label} "
                    f"--starting-balance {bt_starting_balance:g} --contracts {bt_contracts}",
                    language="bash")
        st.caption("The buttons above run this in-process; the CLI is here only as a fallback.")


def render_logs() -> None:
    st.subheader("📊 Strategy Stats & Review")
    st.caption(
        "Latest run, historical stats from local paper runs, exports, and a review "
        "prompt. Read-only — no execution, no broker calls."
    )
    _fwd_root = OUTPUT_ROOT / "forward"
    _pf_root = OUTPUT_ROOT / "portfolio_forward"

    # ── A. Latest run summary (Phase 9I — friendly label first; raw id in Advanced) ──
    st.markdown("**Latest completed test**")
    latest = ch.latest_run_stats(_fwd_root, _pf_root)
    _lman = forward_review.load_latest_manifest(_fwd_root) or {}
    _latest_label = om.friendly_run_label(
        run_id=latest["run_id"], profile_name=_lman.get("profile_name") or latest["profile"],
        strategy_id=_lman.get("strategy_id"), started_at=_lman.get("started_at"))
    la = st.columns(4)
    la[0].metric("Latest run", _latest_label)
    la[1].metric("Profile", str(latest["profile"]))
    la[2].metric("Ticks", latest["ticks"])
    la[3].metric("Selected", latest["signals"])
    if not simple_mode and latest["has_data"]:
        st.caption(f"Full run id: `{latest['run_id']}`")
    lb = st.columns(4)
    lb[0].metric("No-trade", latest["no_trade"])
    lb[1].metric("Open paper", latest["open_paper"])
    lb[2].metric("Realized P&L", ch.fmt_money(latest["realized_pnl"]))
    lb[3].metric("Total P&L", ch.fmt_money(latest["total_pnl"]))
    if not latest["has_data"]:
        st.caption("No runs yet — start a strategy test in **Zσ Strat Tester**.")
    else:
        _latest_profile_ctx = _load_profile_context(_lman.get("profile_id") or latest["profile"])
        if _latest_profile_ctx:
            _render_strategy_synopsis(_latest_profile_ctx, context="stats")

    # ── B. Historical strategy statistics (flat files, no database) ──
    st.markdown("**Historical strategy statistics**")
    hist = ch.historical_stats(_fwd_root, _pf_root)
    if not hist["has_data"]:
        st.info("More stats will appear after additional local paper runs.")
    else:
        ha = st.columns(4)
        ha[0].metric("Runs found", hist["runs_found"])
        ha[1].metric("Total scan ticks", hist["total_ticks"])
        ha[2].metric("Total selected", hist["total_signals"])
        ha[3].metric("Total no-trade", hist["total_no_trade"])
        hb = st.columns(4)
        hb[0].metric("Paper trades", hist["paper_trades"])
        hb[1].metric("Wins / Losses", f"{hist['wins']} / {hist['losses']}")
        hb[2].metric("Realized P&L", ch.fmt_money(hist["realized_pnl"]))
        hb[3].metric("Unrealized P&L", ch.fmt_money(hist["unrealized_pnl"]))
        _reasons = ch.common_no_trade_reasons(_fwd_root)
        if _reasons:
            st.markdown("**Common no-trade reasons**")
            st.dataframe([{"reason": r, "count": c} for r, c in _reasons],
                         use_container_width=True, hide_index=True)
        _best = ch.latest_best_candidate(OUTPUT_ROOT)
        if _best:
            st.caption(
                f"Best candidate (latest EOD): {_best.get('side')} "
                f"{ch.fmt_strike(_best.get('short_strike'))}/{ch.fmt_strike(_best.get('long_strike'))} "
                f"· credit {_best.get('credit')} · score {_best.get('score')}")

    # ── B2. Performance charts (Phase 9I — Streamlit-native, flat-file derived) ──
    st.markdown("**Performance charts**")
    _all_closed: list[dict] = []
    for _prun in (portfolio_ledger.list_portfolio_run_summaries(root=_pf_root) or []):
        _rid = _prun.get("portfolio_run_id")
        if _rid:
            _all_closed.extend(portfolio_ledger.load_closed_trades(_rid, _pf_root) or [])
    _equity = ch.equity_curve_from_closed_trades(_all_closed)
    if not _equity:
        st.info("More stats will appear after additional local paper runs.")
    else:
        _cum = [p["cumulative"] for p in _equity]
        _mdd = ch.max_drawdown(_cum, starting_balance=getattr(session, "starting_balance", None))
        _oc = ch.trade_outcome_counts(_all_closed)
        _mc = st.columns(4)
        _mc[0].metric("Closed trades", _oc["total"])
        _mc[1].metric("Win rate", f"{_oc['win_rate']}%", f"{_oc['wins']}W / {_oc['losses']}L")
        _mc[2].metric("Realized P&L", ch.fmt_money(_cum[-1]))
        _mc[3].metric("Max drawdown", ch.fmt_money(_mdd["max_drawdown"]),
                      f"{_mdd['max_drawdown_pct']}%" if _mdd["max_drawdown_pct"] is not None else None)
        _charts_rendered = False
        if _chart_ready(_cum):
            st.caption("Cumulative realized P&L (equity curve)")
            st.line_chart({"cumulative P&L": _cum})
            _charts_rendered = True
        _dd_series = [d["drawdown"] for d in ch.drawdown_series(_cum)]
        if _chart_ready(_dd_series):
            st.caption("Drawdown (peak-to-trough, $)")
            st.area_chart({"drawdown": _dd_series})
            _charts_rendered = True
        _daily = ch.daily_pnl_from_closed_trades(_all_closed)
        if _daily:
            _daily_pnls = [d["realized_pnl"] for d in _daily]
            if _chart_ready(_daily_pnls):
                st.caption("Daily realized P&L")
                st.bar_chart({"daily P&L": _daily_pnls})
                _charts_rendered = True
            if not simple_mode:
                st.dataframe(_daily, use_container_width=True, hide_index=True)
        if not _charts_rendered:
            st.info("More data will appear after runs.")
        _byprof = ch.pnl_by_profile(_all_closed)
        if _byprof:
            st.caption("P&L by profile")
            st.dataframe(_byprof, use_container_width=True, hide_index=True)
            _hist_profiles = _profile_contexts([
                str(r.get("profile_id")) for r in _byprof if r.get("profile_id")
            ])
            if _hist_profiles:
                st.caption(om.multi_strategy_synopsis(_hist_profiles, context="stats"))
        _exits = ch.exit_reason_counts(_all_closed)
        if _exits:
            st.caption("Exit reasons")
            st.dataframe([{"exit_reason": r, "count": c} for r, c in _exits],
                         use_container_width=True, hide_index=True)
    # Selected signals over runs (from forward run summaries; oldest → newest).
    _runsum = forward_review.list_run_summaries(root=_fwd_root) or []
    if _runsum:
        _sig = [int(r.get("signal_count") or 0) for r in reversed(_runsum)]
        if _chart_ready(_sig):
            st.caption("Selected signals over runs (oldest → newest)")
            st.bar_chart(
                [
                    {"run": index, "selected_signals": value}
                    for index, value in enumerate(_sig, start=1)
                ],
                x="run",
                y="selected_signals",
            )
        else:
            st.info("More data will appear after runs.")

    # ── C. Downloads / exports (operator-friendly labels) ──
    st.markdown("**Downloads / exports**")
    _exports = (ch.forward_export_files(_fwd_root) + ch.portfolio_export_files(_pf_root)
                + [ch.eod_export_file(OUTPUT_ROOT)])
    ecols = st.columns(3)
    _any_export = False
    for _i, _f in enumerate(_exports):
        _col = ecols[_i % 3]
        _label = om.friendly_log_label(_f["filename"])
        if _f["exists"] and _f["text"] is not None:
            _any_export = True
            _col.download_button(f"⬇ {_label}", data=_f["text"],
                                 file_name=_f["filename"], key=f"dl_{_i}")
            if not simple_mode:
                _col.caption(f"_{_f['filename']}_")
        else:
            _col.caption(f"{_label}: _none yet_")
    if not _any_export:
        st.info("No logs yet. Run a strategy test (Zσ Strat Tester) or a paper portfolio to generate logs.")

    # ── D. Review prompt (under an expander to keep the trader view clean) ──
    with st.expander("Copy review prompt (paste into your assistant with the logs)",
                     expanded=False):
        st.code(ch.review_prompt((forward_review.load_latest_manifest() or {}).get("run_id")),
                language="text")

    # ── E. EOD summary (Phase 9I — prominent button + last-generated + staleness
    # + a SAFE one-shot auto-generate; never a background loop, never a broker). ──
    st.divider()
    st.subheader("EOD summary")
    _eod = ch.eod_summary_status(OUTPUT_ROOT, _fwd_root)
    _runner_live = bool(control_ui.status_view(control_ui.get_status()).get("active"))
    if (_eod["stale"] and latest["has_data"] and not _runner_live
            and not st.session_state.get("_eod_autogen_done")):
        try:
            generate_eod_summary(REPO_ROOT)
            st.session_state["_eod_autogen_done"] = True
            _eod = ch.eod_summary_status(OUTPUT_ROOT, _fwd_root)
            st.caption("Auto-generated a fresh EOD summary (was stale on open).")
        except Exception as _exc:   # never let EOD IO break the Stats page
            st.caption(f"Auto EOD generation skipped: {type(_exc).__name__}")
    _ecols = st.columns([2, 1])
    if _ecols[0].button("🧾 Generate / Refresh EOD summary", type="primary", key="eod_gen"):
        try:
            out = generate_eod_summary(REPO_ROOT)
            st.session_state["_eod_autogen_done"] = True
            _eod = ch.eod_summary_status(OUTPUT_ROOT, _fwd_root)
            st.success(f"Wrote {out}")
        except Exception as _exc:   # surface failure, never crash the page
            st.error(f"EOD generation failed: {type(_exc).__name__}: {_exc}")
    _ecols[1].metric("EOD status",
                     "⚠ stale" if _eod["stale"] else ("✅ up to date" if _eod["exists"] else "—"))
    st.caption(f"Last generated: {_eod['generated_at'] or '—'}  ·  for date {_eod['date'] or '—'}  ·  "
               f"{_eod['note']}")
    eod_md = OUTPUT_ROOT / "latest" / "eod_summary.md"
    if eod_md.exists():
        st.markdown(eod_md.read_text(encoding="utf-8"))
    else:
        st.caption("Run a strategy test, then **Generate / Refresh EOD summary** above to populate.")

    st.divider()
    with st.expander("Session config (current overrides)", expanded=False):
        diff = session.diff_against(baseline)
        if diff:
            st.write("Fields edited this session:")
            st.json({k: {"baseline": v[0], "now": v[1]} for k, v in diff.items()})
        else:
            st.caption("No overrides — running with profile defaults.")
        st.subheader("Full session config")
        st.json(session.to_dict())


def render_settings() -> None:
    st.subheader("Session & Paper Settings")
    if session.paper_only:
        st.warning(f"Risk profile **{session.profile_label}** is marked `paper_only`.")
    st.info(
        "These settings affect the current local Streamlit session and paper-lifecycle "
        "defaults. Saved strategy profiles are only changed from **Zσ Strat Builder**."
    )
    if _active_profile is not None:
        _settings_profile_name = om.friendly_text(
            getattr(_active_profile, "profile_name", None)
            or getattr(_active_profile, "profile_id", "—"))
        st.caption(
            "These settings affect local paper lifecycle and sizing; the selected strategy "
            f"remains: {_settings_profile_name}."
        )
    from src.app import readiness_snapshot as _RS

    _latest_readiness = _RS.read_readiness_snapshot()
    st.markdown("**Latest readiness snapshot**")
    if _latest_readiness:
        _snap_cols = st.columns(4)
        _snap_cols[0].metric("Captured", _latest_readiness.get("captured_at") or "—")
        _snap_cols[1].metric("Profile", _latest_readiness.get("profile_id") or "—")
        _snap_cols[2].metric(
            "Quotes",
            f"Quotes: {_latest_readiness.get('quote_label') or 'Unknown'}",
        )
        _snap_cols[3].metric(
            "Start Paper Test",
            "Enabled" if _latest_readiness.get("start_paper_test_enabled") else "Disabled",
        )
        st.caption(_latest_readiness.get("start_reason") or "No readiness reason captured.")
    else:
        st.caption(
            "No readiness snapshot yet. Run the live-readiness diagnostic from Run Strategy."
        )
    with st.form("session_controls"):
        c1, c2, c3 = st.columns(3)
        starting_balance = c1.number_input(
            "Starting balance", value=float(session.starting_balance), step=500.0)
        contracts_per_trade = c2.number_input(
            "Contracts / trade", value=int(session.contracts_per_trade), step=1, min_value=1)
        max_open_positions = c3.number_input(
            "Max open positions", value=int(session.max_open_positions), step=1, min_value=1)

        st.markdown("_Daily caps_")
        d1, d2 = st.columns(2)
        max_daily_loss_dollars = d1.number_input(
            "Max daily loss $", value=float(session.max_daily_loss_dollars or 0.0), step=50.0,
            help="0 = unset")
        max_daily_loss_percent = d2.number_input(
            "Max daily loss %", value=float(session.max_daily_loss_percent or 0.0),
            step=0.01, format="%.3f")

        with st.expander("Advanced — per-trade caps", expanded=False):
            p1, p2, p3, p4 = st.columns(4)
            max_planned_trade_loss_dollars = p1.number_input(
                "Planned $ cap", value=float(session.max_planned_trade_loss_dollars or 0.0), step=50.0)
            max_planned_trade_loss_percent = p2.number_input(
                "Planned % cap", value=float(session.max_planned_trade_loss_percent or 0.0),
                step=0.01, format="%.3f")
            max_theoretical_trade_loss_dollars = p3.number_input(
                "Theoretical $ cap", value=float(session.max_theoretical_trade_loss_dollars or 0.0),
                step=50.0)
            max_theoretical_trade_loss_percent = p4.number_input(
                "Theoretical % cap", value=float(session.max_theoretical_trade_loss_percent or 0.0),
                step=0.01, format="%.3f")

        st.markdown("_Spread + stop_")
        s1, s2, s3 = st.columns(3)
        default_spread_width = s1.number_input(
            "Spread width", value=int(session.default_spread_width), step=1, min_value=1)
        default_stop_variant = s2.selectbox(
            "Stop variant",
            ["BASELINE_CASH_SETTLE", "SL_100_PERCENT_LOSS",
             "SL_150_PERCENT_LOSS", "SL_200_PERCENT_LOSS"],
            index=["BASELINE_CASH_SETTLE", "SL_100_PERCENT_LOSS",
                   "SL_150_PERCENT_LOSS", "SL_200_PERCENT_LOSS"].index(session.default_stop_variant),
            format_func=(om.friendly_enum_label if simple_mode else str),
        )
        profit_target_str = s3.text_input(
            "Profit targets (comma-sep fractions)",
            value=",".join(f"{t:.2f}" for t in session.profit_targets),
        )

        with st.expander("Advanced — filters & decision", expanded=False):
            f1, f2, f3, f4 = st.columns(4)
            min_credit = f1.number_input(
                "Min credit", value=float(session.min_credit), step=0.05, format="%.2f")
            max_bid_ask_width = f2.number_input(
                "Max bid/ask width", value=float(session.max_bid_ask_width), step=0.05, format="%.2f")
            min_distance_from_spot = f3.number_input(
                "Min distance from spot", value=float(session.min_distance_from_spot), step=1.0)
            no_trade_score_threshold = f4.number_input(
                "No-trade score threshold",
                value=float(session.no_trade_score_threshold), step=0.05, format="%.2f")
        submitted = st.form_submit_button(om.BTN_APPLY_SESSION)

    if submitted:
        try:
            parsed_targets = [float(x.strip()) for x in profit_target_str.split(",") if x.strip()]
        except ValueError:
            parsed_targets = session.profit_targets
        new_session = SessionConfig(
            profile_name=session.profile_name,
            profile_label=session.profile_label,
            paper_only=session.paper_only,
            starting_balance=starting_balance,
            contracts_per_trade=int(contracts_per_trade),
            max_open_positions=int(max_open_positions),
            max_daily_loss_dollars=(max_daily_loss_dollars or None),
            max_daily_loss_percent=(max_daily_loss_percent or None),
            max_planned_trade_loss_dollars=(max_planned_trade_loss_dollars or None),
            max_planned_trade_loss_percent=(max_planned_trade_loss_percent or None),
            max_theoretical_trade_loss_dollars=(max_theoretical_trade_loss_dollars or None),
            max_theoretical_trade_loss_percent=(max_theoretical_trade_loss_percent or None),
            default_spread_width=int(default_spread_width),
            default_stop_variant=default_stop_variant,
            profit_targets=parsed_targets,
            no_trade_score_threshold=no_trade_score_threshold,
            min_credit=min_credit,
            max_bid_ask_width=max_bid_ask_width,
            min_distance_from_spot=min_distance_from_spot,
            minimum_reward_risk=session.minimum_reward_risk,
        )
        diff = new_session.diff_against(session)
        for field, (old, new) in diff.items():
            log_config_change(
                OUTPUT_ROOT, field=field, old_value=old, new_value=new,
                active_strategy=st.session_state["active_strategy"],
                active_risk_profile=st.session_state["active_profile"],
            )
        st.session_state["session_config"] = new_session
        st.toast(f"Applied {len(diff)} session change(s); logged to config_change_log.jsonl")
        st.rerun()

    st.divider()
    st.markdown("**Paper lifecycle (Portfolio runner) — env / CLI / config only**")
    st.caption(
        "TP / SL / EOD + limits for `run_portfolio_forward` are NOT part of the Phase 6 "
        "profile schema. They are read from env (`PAPER_*`) / CLI flags / "
        "`config/portfolio_profiles.yaml`. Shown here read-only for reference."
    )
    if simple_mode:
        st.caption("Paper lifecycle JSON is available in Advanced Mode.")
    else:
        try:
            from src.paper.models import PaperLifecycleConfig
            st.json(PaperLifecycleConfig.from_env().to_dict(), expanded=False)
        except Exception as exc:  # never block settings on this
            st.caption(f"_(could not load PaperLifecycleConfig: {type(exc).__name__})_")


# ──────────────────────────────────────────────────────────────────────
# Status strip + tabs (header is rendered at the TOP of the app)
# ──────────────────────────────────────────────────────────────────────

# Phase 9D — compact operational status strip (above the tabs).
_strip_runner = control_ui.status_view(control_ui.get_status())
_strip_hb = forward_review.load_latest_heartbeat() or {}
_strip_psum = portfolio_ledger.load_summary("latest") or {}
_strip_profile = (chosen_profile_id if chosen_profile_id and chosen_profile_id != "(none)"
                  else (st.session_state.get("active_strategy") or "—"))
_strip_last = _strip_hb.get("latest_decision") or "—"
_strip_real = _strip_psum.get("realized_pnl", paper_account.realized_pnl)
# Phase 10B — friendly, READ-ONLY status summary. Short 1–2 word values so cards
# never clip (no raw TRADE_CALL_CREDIT / chain_returned_validation_failed / IDs).
_strip_cells = om.header_status_cells(
    strategy=om.strategy_display_name(st.session_state.get("active_strategy")),
    structure=om.provider_short(resolved_structure_name),
    quotes=om.quote_state_label(QUOTE_STATUS["state"],
                                QUOTE_STATUS["details"].get("top_blocker")),
    runner=om.runner_state_label(_strip_runner["status"]),
    last_signal=om.decision_label(_strip_last),
    paper_pnl=ch.fmt_money(_strip_real),
)
st.caption("Status Summary — read-only (this row reports state; it does not run anything).")
_strip_cols = st.columns(len(_strip_cells))
for _col, (_lbl, _val) in zip(_strip_cols, _strip_cells, strict=False):
    if _lbl == "Safety":
        _col.markdown(f"<div class='zsa-pill-cell' title='No broker execution'>{_lbl}<br>"
                      + ui.pill("NO BROKER", "green") + "</div>", unsafe_allow_html=True)
    else:
        _col.metric(_lbl, _val)
st.caption("▶ To run a strategy, open the **🧪 Run Strategy** tab (Choose → Preview → "
           "Start Paper Test → Review). Paper test only — no broker execution.")

# Phase 9E — branded tab labels (Zσ Strat Tester / Paper Portfolio; no "Forward Runner").
(tab_live, tab_builder, tab_tester, tab_portfolio, tab_backtests, tab_logs,
 tab_settings) = st.tabs(om.tab_labels())

with tab_live:
    render_symbol_health()
    render_operator_decision()   # Phase 9H — operator read, above Market / structure
    render_provider_status()
    render_market()
    render_candidates()
with tab_builder:
    render_strategy_builder()
with tab_tester:
    render_forward_runner()
with tab_portfolio:
    render_portfolio()
    # Phase 9I — Manual Paper Desk is hidden in Simple Mode (not part of Dan's
    # workflow); available in Advanced Mode only.
    if not simple_mode:
        st.divider()
        render_manual_desk()
    else:
        st.caption("Manual paper entry is available in Advanced Mode "
                   "(Manual entries are local records only).")
with tab_backtests:
    render_backtests()
with tab_logs:
    render_logs()
with tab_settings:
    render_settings()
