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
from src.paper.account import PaperAccount  # noqa: E402
from src.paper.manual_tracker import (  # noqa: E402
    append_equity_point,
    build_manual_trade_record,
    record_manual_trade,
    snapshot_positions,
    unrealized_pnl_dollars,
)
from src.paper.positions import PaperPosition  # noqa: E402
from src.providers.quotes.mock_provider import MockQuoteProvider  # noqa: E402
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
    st.caption(f"Structure provider: `{CFG.providers.structure_active}`")
    st.caption(f"Quote provider:     `{CFG.providers.quotes_active}`")
    st.caption(f"Execution mode:     `{CFG.providers.execution_active}`")


# Acquire snapshot + quote (deterministic stub + mock)
structure_provider = StubStructureProvider()
quote_provider = MockQuoteProvider()
SYMBOL = CFG.scanner.get("symbols", ["SPX"])[0]
snap = structure_provider.get_snapshot(SYMBOL)
spot_quote = quote_provider.get_spot(SYMBOL)


# ──────────────────────────────────────────────────────────────────────
# Header + provider status
# ──────────────────────────────────────────────────────────────────────

st.title("ZerσSigma Algo Cockpit")
st.caption("Scanner · decision log · manual + paper trade tracking · EOD summary")

session: SessionConfig = st.session_state["session_config"]
baseline: SessionConfig = st.session_state["session_baseline"]
paper_account: PaperAccount = st.session_state["paper_account"]

with st.expander("Provider status", expanded=False):
    cols = st.columns(3)
    cols[0].metric("StructureProvider", structure_provider.name, snap.quote_ts.strftime("%H:%M:%S"))
    cols[1].metric(
        "QuoteProvider",
        quote_provider.name,
        spot_quote.ts.strftime("%H:%M:%S") if spot_quote else "—",
    )
    cols[2].metric("ExecutionProvider", CFG.providers.execution_active, "no live execution")


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
top = st.columns(6)
top[0].metric("Spot",        f"{snap.spot:,.2f}")
top[1].metric("MaxVol",      f"{snap.exposures.maxvol or '—'}")
top[2].metric("Call wall",   f"{snap.exposures.call_wall or '—'}")
top[3].metric("Put wall",    f"{snap.exposures.put_wall or '—'}")
top[4].metric("DA-GEX",      f"{snap.exposures.da_gex_signed or '—'}")
top[5].metric("Gamma regime", snap.exposures.gamma_regime or "—")

levels = st.columns(5)
levels[0].metric("PUT_CEILING (2K)", f"{snap.exposures.put_ceiling_2k or '—'}")
levels[1].metric("PUT_CEILING (5K)", f"{snap.exposures.put_ceiling_5k or '—'}")
levels[2].metric("CALL_FLOOR (2K)",  f"{snap.exposures.call_floor_2k or '—'}")
levels[3].metric("CALL_FLOOR (5K)",  f"{snap.exposures.call_floor_5k or '—'}")
levels[4].metric("DDOI pin",         f"{snap.exposures.ddoi_pin or '—'}")

st.caption(
    f"Snapshot from `{snap.source}` @ {snap.quote_ts.isoformat()}  ·  "
    f"expiry {snap.expiry}  ·  DTE {snap.dte}"
)


# ──────────────────────────────────────────────────────────────────────
# Candidates + decision
# ──────────────────────────────────────────────────────────────────────

st.header("Ranked candidates")

if not STRATEGIES:
    st.warning("No strategies registered. Check `config/strategies.yaml`.")
else:
    strat = STRATEGIES[st.session_state["active_strategy"]]
    params = {**(strat.default_parameters or {}), **session.to_filter_params()}
    candidates = strat.generate_candidates(snap, params)
    apply_filters(candidates, session.to_filter_params())
    for c in candidates:
        strat.score(c, snap, params)
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
            rows.append({
                "side": c.side,
                "short": c.short_strike,
                "long":  c.long_strike,
                "credit ($)": round(c.credit, 2),
                "width": round(c.max_risk + c.credit, 2),
                "theoretical $": round(theoretical, 0),
                "planned $":     round(planned, 0),
                "R:R":   round(c.reward_risk, 2),
                "breakeven": round(c.breakeven, 2),
                "score": round(c.score, 2),
                "rejected": c.rejected,
                "rejection_reasons": "; ".join(c.rejection_reasons),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

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
    record = build_manual_trade_record(
        ts=ts,
        strategy_id=st.session_state["active_strategy"] or "manual",
        side=side, symbol=SYMBOL, expiry=snap.expiry or "",
        short_strike=short_strike, long_strike=long_strike,
        credit=credit, contracts=int(contracts),
        entry_spot=snap.spot, stop_variant=stop, profit_target=profit_target,
        notes=notes,
    )
    record_manual_trade(OUTPUT_ROOT, row=record)

    pos = PaperPosition(
        position_id=uuid.uuid4().hex[:8],
        strategy_id=record["strategy_id"], side=side, symbol=SYMBOL,
        expiry=snap.expiry or "",
        short_strike=short_strike, long_strike=long_strike,
        credit=credit, contracts=int(contracts),
        entry_time=ts, entry_spot=snap.spot, stop_variant=stop,
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
