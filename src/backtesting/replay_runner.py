"""Phase 10B — historical replay runner.

Runs ZerσSigma Algo run-profiles across local raw snapshot dates and produces
trade-level + candidate-level records, REUSING the live path end to end:

    saved raw file  -> Phase 10A mappers (StructureSnapshot + OptionChainSnapshot)
                    -> VerticalWingV1.generate_candidates  (no strategy fork)
                    -> apply_filters  (live risk filters)
                    -> VerticalWingV1.score
                    -> compute_readiness  (live selector-readiness)
                    -> select_daily_trade  (live Phase 5 selector)
                    -> lifecycle_sim.simulate_exit  (historical TP/SL/EOD)

NO broker, NO order preview, NO Tastytrade, NO ZerσSigma live API. The selector
+ strategy are the SAME modules the live scanner uses; only the data SOURCE and
the exit SIMULATION are new (both read-only over saved snapshots).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import src.app.cockpit_helpers as ch
from src.app.session_state import SessionConfig
from src.backtesting import mappers as M
from src.backtesting import raw_snapshot_loader as L
from src.backtesting import schemas
from src.backtesting.lifecycle_sim import OPTION_MULTIPLIER, build_day_index, simulate_exit
from src.backtesting.profile_runtime import (
    derive_run_settings,
    selector_config_from_profile,
    threshold_scheme,
)
from src.config.strategy_profiles import load_profile_file
from src.risk.filters import apply_filters
from src.risk.limits import load_profile
from src.selector.daily_selector import components_to_str, select_daily_trade
from src.selector.readiness import compute_readiness
from src.strategies.registry import load_strategies
from src.utils.config import load_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STRATEGY_ID = "vertical_wing_v1"
_MIN_SCORE_EDGE = 0.02

# Profile cohorts (per the Phase 10B spec).
PRIMARY_PROFILES: tuple[str, ...] = (
    "morning_5k_dynamic_tp75",
    "morning_2k_dynamic_no_tp",
    "eod_5k_dynamic_sl150_no_tp",
    "eod_5k_dynamic_sl200_no_tp",
)
CONTROL_PROFILES: tuple[str, ...] = (
    "morning_5k_call_tp75_control",
    "morning_2k_call_no_tp_control",
    "eod_5k_call_sl150_no_tp_control",
    "eod_5k_call_tp50_control",
    "regime_put_credit_test",
    "observe_dynamic_5k",
)


def resolve_profiles(profile_arg: str, *, include_controls: bool = False) -> list[str]:
    """Resolve a --profile value into a concrete list of profile ids."""
    p = (profile_arg or "").strip()
    if p in ("all-main", "all_main", "main"):
        out = list(PRIMARY_PROFILES)
        if include_controls:
            out += list(CONTROL_PROFILES)
        return out
    if p in ("all", "everything"):
        return list(PRIMARY_PROFILES) + list(CONTROL_PROFILES)
    return [p]


@dataclass
class BacktestResult:
    run_config: dict[str, Any]
    candidates: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    no_trade_reasons: list[dict[str, Any]] = field(default_factory=list)
    dates_evaluated: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)


# ── setup (cfg + strategy + per-risk-profile session, cached) ────────────────

def _load_cfg_and_strategy():  # type: ignore[no-untyped-def]
    cfg = load_config(_REPO_ROOT)
    strategies = load_strategies(cfg)
    strat = strategies.get(_STRATEGY_ID)
    if strat is None:    # pragma: no cover - registry/yaml misconfig
        raise RuntimeError(f"strategy {_STRATEGY_ID!r} not enabled in config/strategies.yaml")
    return cfg, strat


def _session_for(cfg, profile, cache: dict[str, SessionConfig]) -> SessionConfig:  # type: ignore[no-untyped-def]
    name = profile.risk_profile or cfg.active_risk_profile
    if name not in cache:
        cache[name] = SessionConfig.from_profile(load_profile(cfg.risk_profiles, name))
    return cache[name]


# ── selector row adapter (reuses compute_readiness; no scoring duplication) ──

def _selector_row(c, readiness: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Minimal row the live ``select_daily_trade`` consumes (Phase 5 schema)."""
    return {
        "side": c.side,
        "score": c.score,
        "credit": c.credit,
        "distance_from_spot": c.distance_from_spot,
        "rejected": c.rejected,
        # structure / premium inputs for balanced_structure_premium_valid
        "anchor_volume": c.meta.get("anchor_volume"),
        "structure_strength": c.score_breakdown.get("structure_strength"),
        "maxvol_alignment": c.score_breakdown.get("maxvol_alignment"),
        "score_maxvol_alignment": c.score_breakdown.get("maxvol_alignment"),
        "bid_ask_quality": c.meta.get("bid_ask_quality"),
        "quote_quality_bucket": readiness["quote_quality_bucket"],
        "quote_validation_passed": None,   # historical mid-to-mid, not broker-validated
        "planned_stop_risk_pct": readiness["planned_stop_risk_pct"],
        # readiness pass/fail buckets
        "candidate_passes_trade_filters": readiness["candidate_passes_trade_filters"],
        "candidate_passes_risk_filters": readiness["candidate_passes_risk_filters"],
        "candidate_passes_quote_filters": readiness["candidate_passes_quote_filters"],
        "candidate_passes_score_threshold": readiness["candidate_passes_score_threshold"],
        "candidate_passes_score_edge": readiness["candidate_passes_score_edge"],
        "candidate_is_marginal": readiness["candidate_is_marginal"],
        "selector_eligible_base": readiness["selector_eligible_base"],
    }


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y")
    return False


def _join_unique(values: list[Any]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if isinstance(value, list):
            items = value
        else:
            items = str(value or "").split(";")
        for item in items:
            s = str(item or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return "; ".join(out)


def _top_value(rows: list[dict[str, Any]], key: str) -> str:
    vals = [str(r.get(key) or "").strip() for r in rows if str(r.get(key) or "").strip()]
    if not vals:
        return ""
    return Counter(vals).most_common(1)[0][0]


def _top_blocker(rows: list[dict[str, Any]]) -> str:
    vals: list[str] = []
    for row in rows:
        for key in (
            "selector_blockers", "risk_rejection_type", "quote_quality_reason",
            "rejection_type", "skipped_reason", "selector_reason",
        ):
            raw = row.get(key)
            if isinstance(raw, list):
                vals.extend(str(v).strip() for v in raw if str(v).strip())
            else:
                vals.extend(
                    str(v).strip() for v in str(raw or "").split(";") if str(v).strip()
                )
    return Counter(vals).most_common(1)[0][0] if vals else ""


def _no_trade_record(
    *,
    date: str,
    symbol: str,
    dte_label: str,
    profile_id: str,
    settings: Any | None = None,
    status: str,
    reason: str,
    detail: str = "",
    base: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    base = base or {}
    candidates = candidates or []
    risk_filtered = [c for c in candidates if c.get("candidate_passes_risk_filters") is False]
    quote_filtered = [c for c in candidates if c.get("candidate_passes_quote_filters") is False]
    score_filtered = [
        c for c in candidates
        if c.get("candidate_passes_score_threshold") is False
        or c.get("candidate_passes_score_edge") is False
    ]
    selector_filtered = [
        c for c in candidates
        if not _as_bool(c.get("selected_trade"))
        and (c.get("selector_blockers") or c.get("selector_reason") or c.get("skipped_reason"))
    ]
    eligible = [
        c for c in candidates
        if _as_bool(c.get("selector_eligible_base")) and not c.get("selector_blockers")
    ]
    side_allowed = [
        c.get("side_allowed_by_config") for c in candidates
        if c.get("side_allowed_by_config") is not None
    ]
    return {
        "date": date,
        "symbol": symbol,
        "dte": dte_label,
        "profile_id": profile_id,
        "entry_target": getattr(settings, "entry_target", "") if settings is not None else "",
        "entry_timestamp": base.get("entry_timestamp"),
        "entry_offset_minutes": base.get("entry_offset_minutes"),
        "status": status,
        "reason": reason,
        "detail": detail,
        "first_blocker": _top_blocker(candidates) or reason,
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "selected_count": sum(1 for c in candidates if _as_bool(c.get("selected_trade"))),
        "risk_filtered_count": len(risk_filtered),
        "quote_filtered_count": len(quote_filtered),
        "score_filtered_count": len(score_filtered),
        "selector_filtered_count": len(selector_filtered),
        "corridor_valid": base.get("corridor_valid"),
        "active_wds": base.get("active_wds"),
        "raw_wds": base.get("raw_wds"),
        "wds_tier": base.get("wds_tier"),
        "top_selector_reason": _top_value(selector_filtered, "selector_reason"),
        "top_risk_reason": _top_value(risk_filtered, "risk_rejection_reason")
        or _top_value(risk_filtered, "risk_rejection_type"),
        "top_quote_reason": _top_value(quote_filtered, "quote_quality_reason"),
        "side_allowed_by_config": (
            all(_as_bool(v) for v in side_allowed) if side_allowed else None
        ),
        "missing_price_count": sum(
            int(float(c.get("missing_price_count") or 0)) for c in candidates
        ),
    }


# ── one (profile, date) evaluation ───────────────────────────────────────────

def _evaluate(
    *, strat, session, profile, settings, selector_cfg, symbol, dte_label,
    date, rows, day_index, scheme, warning, contracts,
) -> dict[str, Any]:
    """Map → generate → filter → score → readiness → select → simulate.

    Returns {candidates: [...], trades: [...], no_trade: {...}|None, status, skip_reason}.
    """
    timestamps = L.available_timestamps(rows)
    sel = M.select_snapshot(timestamps, settings.entry_target)
    if not sel["ok"]:
        return {"candidates": [], "trades": [], "status": "skipped",
                "skip_reason": sel["reason"],
                "no_trade": _no_trade_record(
                    date=date, symbol=symbol, dte_label=dte_label,
                    profile_id=profile.profile_id, settings=settings,
                    status="skipped", reason=f"no_entry_snapshot:{sel['reason']}",
                    detail=sel["reason"],
                )}

    ts = sel["timestamp"]
    structure = M.map_structure(rows, ts, symbol)
    chain = M.map_option_chain(rows, ts, symbol)

    params = {
        **(strat.default_parameters or {}),
        **session.to_filter_params(),
        "volume_threshold": settings.volume_threshold,
        "spread_width": settings.spread_width,
        "no_trade_score_threshold": settings.no_trade_score_threshold,
    }
    filter_params = {**session.to_filter_params(), "spread_width": settings.spread_width}

    candidates = strat.generate_candidates(structure, chain, params)
    apply_filters(candidates, filter_params)
    for c in candidates:
        strat.score(c, structure, chain, params)
    strat.select(candidates, params)   # stamps score_threshold/edge/rejection_type

    rows_for_selector: list[dict[str, Any]] = []
    readinesses: list[dict[str, Any]] = []
    for c in candidates:
        rd = compute_readiness(
            c, session=session, threshold=(c.score_threshold or settings.no_trade_score_threshold),
            min_score_edge=_MIN_SCORE_EDGE, target_dte=settings.target_dte, today_et=ts.date(),
        )
        readinesses.append(rd)
        rows_for_selector.append(_selector_row(c, rd))

    sel_result = select_daily_trade(
        rows_for_selector, selector_cfg,
        gamma_regime=structure.exposures.gamma_regime,
    )

    wd = M.corridor_wds(structure)
    gamma = ch.primary_secondary_gamma(structure.exposures, structure.spot)
    base = {
        "symbol": symbol, "date": date, "dte": dte_label, "profile_id": profile.profile_id,
        "preset_kind": settings.preset_kind, "entry_target": settings.entry_target,
        "entry_timestamp": ts.isoformat(), "entry_offset_minutes": sel["offset_minutes"],
        "spot": structure.spot, "threshold": settings.threshold_label,
        "volume_threshold": settings.volume_threshold, "threshold_scheme": scheme,
        "threshold_warning": warning or "", "selector_mode": settings.selector_mode,
        "corridor_valid": wd.get("corridor_valid"), "cw1": wd.get("corridor_cw1"),
        "pw1": wd.get("corridor_pw1"), "corridor_reason": wd.get("corridor_reason"),
        "active_wds": wd.get("dominant_wing_wds"), "raw_wds": wd.get("raw_dominant_wds"),
        "wds_tier": (wd.get("dominant_wing_tier") if wd.get("wds_active")
                     else wd.get("raw_dominant_tier")),
        "dominant_wing_side": (wd.get("dominant_wing_side") if wd.get("wds_active")
                               else wd.get("raw_dominant_side")),
        "primary_gamma": gamma.get("primary"), "secondary_gamma": gamma.get("secondary"),
    }

    cand_records: list[dict[str, Any]] = []
    trade_records: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        meta = sel_result.per_row[i]
        selected = bool(meta["selected_trade"])
        rec = _candidate_record(base, c, settings, meta, readinesses[i], selected)
        cand_records.append(rec)
        if selected:
            trade_records.append(_trade_record(rec, c, settings, day_index, ts, contracts))

    no_trade = None
    if not sel_result.selected_indices:
        reason = sel_result.selector_no_trade_reason or "no_selection"
        no_trade = _no_trade_record(
            date=date, symbol=symbol, dte_label=dte_label, profile_id=profile.profile_id,
            settings=settings, status="ok", reason=reason, detail=sel_result.selector_explanation or "",
            base=base, candidates=cand_records,
        )
    return {"candidates": cand_records, "trades": trade_records,
            "no_trade": no_trade, "status": "ok", "skip_reason": ""}


def _candidate_record(base, c, settings, sel_meta, readiness, selected) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    spot = base["spot"] or 0.0
    width = round(c.max_risk + c.credit, 4)
    dist = abs(c.distance_from_spot)
    skipped_reason = ""
    if not selected:
        # why this candidate was not the selected trade
        blockers = sel_meta.get("selector_blockers") or []
        skipped_reason = sel_meta.get("selector_reason") or (
            "ineligible:" + ",".join(blockers) if blockers else "not_selected"
        )
    return {
        **base,
        "side": c.side,
        "anchor_source": c.meta.get("anchor_source"),
        "wing_strike": c.short_strike,
        "short_strike": c.short_strike,
        "long_strike": c.long_strike,
        "width_points": width,
        "entry_credit_points": round(c.credit, 4),
        "entry_credit_dollars": round(c.credit * OPTION_MULTIPLIER, 2),
        "max_risk_points": round(c.max_risk, 4),
        "max_risk_dollars": round(c.max_risk * OPTION_MULTIPLIER, 2),
        "distance_from_spot_to_short": round(dist, 4),
        "distance_pct_from_spot_to_short": round(dist / spot * 100.0, 4) if spot else None,
        "reward_risk": round(c.reward_risk, 4),
        "score": round(c.score, 4),
        "score_threshold": c.score_threshold,
        "rejected": c.rejected,
        "rejection_reasons": "; ".join(c.rejection_reasons),
        "quote_quality_bucket": c.meta.get("quote_quality_bucket"),
        "selector_score": sel_meta.get("selector_score"),
        "selector_score_components": components_to_str(sel_meta.get("selector_score_components")),
        "selector_reason": sel_meta.get("selector_reason"),
        "rejection_type": c.rejection_type,
        "risk_rejection_type": readiness.get("risk_rejection_type"),
        "risk_rejection_reason": readiness.get("risk_rejection_reason"),
        "quote_quality_reason": readiness.get("quote_quality_reason"),
        "candidate_passes_trade_filters": readiness.get("candidate_passes_trade_filters"),
        "candidate_passes_risk_filters": readiness.get("candidate_passes_risk_filters"),
        "candidate_passes_quote_filters": readiness.get("candidate_passes_quote_filters"),
        "candidate_passes_score_threshold": readiness.get("candidate_passes_score_threshold"),
        "candidate_passes_score_edge": readiness.get("candidate_passes_score_edge"),
        "selector_eligible_base": readiness.get("selector_eligible_base"),
        "selector_blockers": _join_unique([
            readiness.get("selector_blockers") or [],
            sel_meta.get("selector_blockers") or [],
        ]),
        "side_allowed_by_config": sel_meta.get("side_allowed_by_config"),
        "selected_trade": selected,
        "skipped_reason": skipped_reason,
    }


def _trade_record(rec, c, settings, day_index, entry_ts, contracts) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    qty = max(1, int(contracts or 1))
    exit_res = simulate_exit(
        day_index, entry_ts=entry_ts, side=c.side,
        short_strike=c.short_strike, long_strike=c.long_strike,
        entry_credit_points=c.credit,
        take_profit_capture=settings.take_profit_capture,
        stop_loss_loss=settings.stop_loss_loss,
        contracts=qty,
    )
    trade = dict(rec)
    trade.update({
        "contracts": qty,
        "tp_mode": settings.take_profit_label,
        "sl_mode": settings.stop_loss_label,
        "exit_timestamp": exit_res.exit_timestamp.isoformat() if exit_res.exit_timestamp else None,
        "exit_reason": exit_res.exit_reason,
        "exit_debit_points": exit_res.exit_debit_points,
        "exit_debit_dollars": exit_res.exit_debit_dollars,
        "pnl_points": exit_res.pnl_points,
        "pnl_dollars": exit_res.pnl_dollars,
        "credit_kept_pct": exit_res.credit_kept_pct,
        "hold_minutes": exit_res.hold_minutes,
        "max_spot_after_entry": exit_res.max_spot_after_entry,
        "min_spot_after_entry": exit_res.min_spot_after_entry,
        "short_touched_after_entry": exit_res.short_touched_after_entry,
        "long_touched_after_entry": exit_res.long_touched_after_entry,
        "stop_triggered": exit_res.stop_triggered,
        "tp_triggered": exit_res.tp_triggered,
        "event_conflict": exit_res.event_conflict,
        "missing_price_count": exit_res.missing_price_count,
        "snapshots_checked": exit_res.snapshots_checked,
        "settlement_method": exit_res.settlement_method,
    })
    return trade


# ── public entry point ───────────────────────────────────────────────────────

def run_backtest(
    *,
    symbol: str,
    profile_ids: list[str],
    start: str | None = None,
    end: str | None = None,
    dte: int = 0,
    entry_override: str | None = None,
    limit: int = 0,
    latest_days: int = 0,
    trading_root: str | None = None,
    run_label: str = "run",
    starting_balance: float = 10000.0,
    contracts: int = 1,
) -> BacktestResult:
    """Run the backtest for one symbol across a date range for the given profiles."""
    symbol = (symbol or "SPX").strip().upper()
    try:
        starting_balance = float(starting_balance)
    except (TypeError, ValueError):
        starting_balance = 10000.0
    if starting_balance <= 0:
        raise ValueError("starting_balance must be greater than 0")
    try:
        contracts = int(contracts)
    except (TypeError, ValueError):
        contracts = 1
    if contracts <= 0:
        raise ValueError("contracts must be greater than 0")
    dte_label = schemas.DTE_1 if int(dte) == 1 else schemas.DTE_0
    root = L.trading_root(trading_root)
    cfg, strat = _load_cfg_and_strategy()
    scheme, warning = threshold_scheme(symbol)

    # Load + validate profiles up front.
    profiles = []
    for pid in profile_ids:
        res = load_profile_file(pid)
        if not res.ok or res.profile is None:
            raise ValueError(f"profile {pid!r} not loadable: {res.errors}")
        profiles.append(res.profile)

    # Resolve dates.
    dates = L.available_dates(symbol, dte_label, root=root)
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]
    if latest_days and latest_days > 0:
        dates = dates[-latest_days:]
    if limit and limit > 0:
        dates = dates[:limit]

    result = BacktestResult(run_config={
        "symbol": symbol, "dte": dte_label, "entry_override": entry_override,
        "profiles": [p.profile_id for p in profiles], "start": start, "end": end,
        "limit": limit, "latest_days": latest_days, "run_label": run_label,
        "threshold_scheme": scheme, "threshold_warning": warning,
        "trading_root": str(root), "option_multiplier": OPTION_MULTIPLIER,
        "starting_balance": round(starting_balance, 2), "contracts": contracts,
        "selector_path": "live select_daily_trade (Phase 5, reused)",
        "no_broker": True, "no_execution": True, "no_live_api": True,
    })

    session_cache: dict[str, SessionConfig] = {}
    files_found = 0
    valid_entries = 0
    for date in dates:
        csv_path = L.file_for_date(symbol, dte_label, date, root=root)
        if csv_path is None:
            result.no_trade_reasons.append(
                _no_trade_record(
                    date=date, symbol=symbol, dte_label=dte_label, profile_id="*",
                    status="skipped", reason="no_file_for_date",
                ))
            continue
        files_found += 1
        try:
            rows = L.load_raw_rows(csv_path, symbol)
        except (OSError, ValueError) as exc:
            result.no_trade_reasons.append(
                _no_trade_record(
                    date=date, symbol=symbol, dte_label=dte_label, profile_id="*",
                    status="error", reason=f"load_error:{type(exc).__name__}",
                    detail=str(exc),
                ))
            continue
        day_index = build_day_index(rows, symbol)
        result.dates_evaluated.append(date)

        for profile in profiles:
            settings = derive_run_settings(profile, entry_override=entry_override, dte_override=dte)
            session = _session_for(cfg, profile, session_cache)
            selector_cfg = selector_config_from_profile(profile)
            out = _evaluate(
                strat=strat, session=session, profile=profile, settings=settings,
                selector_cfg=selector_cfg, symbol=symbol, dte_label=dte_label, date=date,
                rows=rows, day_index=day_index, scheme=scheme, warning=warning,
                contracts=contracts,
            )
            result.candidates.extend(out["candidates"])
            result.trades.extend(out["trades"])
            if out["status"] == "ok" and out["candidates"]:
                valid_entries += 1
            if out["no_trade"] is not None:
                result.no_trade_reasons.append(out["no_trade"])

    result.counters = {
        "dates_in_range": len(dates),
        "files_found": files_found,
        "dates_evaluated": len(result.dates_evaluated),
        "valid_entry_snapshots": valid_entries,
        "candidates": len(result.candidates),
        "selected_trades": len(result.trades),
        "skipped_candidates": sum(1 for c in result.candidates if not c["selected_trade"]),
        "no_trade_rows": len(result.no_trade_reasons),
    }
    return result
