"""ZerσSigma Algo Cockpit — Streamlit shell.

Phase 1: every panel is a placeholder that wires through real loaders so the
moment a provider goes live the data shows up. No hardcoded mock data leaks
into production paths; the stub StructureProvider is explicitly named.

Session-control roadmap (planned, NOT yet wired):
    At session/algo start the cockpit should let the user override the loaded
    risk-profile template before the scanner begins. Editable fields will
    include — at minimum — starting_balance, contracts_per_trade,
    max_daily_loss_{dollars,percent}, max_planned_trade_loss_{dollars,percent},
    max_theoretical_trade_loss_{dollars,percent}, spread_width, stop_variant,
    profit_targets, max_open_positions, no_trade_score_threshold,
    scan_start_time, scan_end_time, preferred_entry_windows, minimum_credit,
    max_bid_ask_width, min_distance_from_spot. Every edit will append a
    record to outputs/runs/{date}/config_change_log.jsonl.

    For now this file shows the loaded profile as read-only JSON and
    surfaces planned vs theoretical risk per candidate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.providers.structure.stub import StubStructureProvider  # noqa: E402
from src.risk.limits import (  # noqa: E402
    load_profile,
    planned_loss_dollars,
    theoretical_max_loss_dollars,
)
from src.strategies.registry import load_strategies  # noqa: E402
from src.utils.config import load_config  # noqa: E402

# ----------------------------------------------------------------------
# Page / sidebar
# ----------------------------------------------------------------------

st.set_page_config(
    page_title="ZerσSigma Algo Cockpit",
    page_icon="📊",
    layout="wide",
)

cfg = load_config(REPO_ROOT)
strategies = load_strategies(cfg)

profile_names = list(cfg.risk_profiles.keys()) or ["(no profiles)"]
default_profile_idx = (
    profile_names.index(cfg.active_risk_profile)
    if cfg.active_risk_profile in profile_names
    else 0
)

with st.sidebar:
    st.title("ZerσSigma Algo")
    st.caption("Phase 1 scaffold — no live execution")

    strategy_id = st.selectbox(
        "Strategy",
        options=list(strategies.keys()) or ["(none registered)"],
        index=0,
    )
    profile_name = st.selectbox(
        "Risk profile (session default)",
        options=profile_names,
        index=default_profile_idx,
        help="Templates load at session start. Per-session overrides land in a "
             "later phase — every edit will be logged to config_change_log.jsonl.",
    )
    st.divider()
    st.caption(f"Structure provider: `{cfg.providers.structure_active}`")
    st.caption(f"Quote provider:     `{cfg.providers.quotes_active}`")
    st.caption(f"Execution mode:     `{cfg.providers.execution_active}`")


# ----------------------------------------------------------------------
# Top panels
# ----------------------------------------------------------------------

st.title("ZerσSigma Algo Cockpit")
st.caption("Scanner · decision log · manual + paper trade tracking · EOD summary")

provider = StubStructureProvider()
snap = provider.get_snapshot(cfg.scanner.get("symbols", ["SPX"])[0])

top_cols = st.columns(5)
top_cols[0].metric("Spot", f"{snap.spot:,.2f}")
top_cols[1].metric("MaxVol",   f"{snap.exposures.maxvol or '—'}")
top_cols[2].metric("Call wall", f"{snap.exposures.call_wall or '—'}")
top_cols[3].metric("Put wall",  f"{snap.exposures.put_wall or '—'}")
top_cols[4].metric("Gamma",    snap.exposures.gamma_regime or "—")

st.caption(
    f"Snapshot from `{snap.source}` @ {snap.quote_ts.isoformat()}  ·  "
    f"expiry {snap.expiry}  ·  DTE {snap.dte}"
)


# ----------------------------------------------------------------------
# Candidate panel — show planned + theoretical side by side
# ----------------------------------------------------------------------

prof = load_profile(cfg.risk_profiles, profile_name)

st.header("Ranked candidates")
if prof.paper_only:
    st.warning(f"Risk profile **{prof.label}** is marked `paper_only`.")

if not strategies:
    st.warning("No strategies registered. Check `config/strategies.yaml`.")
else:
    strat = strategies[strategy_id]
    params = strat.default_parameters if hasattr(strat, "default_parameters") else {}
    candidates = strat.generate_candidates(snap, params)
    for c in candidates:
        strat.score(c, snap, params)
    candidates.sort(key=lambda c: -c.score)

    if not candidates:
        st.info("No candidates produced for the current snapshot.")
    else:
        for c in candidates:
            with st.expander(f"{c.side}  K {c.short_strike}/{c.long_strike}   score {c.score:.2f}"):
                top = st.columns(4)
                top[0].metric("Credit",     f"${c.credit:.2f}")
                top[1].metric("Max risk",   f"${c.max_risk:.2f}")
                top[2].metric("R:R",        f"{c.reward_risk:.2f}")
                top[3].metric("Δ from spot", f"{c.distance_from_spot:+.1f}")

                # Risk dollars under the session profile
                planned_d = planned_loss_dollars(
                    c.credit, c.max_risk, prof.default_stop_variant, prof.contracts_per_trade,
                )
                theoretical_d = theoretical_max_loss_dollars(c.max_risk, prof.contracts_per_trade)
                risk = st.columns(3)
                risk[0].metric(
                    f"Planned stop risk ({prof.default_stop_variant})",
                    f"${planned_d:,.0f}",
                )
                risk[1].metric("Theoretical max loss", f"${theoretical_d:,.0f}")
                risk[2].metric(
                    "Contracts × width",
                    f"{prof.contracts_per_trade} × ${prof.default_spread_width}",
                )

                st.json(c.score_breakdown)

    decision = strat.select(candidates, params)
    st.subheader("Decision")
    st.success(decision.decision)
    st.write(decision.explanation)


# ----------------------------------------------------------------------
# Manual trade entry (placeholder form)
# ----------------------------------------------------------------------

st.header("Manual trade entry")
with st.form("manual_trade"):
    cols = st.columns(4)
    side = cols[0].selectbox("Side", ["CALL_CREDIT", "PUT_CREDIT"])
    short = cols[1].number_input("Short strike", value=5810.0, step=5.0)
    long_strike = cols[2].number_input("Long strike", value=5815.0, step=5.0)
    credit = cols[3].number_input("Credit ($)",   value=1.20, step=0.05, format="%.2f")
    cols2 = st.columns(3)
    contracts = cols2[0].number_input(
        "Contracts", value=prof.contracts_per_trade, step=1, min_value=1
    )
    stop = cols2[1].selectbox(
        "Stop variant",
        ["BASELINE_CASH_SETTLE", "SL_100_PERCENT_LOSS", "SL_150_PERCENT_LOSS", "SL_200_PERCENT_LOSS"],
        index=["BASELINE_CASH_SETTLE", "SL_100_PERCENT_LOSS", "SL_150_PERCENT_LOSS",
               "SL_200_PERCENT_LOSS"].index(prof.default_stop_variant),
    )
    notes = cols2[2].text_input("Notes", value="")
    submitted = st.form_submit_button("Record trade")
    if submitted:
        st.toast("Phase 1: persistence wiring lands once paper account is initialized in session state.")


# ----------------------------------------------------------------------
# Risk profile + EOD placeholders
# ----------------------------------------------------------------------

st.header(f"Risk profile (session default: {prof.label})")
st.caption(
    "Loaded from `config/risk_profiles.yaml`. Per-session editing arrives in a "
    "later phase; see the docstring at the top of this file for the planned controls."
)
st.json(prof.raw)

st.header("EOD summary")
st.caption("Run `python scripts/run_eod_summary.py` to generate the day's summary; this panel will surface it.")
