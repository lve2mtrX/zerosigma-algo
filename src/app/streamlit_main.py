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
from src.providers.quotes.factory import build_quote_provider  # noqa: E402
from src.providers.quotes.tastytrade_provider import (  # noqa: E402
    TastytradeConfigurationError,
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
chain = quote_provider.get_option_chain(SYMBOL, expiry=structure.expiry)
quote_status = quote_provider.status()

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


# ──────────────────────────────────────────────────────────────────────
# Section renderers (each renders one cockpit panel)
# ──────────────────────────────────────────────────────────────────────

def render_symbol_health() -> None:
    """Phase 9F — compact symbol-health panel. Distinguishes Tasty MARKET DATA from
    ZerσSigma EXPOSURES, and SANDBOX from unavailable LIVE data (sandbox reads
    'sandbox mock' / 'sandbox stub', never an alarming 'unavailable')."""
    sandbox = om.is_sandbox(resolved_structure_name, resolved_quote_name)
    market_data_available = chain is not None
    exposures_available = (
        structure_error is None and structure is not None
        and (structure.exposures.da_gex_signed is not None
             or structure.exposures.maxvol is not None)
    )
    view = om.symbol_health_view(
        symbol=SYMBOL, sandbox=sandbox,
        market_data_available=market_data_available, exposures_available=exposures_available)
    cols = st.columns(5)
    cols[0].metric("Symbol", view["symbol"])
    cols[1].metric("Tasty market data", view["market_data"])
    cols[2].metric("ZerσSigma exposures", view["exposures"])
    cols[3].metric("Strategy eligible", view["eligible"])
    cols[4].markdown("<div class='zsa-pill-cell'>" + ui.pill("NO BROKER EXECUTION", "green")
                     + "</div>", unsafe_allow_html=True)
    if view["note"]:
        st.caption(view["note"])
    if view["reason"]:
        st.warning(view["reason"])


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
                    "`/exposure/series` is skipped, so **PUT_CEILING / CALL_FLOOR / "
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
    left, right = st.columns(2)
    left.markdown(f"**Structure Read**  \n{layer['structure_read']}")
    left.markdown(f"**Trade Bias**  \n{layer['trade_bias']}")
    left.markdown(f"**Candidate Risk**  \n{layer['candidate_risk']}")
    right.markdown(f"**Best Eligible Setup**  \n{layer['best_eligible_setup']}")
    right.markdown(f"**Why / Why Not**  \n{layer['why_why_not']}")
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
        pc[_i].metric(_e["label"], _e["strike_fmt"],
                      f"{_e['distance_fmt']} pts" if _e["available"] else None)
    cf = st.columns(3)
    for _i, _e in enumerate(ws["call_floors"]):
        cf[_i].metric(_e["label"], _e["strike_fmt"],
                      f"{_e['distance_fmt']} pts" if _e["available"] else None)
    # ── Dominant wing (Phase 9J — true WDS, NOT nearest distance) ──
    wd = ch.wing_dominance(ex, spot_val)
    st.markdown("**Dominant wing (WDS)** — how clean the 10K wing is vs the adjacent strike (W2)")
    if wd["wds_source"] == "true":
        _side = wd["dominant_wing_side"]
        _w1v = wd["call_w1_volume"] if _side == "CALL" else wd["put_w1_volume"]
        _w2s = wd["call_w2_strike"] if _side == "CALL" else wd["put_w2_strike"]
        _w2v = wd["call_w2_volume"] if _side == "CALL" else wd["put_w2_volume"]
        _wsr = wd["call_wsr"] if _side == "CALL" else wd["put_wsr"]
        dcols = st.columns(4)
        dcols[0].metric(f"Dominant {wd['dominant_wing_label']}",
                        ch.fmt_strike(wd["dominant_wing_strike"]),
                        f"WDS {wd['dominant_wing_wds_pct']} · Tier {wd['dominant_wing_tier']}")
        dcols[1].metric("W1 volume", ch.fmt_count(_w1v))
        dcols[2].metric(f"W2 @ {ch.fmt_strike(_w2s)}", ch.fmt_count(_w2v))
        dcols[3].metric("WSR (W2/W1)", f"{round(_wsr * 100)}%" if _wsr is not None else "—")
        st.caption(wd["wds_reason"])
    else:
        st.caption(f"True WDS unavailable — {wd['wds_reason']}")
    near = ws["nearest_wing"]
    near_txt = (f"{near['label']} {near['strike_fmt']} ({near['distance_fmt']} pts)"
                if near else "unavailable")
    _dom_txt = (f"{wd['dominant_wing_label']} {ch.fmt_strike(wd['dominant_wing_strike'])} "
                f"(WDS {wd['dominant_wing_wds_pct']}, Tier {wd['dominant_wing_tier']})"
                if wd["wds_source"] == "true" else "unavailable")
    _brd = (f"{wd['nearest_wing_distance_points']:.2f} pts"
            if wd["nearest_wing_distance_points"] is not None else "—")
    st.caption(f"Dominant wing (WDS): **{_dom_txt}**  ·  Nearest wing (immediate breach risk): "
               f"**{near_txt}** — {_brd} from spot")
    if not (ws["put_ceilings"][2]["available"] or ws["call_floors"][2]["available"]):
        st.caption("10K wings (and true WDS) require upstream exposure volume ≥ 10,000 "
                   "(subscription series); unavailable in sandbox / mock data.")

    if chain is None:
        # Phase 9I — say WHY (concise in Simple, raw provider state in Advanced).
        _qstat = ch.quote_chain_status(
            resolved_quote_name=resolved_quote_name, quote_status=quote_status,
            quote_provider_error=quote_provider_error, structure_error=structure_error,
            chain=None)
        st.warning(f"{_qstat['simple_reason']} Showing Zσ structure context only.")
        _actions = ch.chain_unavailable_actions(
            resolved_quote_name, last_error=getattr(quote_status, "last_error", None))
        for _a in (_actions[:2] if simple_mode else _actions):
            st.caption(f"• {_a}")
        if not simple_mode:
            with st.expander("Quote diagnostics (raw provider status)", expanded=False):
                st.json(_qstat["advanced"], expanded=False)

    st.caption(
        f"Structure from `{structure.source}` @ {structure.quote_ts.isoformat()}  ·  "
        f"chain from `{chain.provider_name if chain else '—'}` "
        f"@ {chain.quote_ts.isoformat() if chain else '—'}  ·  "
        f"expiry {structure.expiry}  ·  DTE {structure.dte}"
    )

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

            rows.append({
                "side": c.side,
                "short K": c.short_strike,
                "long K": c.long_strike,
                "short b/a/m": _fmt_quote(short_leg),
                "long b/a/m": _fmt_quote(long_leg),
                "quote": quote_badge,
                "credit ($)": round(c.credit, 2),
                "width": round(c.max_risk + c.credit, 2),
                "theoretical $": round(theoretical, 0),
                "planned $": round(planned, 0),
                "R:R": round(c.reward_risk, 2),
                "b/a quality": round(c.meta.get("bid_ask_quality", 0.0), 2),
                "b/a mode": c.meta.get("bid_ask_quality_mode") or "—",
                "breakeven": round(c.breakeven, 2),
                "score": round(c.score, 2),
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
            row["selected"] = "✅" if _sel.per_row[i]["selected_trade"] else ""
        st.dataframe(rows, use_container_width=True, hide_index=True)

        if _sel.selected_trade:
            i = _sel.selected_indices[0]
            sc = candidates[i]
            st.success(
                f"Daily selector (`{_sel.daily_selector_mode}`): selected "
                f"{sc.side} {sc.short_strike}/{sc.long_strike} "
                f"(score {sc.score:.3f}, credit {sc.credit:.2f}) — "
                f"{_sel.per_row[i]['selector_reason']}"
            )
        else:
            st.info(
                f"Daily selector (`{_sel.daily_selector_mode}`): NO_TRADE — "
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
            label = (
                f"{sel_badge}{c.side}  K {c.short_strike}/{c.long_strike}  "
                f"score {c.score:.4f}  ({gap_str})  rejection={c.rejection_type or '—'}"
            )
            with st.expander(label, expanded=(c.rejection_type == "selected")):
                etop = st.columns(4)
                etop[0].metric("Score", f"{c.score:.4f}")
                etop[1].metric(
                    "Threshold",
                    f"{c.score_threshold:.2f}" if c.score_threshold is not None else "—",
                )
                etop[2].metric(
                    "Gap",
                    f"{c.score_gap_to_threshold:+.4f}"
                    if c.score_gap_to_threshold is not None else "—",
                )
                etop[3].metric("Rejection type", c.rejection_type or "—")

                anchor_cols = st.columns(4)
                anchor_cols[0].metric("Anchor", c.meta.get("anchor_source") or "—")
                av = c.meta.get("anchor_volume")
                anchor_cols[1].metric(
                    "Anchor volume", f"{av:,.0f}" if isinstance(av, (int, float)) else "—",
                )
                anchor_cols[2].metric("Volume source", c.meta.get("anchor_volume_source") or "—")
                anchor_cols[3].metric(
                    "structure_strength_source", c.meta.get("structure_strength_source") or "—",
                )

                if c.weak_components:
                    st.markdown(
                        "**Weakest components:** " + ", ".join(f"`{w}`" for w in c.weak_components)
                    )
                if c.rejection_reasons:
                    st.markdown(
                        "**Filter reasons:** " + ", ".join(f"`{r}`" for r in c.rejection_reasons)
                    )
                short_meta = c.meta.get("short_leg") or {}
                long_meta = c.meta.get("long_leg") or {}
                if any(
                    k in short_meta or k in long_meta
                    for k in ("validation_passed", "validation_rejection_reason", "quote_time")
                ):
                    qcols = st.columns(2)
                    for col, leg_label, leg in (
                        (qcols[0], "Short leg", short_meta),
                        (qcols[1], "Long leg", long_meta),
                    ):
                        passed = leg.get("validation_passed")
                        reason = leg.get("validation_rejection_reason")
                        qtime = leg.get("quote_time")
                        badge = (
                            "—" if passed is None else
                            ("✓ pass" if passed else f"✗ {reason or 'fail'}")
                        )
                        col.metric(f"{leg_label} quote", badge, qtime or "")

                rd = c.meta.get("_readiness") or {}
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
                        "marginal" if c.marginal_score else (
                            "passed" if c.score_edge_passed else "below"
                        ),
                    )
                    sc[1].metric("Quote bucket", rd.get("quote_quality_bucket") or "—")
                    sc[2].metric("Risk type", rd.get("risk_rejection_type") or "—")
                    sc[3].metric(
                        "Eligible (base)",
                        "yes" if rd.get("selector_eligible_base") else "no",
                        rd.get("selector_readiness_note") or "",
                    )
                    blockers = rd.get("selector_blockers") or []
                    if blockers:
                        st.markdown(
                            "**Selector blockers:** " + ", ".join(f"`{b}`" for b in blockers)
                        )
                    p42 = st.columns(4)
                    p42[0].metric(
                        "b/a quality", f"{c.meta.get('bid_ask_quality', 0.0):.2f}",
                        c.meta.get("bid_ask_quality_mode") or "—",
                    )
                    p42[1].metric("b/a reason", c.meta.get("bid_ask_quality_reason") or "—")
                    skew_det = c.meta.get("quote_clock_skew_detected")
                    p42[2].metric(
                        "Clock skew",
                        "yes" if skew_det else ("no" if skew_det is False else "—"),
                    )
                    skew_s = c.meta.get("quote_clock_skew_seconds")
                    p42[3].metric(
                        "Skew (s)", f"{skew_s:.2f}" if isinstance(skew_s, (int, float)) else "—",
                    )
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

    decision = strat.select(candidates, params)
    st.subheader("Decision")
    badge = {"TRADE_CALL_CREDIT": "success", "TRADE_PUT_CREDIT": "success", "NO_TRADE": "warning"}
    getattr(st, badge.get(decision.decision, "info"))(decision.decision)
    st.write(decision.explanation)


def _render_profile_info_card(info: dict) -> None:
    """Shared profile info card (Builder + Tester). Phase 9G — shows side policy,
    entry window, target time, threshold, TP/SL, selector mode + dynamic-exit
    status alongside the basics. Reads the pure ``om.profile_info_fields`` map."""
    grid = (
        "Profile", "Profile ID", "Symbol", "Strategy",
        "Entry window", "Target time", "Target DTE", "Threshold",
        "Side policy", "Selector mode", "Take profit (TP)", "Stop loss (SL)",
        "Risk profile", "Data source",
    )
    cols = st.columns(4)
    for _i, _k in enumerate(grid):
        cols[_i % 4].metric(_k, ui.dash(info.get(_k)))
    st.caption(f"**Dynamic exits:** {info.get('Dynamic exits')}")
    st.caption(f"**Designed to test:** {info.get('Designed to test')}")
    st.caption(
        f"Enabled: `{info.get('Enabled')}`  ·  Safety: {info.get('Safety')}  ·  "
        "TP/SL shown is the preset's intent; the paper lifecycle currently applies "
        "the PAPER_* env values (per-profile wiring deferred).")


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
            "Show comparison and legacy profiles", value=False, key="builder_show_all",
            help="Off = only your Main Strategies. On = comparison, research, and legacy too.")
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
        _render_profile_info_card(om.profile_info_fields(sel_dict))
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
        st.info("Choose **Create new profile**, or pick a preset above and **Edit** / **Clone** it.")
        return

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
            _ds_idx = list(om.DATA_SOURCES).index(om.providers_to_data_source(
                base.get("structure_provider") or "stub", base.get("quote_provider") or "mock"))
            s_ds = r4[0].radio("Data source", list(om.DATA_SOURCES), index=_ds_idx)
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
            s_validate = st.form_submit_button("Validate strategy")
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
                "target_dte": int(s_dte), "risk_profile": s_risk, "enabled": True,
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
    st.subheader("🧪 Zσ Strat Tester — local paper strategy test")
    st.markdown(ui.pill(control_ui.EXECUTION_BANNER, "green"), unsafe_allow_html=True)
    st.caption(
        "**Step 1** select a strategy profile → **Step 2** preview → **Step 3** start a "
        "local paper test → **Step 4** stop → **Step 5** review the latest result. "
        "LOCAL PAPER TEST ONLY — no broker orders, no order preview, no live execution."
    )
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

    _hb = forward_review.load_latest_heartbeat() or {}
    view = control_ui.status_view(control_ui.get_status())
    # Friendly "Latest test run" label; the full run_id lives in Advanced details.
    _latest_run_label = om.friendly_run_label(
        run_id=view["run_id"] or _hb.get("run_id"),
        profile_name=(_tester_prof.profile_name if _tester_prof else None),
        strategy_id=(_tester_prof.strategy_id if _tester_prof else None),
        started_at=_hb.get("started_at") or _hb.get("latest_tick_time"))
    if simple_mode:
        # Simple Mode hides PID + raw run_id; shows Running: Yes/No + friendly label.
        mcols = st.columns(3)
        mcols[0].metric("Runner", view["status"])
        mcols[1].metric("Running", om.running_display(view["active"]))
        mcols[2].metric("Latest test run", _latest_run_label)
    else:
        mcols = st.columns(4)
        mcols[0].metric("Runner", view["status"])
        mcols[1].metric("Running", om.running_display(view["active"]))
        mcols[2].metric("PID", str(view["pid"] or "—"))
        mcols[3].metric("Latest test run", _latest_run_label)
    if view["stale"]:
        st.warning("Control state is STALE (PID not alive). Use **Clear stale runner** below.")
    if view["active"] or view.get("status") in ("running", "starting", "stopping"):
        st.warning("⚠ " + om.runner_busy_message(
            view.get("profile_id") or chosen_profile_id, view.get("status")))
    # Phase 9D — latest decision + open paper P&L at a glance.
    _rs_hb = _hb
    _rs_ps = portfolio_ledger.load_summary("latest") or {}
    _rs_sel = (f"{_rs_hb.get('latest_decision')} (selected)" if _rs_hb.get("selected_trade")
               else (_rs_hb.get("latest_decision") or "—"))
    st.caption(
        f"Latest decision: **{_rs_sel}**  ·  open paper trades: "
        f"**{_rs_ps.get('open_trade_count', 0)}**  ·  realized P&L: "
        f"**{ch.fmt_money(_rs_ps.get('realized_pnl', 0.0))}**  ·  total P&L: "
        f"**{ch.fmt_money(_rs_ps.get('total_pnl', 0.0))}**"
    )

    _runner_summaries = {s["profile_id"]: s for s in pb.list_summaries() if s.get("ok")}
    _summaries_list = list(_runner_summaries.values())
    _all_runner_profiles = om.order_profiles_for_dropdown(list(_runner_summaries))

    def _runner_label(pid: str) -> str:
        s = _runner_summaries.get(pid, {})
        return om.profile_dropdown_label(pid, s.get("profile_name"), s.get("preset_kind"))

    # ── Phase 9I — Simple Mode shows ONLY Main Strategies (the 9G dynamic-first
    # presets); a checkbox reveals comparison + research + legacy. Advanced = all. ──
    if simple_mode:
        _show_all = st.checkbox(
            "Show comparison and legacy profiles", value=False, key="runner_show_all",
            help="Off = only your Main Strategies. On = comparison tests, research, "
                 "and legacy/archived profiles too.")
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
            with st.expander("Selected profile details", expanded=simple_mode):
                _render_profile_info_card(om.profile_info_fields(_sel_runner_dict))

    # ── Phase 9I — App vs Profile data source for THIS run (never silently mismatch) ──
    _ovr_struct = _ovr_quote = None
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
    bcols = st.columns(5)
    if bcols[0].button(om.BTN_REFRESH, key="runner_refresh"):
        st.rerun()
    if bcols[1].button(om.BTN_PREVIEW, disabled=not runner_profiles or not can,
                       key="runner_preview",
                       help="Runs a single local paper-test tick (no broker)."):
        ok, msg, pid = control_ui.start_runner(
            sel_profile, once=True, market_hours_only=bool(market_hours_only),
            structure_provider=_ovr_struct, quote_provider=_ovr_quote)
        (st.success if ok else st.error)(("Preview launched. " if ok else "") + str(msg)
                                         + (f" (pid {pid})" if pid else ""))
        if ok:
            st.rerun()
    if bcols[2].button(om.BTN_START_TEST, type="primary",
                       disabled=not runner_profiles or not can, key="runner_start"):
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
    if bcols[4].button(om.BTN_CLEAR_STALE, key="runner_cleanup"):
        ok, msg = control_ui.cleanup()
        (st.success if ok else st.warning)(msg)
        st.rerun()
    if not can:
        st.caption(f"_Start / Preview disabled: {why}_")
    st.checkbox(
        "⚠ Force stop (terminate the stored PID) — use only if graceful stop fails",
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
        st.markdown("**Open paper trades & unrealized P&L**")
        if _open_rows:
            st.dataframe([{k: r.get(k) for k in _pf_cols} for r in _open_rows],
                         use_container_width=True, hide_index=True)
        else:
            st.caption("No open paper trades. Start a portfolio forward run or wait for a selected signal.")
        _closed_rows = portfolio_ledger.load_closed_trades("latest")
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
        st.caption("Cumulative realized P&L (equity curve)")
        st.line_chart({"cumulative P&L": _cum})
        st.caption("Drawdown (peak-to-trough, $)")
        st.area_chart({"drawdown": [d["drawdown"] for d in ch.drawdown_series(_cum)]})
        _daily = ch.daily_pnl_from_closed_trades(_all_closed)
        if _daily:
            st.caption("Daily realized P&L")
            st.bar_chart({"daily P&L": [d["realized_pnl"] for d in _daily]})
            if not simple_mode:
                st.dataframe(_daily, use_container_width=True, hide_index=True)
        _byprof = ch.pnl_by_profile(_all_closed)
        if _byprof:
            st.caption("P&L by profile")
            st.dataframe(_byprof, use_container_width=True, hide_index=True)
        _exits = ch.exit_reason_counts(_all_closed)
        if _exits:
            st.caption("Exit reasons")
            st.dataframe([{"exit_reason": r, "count": c} for r, c in _exits],
                         use_container_width=True, hide_index=True)
    # Selected signals over runs (from forward run summaries; oldest → newest).
    _runsum = forward_review.list_run_summaries(root=_fwd_root) or []
    if _runsum:
        _sig = [int(r.get("signal_count") or 0) for r in reversed(_runsum)]
        if any(_sig):
            st.caption("Selected signals over runs (oldest → newest)")
            st.bar_chart({"selected signals": _sig})

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
_strip_selected = _strip_hb.get("latest_decision") if _strip_hb.get("selected_trade") else "—"
_strip_open = _strip_psum.get("open_trade_count", len(paper_account.open_positions))
_strip_real = _strip_psum.get("realized_pnl", paper_account.realized_pnl)
_strip_cells = ch.status_strip_cells(
    run_profile=_strip_profile, structure_name=resolved_structure_name,
    quote_name=resolved_quote_name, runner_status=_strip_runner["status"],
    selected_trade=_strip_selected, open_trades=_strip_open, realized_pnl=_strip_real,
)
_strip_cols = st.columns(len(_strip_cells) + 1)
for _col, (_lbl, _val) in zip(_strip_cols, _strip_cells, strict=False):
    _col.metric(_lbl, _val)
_strip_cols[-1].markdown(
    "<div class='zsa-pill-cell'>" + ui.pill("NO BROKER EXECUTION", "green") + "</div>",
    unsafe_allow_html=True,
)

# Phase 9E — branded tab labels (Zσ Strat Tester / Paper Portfolio; no "Forward Runner").
tab_live, tab_builder, tab_tester, tab_portfolio, tab_logs, tab_settings = st.tabs(om.tab_labels())

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
with tab_logs:
    render_logs()
with tab_settings:
    render_settings()
