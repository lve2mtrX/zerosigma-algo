"""Phase 10B — derive runtime backtest settings from a StrategyProfile.

PURE: turns a Phase 6 ``StrategyProfile`` (+ optional CLI overrides) into the
concrete knobs the replay runner needs — entry target, wing VOLUME threshold,
spread width, allowed sides, selector mode, TP/SL exit fractions, target DTE.

Behavior is read from PROFILE FIELDS (``threshold_label`` / ``target_time`` /
``allow_*_credit`` / ``daily_selector`` / ``take_profit_pct`` / ``stop_loss_pct``
/ ``target_dte``), never hardcoded by profile name. Name-based fallbacks are
avoided because the schema already carries the information.

TP/SL semantics (match the reference vertical_wing_backtest + the spec):
  * ``take_profit_pct`` is the CREDIT-CAPTURE fraction. TP fires when the
    debit-to-close falls to ``(1 - capture) * credit``:
        capture 0.50 (TP50) -> debit <= 0.50 * credit
        capture 0.75 (TP75) -> debit <= 0.25 * credit
  * ``stop_loss_pct`` is the LOSS fraction on the credit. SL fires when the
    debit-to-close reaches ``(1 + loss) * credit``:
        loss 1.50 (SL150) -> debit >= 2.50 * credit
        loss 2.00 (SL200) -> debit >= 3.00 * credit
This differs from the live PaperLifecycleConfig debit-fraction convention by
design; the historical simulator (``lifecycle_sim``) owns these thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config.strategy_profiles import StrategyProfile
from src.selector.daily_selector import SelectorConfig

# SPX uses the standard 2K/5K/10K volume thresholds (validated vs wingonomics).
# SPY/QQQ reuse the SPX thresholds PROVISIONALLY until symbol-specific
# calibration lands (a documented Phase 10C task — see plan.md).
_PROVISIONAL_THRESHOLD_SYMBOLS = frozenset({"SPY", "QQQ"})

_CALL = "CALL_CREDIT"
_PUT = "PUT_CREDIT"


def threshold_scheme(symbol: str) -> tuple[str, str | None]:
    """Return ``(scheme_label, warning_or_None)`` for a symbol's wing thresholds."""
    s = (symbol or "").strip().upper()
    if s in _PROVISIONAL_THRESHOLD_SYMBOLS:
        return (
            "provisional_spx_2k5k10k",
            f"{s} reuses SPX 2K/5K/10K volume thresholds (PROVISIONAL — "
            "symbol-specific calibration is a future step; interpret results with care)",
        )
    return ("spx_2k5k10k_standard", None)


def _threshold_from_label(label: str | None) -> float:
    raw = (label or "").strip().lower()
    return {
        "1k": 1000.0, "1000": 1000.0,
        "2k": 2000.0, "2000": 2000.0,
        "5k": 5000.0, "5000": 5000.0,
        "10k": 10000.0, "10000": 10000.0,
    }.get(raw, 2000.0)


def _tp_label(capture: float | None) -> str:
    if capture is None:
        return "NO_TP"
    if abs(capture - 0.75) < 1e-9:
        return "TP75"
    if abs(capture - 0.50) < 1e-9:
        return "TP50"
    return f"TP{round(capture * 100)}"


def _sl_label(loss: float | None) -> str:
    if loss is None:
        return "NO_SL"
    if abs(loss - 1.00) < 1e-9:
        return "SL100"
    if abs(loss - 1.50) < 1e-9:
        return "SL150"
    if abs(loss - 2.00) < 1e-9:
        return "SL200"
    return f"SL{round(loss * 100)}"


@dataclass(frozen=True)
class RunSettings:
    """Concrete, name-independent backtest knobs derived from a profile."""

    profile_id: str
    preset_kind: str | None
    side_policy: str | None
    entry_target: str
    volume_threshold: float
    threshold_label: str
    spread_width: float
    allow_call_credit: bool
    allow_put_credit: bool
    selector_mode: str
    take_profit_capture: float | None   # credit-capture fraction (0.50 / 0.75) or None
    take_profit_label: str              # "TP50" | "TP75" | "NO_TP"
    stop_loss_loss: float | None        # loss fraction (1.50 / 2.00) or None
    stop_loss_label: str                # "SL150" | "SL200" | "NO_SL"
    target_dte: int
    no_trade_score_threshold: float
    sides_evaluated: tuple[str, ...]    # sides the selector may actually pick


def derive_run_settings(
    profile: StrategyProfile,
    *,
    entry_override: str | None = None,
    dte_override: int | None = None,
    default_spread_width: float = 5.0,
) -> RunSettings:
    """Build :class:`RunSettings` from a profile + optional CLI overrides."""
    p = profile
    entry = entry_override or p.target_time or p.entry_window_start or "11:00"
    vt = (
        float(p.wing_threshold)
        if p.wing_threshold is not None
        else _threshold_from_label(p.threshold_label)
    )
    width = float(p.spread_width) if p.spread_width is not None else float(default_spread_width)
    tp = float(p.take_profit_pct) if p.take_profit_pct is not None else None
    sl = float(p.stop_loss_pct) if p.stop_loss_pct is not None else None
    dte = int(dte_override) if dte_override is not None else int(p.target_dte)
    nts = (
        float(p.no_trade_score_threshold)
        if p.no_trade_score_threshold is not None
        else 0.60
    )
    sides = tuple(
        s for s, ok in ((_CALL, p.allow_call_credit), (_PUT, p.allow_put_credit)) if ok
    )
    label = p.threshold_label or f"{int(vt) // 1000}k"
    return RunSettings(
        profile_id=p.profile_id,
        preset_kind=p.preset_kind,
        side_policy=p.side_policy,
        entry_target=str(entry).strip(),
        volume_threshold=vt,
        threshold_label=label,
        spread_width=width,
        allow_call_credit=bool(p.allow_call_credit),
        allow_put_credit=bool(p.allow_put_credit),
        selector_mode=p.daily_selector,
        take_profit_capture=tp,
        take_profit_label=_tp_label(tp),
        stop_loss_loss=sl,
        stop_loss_label=_sl_label(sl),
        target_dte=dte,
        no_trade_score_threshold=nts,
        sides_evaluated=sides,
    )


def selector_config_from_profile(profile: StrategyProfile) -> SelectorConfig:
    """Mirror ``run_scanner``'s SelectorConfig construction from profile fields.

    The selector itself is reused verbatim (``select_daily_trade``); this only
    assembles its config from the profile so the SAME selection logic runs.
    """
    return SelectorConfig(
        mode=profile.daily_selector,
        max_trades_per_day=int(profile.max_trades_per_day),
        allow_call_credit=bool(profile.allow_call_credit),
        allow_put_credit=bool(profile.allow_put_credit),
        require_selector_eligible_base=bool(profile.require_selector_eligible_base),
        require_quote_validation=bool(profile.require_quote_validation),
        require_score_edge=bool(profile.require_score_edge),
        min_selector_score=profile.min_selector_score,
        min_selector_credit=profile.min_selector_credit,
        min_selector_distance_from_spot=profile.min_selector_distance_from_spot,
        max_selector_distance_from_spot=profile.max_selector_distance_from_spot,
    )
