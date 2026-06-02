"""ZerσSigma Algo Cockpit — Streamlit shell.

Phase 1 wiring:
  - sidebar: strategy + risk-profile selectors, "Reset to defaults", provider status
  - session-control form: editable overrides for every risk profile field
  - structure panel: spot + maxvol + walls + gamma + put_ceiling / call_floor + ddoi
  - candidate table with planned + theoretical $ under the session profile
  - decision card
  - manual trade entry → in-memory PaperAccount + CSV mirrors
  - open positions table
  - realized + unrealized P&L
  - "Generate EOD summary" button

No live providers. Stub structure + null/mock quote provider only.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.app.session_state import SessionConfig  # noqa: E402
from src.config.strategy_profiles import list_profiles as list_run_profiles  # noqa: E402
from src.forward import control as forward_control  # noqa: E402
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
# Boot
# ──────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="ZerσSigma Algo Cockpit",
    page_icon="📊",
    layout="wide",
)

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


# Default-pick profile + strategy
if "active_strategy" not in st.session_state:
    st.session_state["active_strategy"] = (
        next(iter(STRATEGIES)) if STRATEGIES else None
    )
if "active_profile" not in st.session_state:
    st.session_state["active_profile"] = (
        CFG.active_risk_profile if CFG.active_risk_profile in profile_names else profile_names[0]
    )
if "session_config" not in st.session_state:
    _init_session(st.session_state["active_profile"])


# ──────────────────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("ZerσSigma Algo")
    st.caption("Phase 1 cockpit — no live execution")

    # Strategy selector
    new_strategy = st.selectbox(
        "Strategy",
        options=list(STRATEGIES.keys()) or ["(none)"],
        index=(list(STRATEGIES.keys()).index(st.session_state["active_strategy"])
               if st.session_state["active_strategy"] in STRATEGIES else 0),
    )
    if new_strategy != st.session_state["active_strategy"]:
        st.session_state["active_strategy"] = new_strategy

    # Risk profile selector
    new_profile = st.selectbox(
        "Risk profile (session default)",
        options=profile_names,
        index=profile_names.index(st.session_state["active_profile"]),
        help="Profiles are TEMPLATES — edit the form below to override.",
    )
    if new_profile != st.session_state["active_profile"]:
        st.session_state["active_profile"] = new_profile
        _init_session(new_profile)
        st.rerun()

    if st.button("Reset to profile defaults"):
        _init_session(st.session_state["active_profile"])
        st.rerun()

    st.divider()
    st.caption("Provider selection (config/providers.yaml + .env)")
    # Structure provider is user-selectable in the cockpit; defaults to the
    # YAML/env value. Switching to zerosigma_api without creds is allowed —
    # the factory falls back to stub and logs the reason.
    available_structure = ["stub", "zerosigma_api"]
    default_structure_idx = (
        available_structure.index(CFG.providers.structure_active)
        if CFG.providers.structure_active in available_structure else 0
    )
    chosen_structure = st.selectbox(
        "Structure provider",
        options=available_structure,
        index=default_structure_idx,
        help="Stub = deterministic mock. zerosigma_api = read-only against the ZS API "
             "(requires ZS_API_AUTH_MODE + credentials in .env). Empty creds → falls "
             "back to stub automatically.",
    )
    # Phase 4 — quote provider selector. Default = whatever the factory
    # resolves (CLI override > QUOTE_PROVIDER env > YAML > "mock"). Picking
    # `tastytrade` without TASTY_* OAuth env vars triggers a graceful
    # fallback to mock with a visible warning below.
    available_quotes = ["mock", "null", "tastytrade"]
    yaml_quote_default = (CFG.providers.quotes_active or "mock").lower()
    default_quote_idx = (
        available_quotes.index(yaml_quote_default)
        if yaml_quote_default in available_quotes else 0
    )
    chosen_quote = st.selectbox(
        "Quote provider",
        options=available_quotes,
        index=default_quote_idx,
        help="Mock = deterministic synthesized chain (no network). "
             "null = no quotes (force manual marks). "
             "tastytrade = live Tasty REST quotes (requires TASTY_OAUTH_* "
             "or TASTY_USERNAME/PASSWORD in .env). Misconfigured tasty → "
             "falls back to mock with a warning.",
    )
    st.caption(f"Execution mode:     `{CFG.providers.execution_active}`")

    # Phase 6 — saved run-profile selector (read/display + prefill the daily
    # selector default). SELECTION/CONFIG ONLY — no execution. Picking a profile
    # prefills the daily-selector dropdown below; full prefill of every control
    # is a Phase 6.1 nicety.
    _prof_results = [r for r in list_run_profiles() if r.ok and r.profile]
    _prof_options = ["(none)"] + [r.profile.profile_id for r in _prof_results]
    chosen_profile_id = st.selectbox(
        "Run profile (Phase 6)",
        options=_prof_options,
        index=0,
        help="Saved strategy run-profiles (profiles/*.yaml). Read-only here — "
             "the scanner CLI applies a profile via --profile. Selecting one "
             "prefills the daily-selector default below.",
    )
    _active_profile = next(
        (r.profile for r in _prof_results if r.profile.profile_id == chosen_profile_id),
        None,
    )
    if _active_profile is not None:
        st.caption(
            f"`{_active_profile.profile_id}` — {_active_profile.profile_name}  ·  "
            f"selector=`{_active_profile.daily_selector}`  ·  dte={_active_profile.target_dte}  ·  "
            f"hash `{_active_profile.profile_hash()}`"
        )

    # Phase 5 — daily trade selector mode (SELECTION ONLY; no execution).
    _sel_yaml = (
        (CFG.scanner.get("selector") or {}).get("daily_trade_selector")
        if isinstance(CFG.scanner, dict) else None
    ) or DEFAULT_SELECTOR_MODE
    # Prefill from the chosen run-profile when one is selected (Phase 6).
    _sel_default = _active_profile.daily_selector if _active_profile else _sel_yaml
    chosen_selector = st.selectbox(
        "Daily selector",
        options=list(SELECTOR_MODES),
        index=(list(SELECTOR_MODES).index(_sel_default)
               if _sel_default in SELECTOR_MODES else 0),
        help="Chooses AT MOST ONE candidate from the generated set. Selection "
             "only — never executes or submits. score_best_valid = highest-score "
             "eligible (default).",
    )


# Acquire snapshots — explicit separation of structure vs quotes.
#   StructureProvider → structure context (MaxVol / DA-GEX / ceilings / floors / DDOI)
#   QuoteProvider     → spot + full option chain (bid/ask/mid/volume per strike)
structure_provider, resolved_structure_name = build_structure_provider(
    CFG, override=chosen_structure,
)

# Streamlit MUST stay loadable even when Tasty is misconfigured — fall
# back to mock visibly. CLI scanner is the strict path.
quote_provider_error: str | None = None
try:
    quote_provider, resolved_quote_name = build_quote_provider(
        override=chosen_quote,
        yaml_active=CFG.providers.quotes_active,
        fallback_on_misconfig=True,    # never block UI on bad creds
    )
except TastytradeConfigurationError as exc:
    # Defensive — fallback_on_misconfig=True should make this unreachable.
    quote_provider_error = f"{type(exc).__name__}: {exc}"
    from src.providers.quotes.mock_provider import MockQuoteProvider
    quote_provider, resolved_quote_name = MockQuoteProvider(), "mock"

SYMBOL = CFG.scanner.get("symbols", ["SPX"])[0]
try:
    structure = structure_provider.get_snapshot(SYMBOL)
    structure_error: str | None = None
except Exception as exc:
    # Never surface secrets — just the exception type + sanitized message.
    structure_error = f"{type(exc).__name__}: {exc}"
    structure_provider = StubStructureProvider()
    resolved_structure_name = "stub"
    structure = structure_provider.get_snapshot(SYMBOL)
spot_quote = quote_provider.get_spot(SYMBOL)
chain      = quote_provider.get_option_chain(SYMBOL, expiry=structure.expiry)
quote_status = quote_provider.status()


# ──────────────────────────────────────────────────────────────────────
# Header + provider status
# ──────────────────────────────────────────────────────────────────────

st.title("ZerσSigma Algo Cockpit")
st.caption("Scanner · decision log · manual + paper trade tracking · EOD summary")

session: SessionConfig = st.session_state["session_config"]
baseline: SessionConfig = st.session_state["session_baseline"]
paper_account: PaperAccount = st.session_state["paper_account"]

with st.expander("Provider status", expanded=True):
    cols = st.columns(3)
    cols[0].metric(
        "StructureProvider",
        f"{structure_provider.name}",
        f"context @ {structure.quote_ts.strftime('%H:%M:%S')}",
    )
    cols[1].metric(
        "QuoteProvider",
        f"{quote_provider.name}",
        (
            f"chain @ {chain.quote_ts.strftime('%H:%M:%S')}"
            if chain else
            ("spot @ " + spot_quote.ts.strftime("%H:%M:%S") if spot_quote else "no quotes")
        ),
    )
    # Phase 4 — surface broker-side root resolution + misconfig fallback
    if chain is not None and chain.resolved_root_symbol:
        cols[1].caption(
            f"root=`{chain.resolved_root_symbol}` "
            f"(source: {chain.root_resolution_source or '-'})"
        )
    if chosen_quote == "tastytrade" and resolved_quote_name != "tastytrade":
        st.warning(
            "Selected `tastytrade` but provider is not configured "
            "(missing TASTY_OAUTH_* or TASTY_USERNAME/PASSWORD). Running "
            "on the mock chain. See `.env.example` for required variables."
        )
    if quote_provider_error is not None:
        st.warning(f"Quote provider boot error → fell back to mock. `{quote_provider_error}`")
    cols[2].metric(
        "ExecutionProvider",
        CFG.providers.execution_active,
        "no live execution",
    )
    if not quote_status.connected:
        st.caption(f"_QuoteProvider notes: {quote_status.notes or 'disconnected'}_")

    # If the user picked zerosigma_api but the factory degraded to stub —
    # or get_snapshot raised — surface a clear warning. Never display
    # tokens, passwords, or service keys.
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
    # When the real provider is connected, surface its status (no secrets).
    if resolved_structure_name == "zerosigma_api" and hasattr(structure_provider, "status"):
        provider_status = structure_provider.status()
        # Promote auth_mode + public_only to a top-line caption so it's not
        # buried in the JSON expander.
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


# ──────────────────────────────────────────────────────────────────────
# Session controls (editable overrides on top of profile defaults)
# ──────────────────────────────────────────────────────────────────────

if session.paper_only:
    st.warning(f"Risk profile **{session.profile_label}** is marked `paper_only`.")

with st.expander("Session controls (override the profile for this session)", expanded=False):
    with st.form("session_controls"):
        c1, c2, c3 = st.columns(3)
        starting_balance = c1.number_input(
            "Starting balance", value=float(session.starting_balance), step=500.0
        )
        contracts_per_trade = c2.number_input(
            "Contracts / trade", value=int(session.contracts_per_trade), step=1, min_value=1
        )
        max_open_positions = c3.number_input(
            "Max open positions", value=int(session.max_open_positions), step=1, min_value=1
        )

        st.subheader("Daily caps")
        d1, d2 = st.columns(2)
        max_daily_loss_dollars = d1.number_input(
            "Max daily loss $", value=float(session.max_daily_loss_dollars or 0.0), step=50.0,
            help="0 = unset",
        )
        max_daily_loss_percent = d2.number_input(
            "Max daily loss %", value=float(session.max_daily_loss_percent or 0.0),
            step=0.01, format="%.3f",
        )

        st.subheader("Per-trade caps")
        p1, p2, p3, p4 = st.columns(4)
        max_planned_trade_loss_dollars = p1.number_input(
            "Planned $ cap", value=float(session.max_planned_trade_loss_dollars or 0.0), step=50.0,
        )
        max_planned_trade_loss_percent = p2.number_input(
            "Planned % cap", value=float(session.max_planned_trade_loss_percent or 0.0),
            step=0.01, format="%.3f",
        )
        max_theoretical_trade_loss_dollars = p3.number_input(
            "Theoretical $ cap", value=float(session.max_theoretical_trade_loss_dollars or 0.0),
            step=50.0,
        )
        max_theoretical_trade_loss_percent = p4.number_input(
            "Theoretical % cap", value=float(session.max_theoretical_trade_loss_percent or 0.0),
            step=0.01, format="%.3f",
        )

        st.subheader("Spread + stop")
        s1, s2, s3 = st.columns(3)
        default_spread_width = s1.number_input(
            "Spread width", value=int(session.default_spread_width), step=1, min_value=1
        )
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

        st.subheader("Filters + decision")
        f1, f2, f3, f4 = st.columns(4)
        min_credit = f1.number_input(
            "Min credit", value=float(session.min_credit), step=0.05, format="%.2f"
        )
        max_bid_ask_width = f2.number_input(
            "Max bid/ask width", value=float(session.max_bid_ask_width), step=0.05, format="%.2f"
        )
        min_distance_from_spot = f3.number_input(
            "Min distance from spot", value=float(session.min_distance_from_spot), step=1.0
        )
        no_trade_score_threshold = f4.number_input(
            "No-trade score threshold",
            value=float(session.no_trade_score_threshold), step=0.05, format="%.2f",
        )

        submitted = st.form_submit_button("Apply session changes")

    if submitted:
        # Parse profit_targets
        try:
            parsed_targets = [
                float(x.strip()) for x in profit_target_str.split(",") if x.strip()
            ]
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
                OUTPUT_ROOT,
                field=field, old_value=old, new_value=new,
                active_strategy=st.session_state["active_strategy"],
                active_risk_profile=st.session_state["active_profile"],
            )
        st.session_state["session_config"] = new_session
        st.toast(f"Applied {len(diff)} session change(s); logged to config_change_log.jsonl")
        st.rerun()


# Refresh session reference after potential edits
session = st.session_state["session_config"]


# ──────────────────────────────────────────────────────────────────────
# Market / structure panel
# ──────────────────────────────────────────────────────────────────────

st.header("Market / structure")

# Spot comes from the QuoteProvider; structure context comes from StructureProvider.
# Both are surfaced so the user can spot drift between them.
quote_spot     = chain.spot if chain else (spot_quote.last if spot_quote else None)
structure_spot = structure.spot

top = st.columns(6)
top[0].metric(
    "Spot (quote)", f"{quote_spot:,.2f}" if quote_spot is not None else "—",
    f"struct {structure_spot:,.2f}" if structure_spot is not None else "—",
)
top[1].metric("MaxVol",      f"{structure.exposures.maxvol or '—'}")
top[2].metric("Call wall",   f"{structure.exposures.call_wall or '—'}")
top[3].metric("Put wall",    f"{structure.exposures.put_wall or '—'}")
top[4].metric("DA-GEX",      f"{structure.exposures.da_gex_signed or '—'}")
top[5].metric("Gamma regime", structure.exposures.gamma_regime or "—")

levels = st.columns(5)
levels[0].metric("PUT_CEILING (2K)", f"{structure.exposures.put_ceiling_2k or '—'}")
levels[1].metric("PUT_CEILING (5K)", f"{structure.exposures.put_ceiling_5k or '—'}")
levels[2].metric("CALL_FLOOR (2K)",  f"{structure.exposures.call_floor_2k or '—'}")
levels[3].metric("CALL_FLOOR (5K)",  f"{structure.exposures.call_floor_5k or '—'}")
levels[4].metric("DDOI pin",         f"{structure.exposures.ddoi_pin or '—'}")

st.caption(
    f"Structure from `{structure.source}` @ {structure.quote_ts.isoformat()}  ·  "
    f"chain from `{chain.provider_name if chain else '—'}` "
    f"@ {chain.quote_ts.isoformat() if chain else '—'}  ·  "
    f"expiry {structure.expiry}  ·  DTE {structure.dte}"
)


# ──────────────────────────────────────────────────────────────────────
# Candidates + decision
# ──────────────────────────────────────────────────────────────────────

def _fmt_quote(q: dict) -> str:
    """'bid / mid / ask' formatting for the candidate table."""
    b = q.get("bid")
    m = q.get("mid")
    a = q.get("ask")
    if b is None or m is None or a is None:
        return "—"
    return f"{b:.2f} / {m:.2f} / {a:.2f}"


st.header("Ranked candidates")

if not STRATEGIES:
    st.warning("No strategies registered. Check `config/strategies.yaml`.")
elif chain is None:
    st.error("QuoteProvider returned no chain. Cannot build candidates.")
else:
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
            long_leg  = c.meta.get("long_leg")  or {}
            # Phase 4 — per-candidate quote-validation badge. None=unvalidated
            # (mock chain), True=both legs passed, False=at least one rejected.
            sp = short_leg.get("validation_passed")
            lp = long_leg.get("validation_passed")
            if sp is None and lp is None:
                quote_badge = "—"
            elif sp is True and lp is True:
                quote_badge = "✓ pass"
            else:
                quote_badge = "✗ fail"
            # Phase 4.1 — readiness fields for table + expander
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
                "side":        c.side,
                "short K":     c.short_strike,
                "long K":      c.long_strike,
                "short b/a/m": _fmt_quote(short_leg),
                "long b/a/m":  _fmt_quote(long_leg),
                "quote":       quote_badge,
                "credit ($)":  round(c.credit, 2),
                "width":       round(c.max_risk + c.credit, 2),
                "theoretical $": round(theoretical, 0),
                "planned $":   round(planned, 0),
                "R:R":         round(c.reward_risk, 2),
                "b/a quality": round(c.meta.get("bid_ask_quality", 0.0), 2),
                # Phase 4.2 — surface the bid_ask_quality MODE (relative |
                # absolute) so the operator can see which calibration produced
                # the score, alongside the existing 'bucket' column.
                "b/a mode":    c.meta.get("bid_ask_quality_mode") or "—",
                "breakeven":   round(c.breakeven, 2),
                "score":       round(c.score, 2),
                "gap":         (round(c.score_gap_to_threshold, 3)
                                if c.score_gap_to_threshold is not None else None),
                # Phase 4.1 — score-edge, quote bucket, risk-type
                "edge":        (round(c.score_edge, 4)
                                if isinstance(c.score_edge, (int, float)) else None),
                "bucket":      readiness["quote_quality_bucket"],
                # Phase 4.2 — bucket reason (same pct cutoffs as the score)
                "bucket_reason": readiness["quote_quality_reason"],
                "risk_type":   readiness["risk_rejection_type"] or "—",
                "rejection":   c.rejection_type or ("rejected" if c.rejected else None),
                "weak":        "; ".join(c.weak_components),
                "rejection_reasons": "; ".join(c.rejection_reasons),
            })

        # ── Phase 5 — run the daily selector over the candidate rows ──
        # SELECTION ONLY (no execution). Build selector-input rows from each
        # candidate's readiness + core fields, pick ≤1, and surface it.
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

        # Selector result — compact, selection-only.
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

        # Per-candidate score breakdown expanders (Phase 2.7 observability)
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
                top = st.columns(4)
                top[0].metric("Score", f"{c.score:.4f}")
                top[1].metric(
                    "Threshold",
                    f"{c.score_threshold:.2f}" if c.score_threshold is not None else "—",
                )
                top[2].metric(
                    "Gap",
                    f"{c.score_gap_to_threshold:+.4f}"
                    if c.score_gap_to_threshold is not None else "—",
                )
                top[3].metric("Rejection type", c.rejection_type or "—")

                # Phase 2.8 — anchor observability
                anchor_cols = st.columns(4)
                anchor_cols[0].metric("Anchor", c.meta.get("anchor_source") or "—")
                av = c.meta.get("anchor_volume")
                anchor_cols[1].metric(
                    "Anchor volume",
                    f"{av:,.0f}" if isinstance(av, (int, float)) else "—",
                )
                anchor_cols[2].metric(
                    "Volume source",
                    c.meta.get("anchor_volume_source") or "—",
                )
                anchor_cols[3].metric(
                    "structure_strength_source",
                    c.meta.get("structure_strength_source") or "—",
                )

                if c.weak_components:
                    st.markdown(
                        "**Weakest components:** "
                        + ", ".join(f"`{w}`" for w in c.weak_components)
                    )
                if c.rejection_reasons:
                    st.markdown(
                        "**Filter reasons:** "
                        + ", ".join(f"`{r}`" for r in c.rejection_reasons)
                    )
                # Phase 4 — broker-side quote-validation detail per leg
                short_meta = c.meta.get("short_leg") or {}
                long_meta  = c.meta.get("long_leg")  or {}
                if any(
                    k in short_meta or k in long_meta
                    for k in ("validation_passed", "validation_rejection_reason", "quote_time")
                ):
                    qcols = st.columns(2)
                    for col, label, leg in (
                        (qcols[0], "Short leg", short_meta),
                        (qcols[1], "Long leg",  long_meta),
                    ):
                        passed = leg.get("validation_passed")
                        reason = leg.get("validation_rejection_reason")
                        qtime  = leg.get("quote_time")
                        badge  = (
                            "—" if passed is None else
                            ("✓ pass" if passed else f"✗ {reason or 'fail'}")
                        )
                        col.metric(f"{label} quote", badge, qtime or "")

                # ── Phase 4.1 — Selector readiness ──
                rd = c.meta.get("_readiness") or {}
                if rd:
                    st.caption(
                        "Phase 4.1: `score_edge` (score − threshold), "
                        "`quote_quality_bucket` (good/acceptable/poor/wide/invalid), "
                        "`risk_rejection_type` (planned/theoretical cap), "
                        "`selector_blockers` (eligibility audit). "
                        "Phase 4.2: `bid_ask_quality` is now RELATIVE (pct-of-mid) "
                        "and shares the SAME cutoffs as the bucket; quote VALIDATION "
                        "(broker pass/fail per leg, above) is separate from the quote "
                        "QUALITY score. `quote_clock_skew_*` flags a negative quote "
                        "age clamped to 0. `strict_target_dte` (CLI scanner only)."
                    )
                    sc = st.columns(4)
                    sc[0].metric(
                        "Score edge",
                        f"{c.score_edge:+.4f}" if isinstance(c.score_edge, (int, float)) else "—",
                        "marginal" if c.marginal_score else (
                            "passed" if c.score_edge_passed else "below"
                        ),
                    )
                    sc[1].metric("Quote bucket",     rd.get("quote_quality_bucket") or "—")
                    sc[2].metric("Risk type",        rd.get("risk_rejection_type") or "—")
                    sc[3].metric(
                        "Eligible (base)",
                        "yes" if rd.get("selector_eligible_base") else "no",
                        rd.get("selector_readiness_note") or "",
                    )
                    blockers = rd.get("selector_blockers") or []
                    if blockers:
                        st.markdown(
                            "**Selector blockers:** "
                            + ", ".join(f"`{b}`" for b in blockers)
                        )
                    # ── Phase 4.2 — bid_ask_quality mode + clock-skew tiles ──
                    # mode/reason are stamped on c.meta by the strategy; the
                    # clock-skew fields are stamped by the scanner's
                    # _candidate_row and may be absent in this inline preview
                    # (strict_target_dte is intentionally NOT wired into the
                    # inline compute_readiness this phase).
                    p42 = st.columns(4)
                    p42[0].metric(
                        "b/a quality",
                        f"{c.meta.get('bid_ask_quality', 0.0):.2f}",
                        c.meta.get("bid_ask_quality_mode") or "—",
                    )
                    p42[1].metric(
                        "b/a reason", c.meta.get("bid_ask_quality_reason") or "—",
                    )
                    skew_det = c.meta.get("quote_clock_skew_detected")
                    p42[2].metric(
                        "Clock skew",
                        "yes" if skew_det else ("no" if skew_det is False else "—"),
                    )
                    skew_s = c.meta.get("quote_clock_skew_seconds")
                    p42[3].metric(
                        "Skew (s)",
                        f"{skew_s:.2f}" if isinstance(skew_s, (int, float)) else "—",
                    )
                    # Phase 4.2 — bucket reason (shares the score's pct cutoffs)
                    # + strict_target_dte status. strict is sourced from the
                    # readiness dict; in this inline preview it is the default
                    # (False / True) since strict is a CLI-scanner-only gate.
                    q_reason = rd.get("quote_quality_reason")
                    if q_reason:
                        st.caption(f"Quote-quality reason: `{q_reason}`")
                    strict_on = rd.get("strict_target_dte")
                    strict_ok = rd.get("strict_target_dte_passed")
                    st.caption(
                        f"strict_target_dte: `{strict_on}`  ·  "
                        f"passed: `{strict_ok}`  "
                        "_(CLI scanner gate; not enforced in this inline preview)_"
                    )
                st.json(c.score_breakdown, expanded=False)

    decision = strat.select(candidates, params)
    st.subheader("Decision")
    badge = {"TRADE_CALL_CREDIT": "success", "TRADE_PUT_CREDIT": "success", "NO_TRADE": "warning"}
    getattr(st, badge.get(decision.decision, "info"))(decision.decision)
    st.write(decision.explanation)


# ──────────────────────────────────────────────────────────────────────
# Manual trade entry
# ──────────────────────────────────────────────────────────────────────

st.header("Manual trade entry")
with st.form("manual_trade"):
    cols = st.columns(4)
    side = cols[0].selectbox("Side", ["CALL_CREDIT", "PUT_CREDIT"])
    short_strike = cols[1].number_input("Short strike", value=5815.0, step=5.0)
    long_strike  = cols[2].number_input("Long strike",  value=5820.0, step=5.0)
    credit       = cols[3].number_input("Credit ($)",   value=0.60, step=0.05, format="%.2f")

    cols2 = st.columns(4)
    contracts = cols2[0].number_input(
        "Contracts", value=int(session.contracts_per_trade), step=1, min_value=1
    )
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
    submit_trade = st.form_submit_button("Record trade")

if submit_trade:
    ts = now_et()
    # Entry spot prefers the QuoteProvider's current spot; falls back to
    # structure spot if the quote provider is unavailable.
    entry_spot = quote_spot if quote_spot is not None else structure.spot
    record = build_manual_trade_record(
        ts=ts,
        strategy_id=st.session_state["active_strategy"] or "manual",
        side=side, symbol=SYMBOL, expiry=structure.expiry or "",
        short_strike=short_strike, long_strike=long_strike,
        credit=credit, contracts=int(contracts),
        entry_spot=entry_spot, stop_variant=stop, profit_target=profit_target,
        notes=notes,
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


# ──────────────────────────────────────────────────────────────────────
# Open positions + P&L
# ──────────────────────────────────────────────────────────────────────

st.header("Open tracked positions")

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
pnl_cols[0].metric("Realized P&L",   f"${paper_account.realized_pnl:,.2f}")
pnl_cols[1].metric("Unrealized P&L", f"${paper_account.unrealized_pnl:,.2f}")
pnl_cols[2].metric("Equity",         f"${paper_account.equity:,.2f}")

if paper_account.equity_curve:
    eq_rows = [{"ts": ts.isoformat(), "equity": eq} for ts, eq in paper_account.equity_curve]
    st.line_chart(eq_rows, x="ts", y="equity")


# ──────────────────────────────────────────────────────────────────────
# Forward runs (Phase 7) — READ-ONLY monitoring view. No start/stop here.
# ──────────────────────────────────────────────────────────────────────

st.header("Forward runs (monitoring)")
st.caption(
    "Read-only review of the local forward runner "
    "(`python -m scripts.run_forward --profile <id>`). Monitoring + local ledger "
    "only — **no execution, no broker orders.** This panel never launches or "
    "stops a run; it only inspects ledgers + shows the commands to copy."
)

# Phase 9A — control status (read-only). The UI NEVER starts/stops a process;
# it shows status + copy-only control commands.
_ctl = forward_control.status()
_ccols = st.columns(3)
_ccols[0].metric("Runner", _ctl.get("status", "stopped"))
_ccols[1].metric("Active", str(_ctl.get("active", False)))
_ccols[2].metric("PID", str(_ctl.get("pid") or "—"))
if _ctl.get("status") == "stale":
    st.warning("Control state is **stale** (PID not alive). "
               "Run `python -m scripts.control_forward cleanup-stale` to clear it.")
st.markdown("**Control commands (copy into a terminal — the UI never launches/stops a process):**")
_ctl_profile = (
    (forward_review.load_latest_manifest() or {}).get("profile_id")
    or "vertical_wing_score_best_1dte"
)
st.code(
    "python -m scripts.control_forward status\n"
    f"python -m scripts.control_forward command --profile {_ctl_profile} --interval-seconds 60 --market-hours-only\n"
    f"python -m scripts.control_forward start --profile {_ctl_profile} --interval-seconds 60 --market-hours-only\n"
    "python -m scripts.control_forward stop\n"
    "python -m scripts.control_forward cleanup-stale",
    language="powershell",
)

# Discover runs (newest first) via the Phase 8 review module — read-only.
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
    st.info("No forward runs yet. Run "
            "`python -m scripts.run_forward --profile vertical_wing_score_best_1dte --once` "
            "to create one.")
else:
    _run_ids = [p.name for p in _fwd_runs]
    _chosen_run = st.selectbox("Run", options=_run_ids, index=0,
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

# Safe commands to copy (display only — NO process launch from the UI).
_prof_for_cmd = (_summary.get("profile_id") if _fwd_runs and _summary else None) \
    or "vertical_wing_score_best_1dte"
st.markdown("**Start a local forward session (copy into a terminal — the UI never launches it):**")
st.code(
    f"python -m scripts.run_forward --profile {_prof_for_cmd} --interval-seconds 60 --market-hours-only\n"
    f"python -m scripts.run_forward --profile {_prof_for_cmd} --once\n"
    f"python -m scripts.run_forward --profile {_prof_for_cmd} --max-ticks 5 --interval-seconds 60\n"
    f"python -m scripts.review_forward --latest",
    language="powershell",
)


# ──────────────────────────────────────────────────────────────────────
# Portfolio forward (multi-strategy local paper lifecycle) — Phase 9B
# READ-ONLY. The UI never launches/stops a run, never places/previews orders.
# ──────────────────────────────────────────────────────────────────────

st.header("Portfolio forward (paper lifecycle)")
st.caption(
    "LOCAL PAPER ACCOUNTING ONLY — simulated credit-spread lifecycle with TP / SL "
    "/ EOD exits across multiple profiles. No broker orders, no order preview, no "
    "live execution. This panel is read-only; it never launches a run."
)

_pf_man = portfolio_ledger.load_manifest("latest")
if not _pf_man:
    st.caption("No portfolio runs yet. Run a local paper portfolio from a terminal (commands below).")
else:
    _pf_summ = portfolio_ledger.load_summary("latest") or {}
    _pf_hb = portfolio_ledger.load_heartbeat("latest") or {}
    st.caption(
        f"latest: {_pf_man.get('portfolio_run_id')}  ·  status={_pf_man.get('status')}  ·  "
        f"profiles={', '.join(_pf_man.get('profiles') or [])}"
    )
    _pc = st.columns(5)
    _pc[0].metric("Open", _pf_summ.get("open_trade_count", 0))
    _pc[1].metric("Closed", _pf_summ.get("closed_trade_count", 0))
    _pc[2].metric("Realized P&L", _pf_summ.get("realized_pnl", 0.0))
    _pc[3].metric("Unrealized P&L", _pf_summ.get("unrealized_pnl", 0.0))
    _pc[4].metric("Total P&L", _pf_summ.get("total_pnl", 0.0))
    _pc2 = st.columns(4)
    _pc2[0].metric("Wins", _pf_summ.get("wins", 0))
    _pc2[1].metric("Losses", _pf_summ.get("losses", 0))
    _pc2[2].metric("Dup skipped", _pf_summ.get("duplicate_skipped_count", 0))
    _pc2[3].metric("Blocked", _pf_summ.get("blocked_by_limits_count", 0))

    _pf_cols = ("paper_trade_id", "profile_id", "side", "short_strike", "long_strike",
                "entry_credit", "current_mark", "unrealized_pnl", "realized_pnl",
                "exit_reason", "ticks_held")
    _open_rows = portfolio_ledger.load_open_trades("latest")
    if _open_rows:
        st.markdown("**Open paper trades**")
        st.dataframe([{k: r.get(k) for k in _pf_cols} for r in _open_rows],
                     use_container_width=True, hide_index=True)
    _closed_rows = portfolio_ledger.load_closed_trades("latest")
    if _closed_rows:
        st.markdown("**Closed paper trades**")
        st.dataframe([{k: r.get(k) for k in _pf_cols} for r in _closed_rows],
                     use_container_width=True, hide_index=True)
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
st.markdown("**Run a local paper portfolio (copy into a terminal — the UI never launches it):**")
st.code(
    f"python -m scripts.run_portfolio_forward --profiles {_pf_profiles} --interval-seconds 60 --market-hours-only\n"
    f"python -m scripts.run_portfolio_forward --profiles {_pf_profiles} --once\n"
    "python -m scripts.review_portfolio_forward --latest\n"
    "python -m scripts.review_portfolio_forward --open latest\n"
    "python -m scripts.review_portfolio_forward --closed latest\n"
    "python -m scripts.review_portfolio_forward --reconcile latest",
    language="powershell",
)


# ──────────────────────────────────────────────────────────────────────
# EOD
# ──────────────────────────────────────────────────────────────────────

st.header("EOD summary")
if st.button("Generate EOD summary now"):
    out = generate_eod_summary(REPO_ROOT)
    st.success(f"Wrote {out}")

eod_md = OUTPUT_ROOT / "latest" / "eod_summary.md"
if eod_md.exists():
    st.markdown(eod_md.read_text(encoding="utf-8"))
else:
    st.caption("Run the scanner + Generate EOD summary above to populate.")


# ──────────────────────────────────────────────────────────────────────
# Session config (debug view)
# ──────────────────────────────────────────────────────────────────────

with st.expander("Session config (current overrides)", expanded=False):
    diff = session.diff_against(baseline)
    if diff:
        st.write("Fields edited this session:")
        st.json({k: {"baseline": v[0], "now": v[1]} for k, v in diff.items()})
    else:
        st.caption("No overrides — running with profile defaults.")
    st.subheader("Full session config")
    st.json(session.to_dict())
