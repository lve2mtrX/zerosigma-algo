"""Risk profile loader + circuit breakers + risk arithmetic.

Two risk concepts are tracked independently for every candidate:

  - theoretical_max_loss_per_spread = spread_width - credit
        what the spread loses if it goes fully ITM with no stop fired.

  - planned_loss_per_spread        = max(credit * (stop_mult - 1), 0)
                                     capped above by theoretical max loss.
        what we INTEND to lose if our stop fires.

Both have separate caps on `RiskProfile`. The "can I take this?" gate
uses the planned cap; the theoretical cap is a separate hard ceiling.

BASELINE_CASH_SETTLE handling (documented):
  When the user picks no stop, `planned_loss_per_spread` falls back to
  theoretical max loss. Rationale:
    - safer: a no-stop trade is sized as if the full defined risk could
      realize, instead of waved through;
    - clearer: one consistent formula (planned = min(stop-derived,
      theoretical)) instead of an "undefined" special case.
  If you prefer "fail any filter requiring planned-stop protection",
  use a guard like `requires_stop_variant != "BASELINE_CASH_SETTLE"`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Debit threshold = credit × STOP_DEBIT_MULT[variant]
# Realized loss per spread at that stop = credit × STOP_LOSS_MULT[variant]
# (i.e. STOP_LOSS_MULT = STOP_DEBIT_MULT - 1, since loss = debit - credit.)
STOP_DEBIT_MULT: dict[str, float] = {
    "BASELINE_CASH_SETTLE": float("inf"),
    "SL_100_PERCENT_LOSS":  2.0,
    "SL_150_PERCENT_LOSS":  2.5,
    "SL_200_PERCENT_LOSS":  3.0,
}
STOP_LOSS_MULT: dict[str, float] = {
    "BASELINE_CASH_SETTLE": 0.0,    # see realized_loss_for() caveat
    "SL_100_PERCENT_LOSS":  1.0,
    "SL_150_PERCENT_LOSS":  1.5,
    "SL_200_PERCENT_LOSS":  2.0,
}

OPTION_MULTIPLIER = 100  # standard equity-index option contract multiplier


# ──────────────────────────────────────────────────────────────────────
# Per-spread arithmetic (units: spread-price points, NOT dollars)
# ──────────────────────────────────────────────────────────────────────

def theoretical_max_loss_per_spread(max_risk: float) -> float:
    """The full defined-risk loss = spread_width - credit, per spread.

    `max_risk` on a Candidate is already (spread_width - credit), so this
    is essentially an identity with a defensive non-negative clamp.
    """
    return max(float(max_risk), 0.0)


def planned_loss_per_spread(
    credit: float,
    max_risk: float,
    stop_variant: str,
) -> float:
    """Per-spread loss if the stop fires (or, for BASELINE, the theoretical max).

    Returns a NON-NEGATIVE per-spread loss in spread-price points
    (multiply by OPTION_MULTIPLIER × contracts to get dollars).

    BASELINE_CASH_SETTLE → theoretical max loss (safer + clearer fallback).
    Any other variant → credit × (mult - 1), capped at theoretical.
    """
    theoretical = theoretical_max_loss_per_spread(max_risk)
    mult = STOP_DEBIT_MULT.get(stop_variant, float("inf"))
    if mult == float("inf"):
        return theoretical
    stop_derived = max(float(credit) * (mult - 1.0), 0.0)
    return min(stop_derived, theoretical)


# ──────────────────────────────────────────────────────────────────────
# Dollar conversions
# ──────────────────────────────────────────────────────────────────────

def theoretical_max_loss_dollars(max_risk: float, contracts: int) -> float:
    return theoretical_max_loss_per_spread(max_risk) * OPTION_MULTIPLIER * int(contracts)


def planned_loss_dollars(
    credit: float,
    max_risk: float,
    stop_variant: str,
    contracts: int,
) -> float:
    return planned_loss_per_spread(credit, max_risk, stop_variant) * OPTION_MULTIPLIER * int(contracts)


# ──────────────────────────────────────────────────────────────────────
# Paper-accounting helper (kept for src/paper/account.py.force_stop)
# ──────────────────────────────────────────────────────────────────────

def realized_loss_for(credit: float, variant: str) -> float:
    """Per-spread realized loss in spread-price points (NEGATIVE).

    Multiply by contracts × OPTION_MULTIPLIER to get dollar P&L.
    BASELINE_CASH_SETTLE returns 0 here; the paper account should not
    call `force_stop` on a no-stop trade — it should let it settle.
    """
    return -float(credit) * STOP_LOSS_MULT.get(variant, 0.0)


# ──────────────────────────────────────────────────────────────────────
# RiskProfile
# ──────────────────────────────────────────────────────────────────────

@dataclass
class RiskProfile:
    name: str
    raw: dict[str, Any]

    # ── identity / mode ──
    @property
    def label(self) -> str: return str(self.raw.get("label", self.name))
    @property
    def paper_only(self) -> bool: return bool(self.raw.get("paper_only", False))

    # ── account ──
    @property
    def starting_balance(self) -> float: return float(self.raw.get("starting_balance", 10000))
    @property
    def contracts_per_trade(self) -> int: return int(self.raw.get("contracts_per_trade", 1))
    @property
    def max_open_positions(self) -> int: return int(self.raw.get("max_open_positions", 1))

    # ── spreads ──
    @property
    def allowed_spread_widths(self) -> list[int]:
        return [int(w) for w in self.raw.get("allowed_spread_widths", [])]
    @property
    def default_spread_width(self) -> int: return int(self.raw.get("default_spread_width", 5))
    @property
    def default_stop_variant(self) -> str:
        return str(self.raw.get("default_stop_variant", "BASELINE_CASH_SETTLE"))
    @property
    def profit_targets(self) -> list[float]:
        return [float(t) for t in self.raw.get("profit_targets", [])]

    # ── per-trade risk caps ──
    @property
    def max_planned_trade_loss_percent(self) -> float | None:
        v = self.raw.get("max_planned_trade_loss_percent")
        return float(v) if v is not None else None
    @property
    def max_planned_trade_loss_dollars(self) -> float | None:
        v = self.raw.get("max_planned_trade_loss_dollars")
        return float(v) if v is not None else None
    @property
    def max_theoretical_trade_loss_percent(self) -> float | None:
        v = self.raw.get("max_theoretical_trade_loss_percent")
        return float(v) if v is not None else None
    @property
    def max_theoretical_trade_loss_dollars(self) -> float | None:
        v = self.raw.get("max_theoretical_trade_loss_dollars")
        return float(v) if v is not None else None

    # ── daily caps ──
    @property
    def max_daily_loss_dollars(self) -> float | None:
        v = self.raw.get("max_daily_loss_dollars")
        return float(v) if v is not None else None
    @property
    def max_daily_loss_percent(self) -> float | None:
        v = self.raw.get("max_daily_loss_percent")
        return float(v) if v is not None else None

    # ── decision / gating ──
    @property
    def no_trade_score_threshold(self) -> float: return float(self.raw.get("no_trade_score_threshold", 0.6))
    @property
    def event_day_avoidance(self) -> bool: return bool(self.raw.get("event_day_avoidance", True))
    @property
    def no_trade_dates(self) -> list[str]: return list(self.raw.get("no_trade_dates", []))


# ──────────────────────────────────────────────────────────────────────
# Profile loading + circuit breakers
# ──────────────────────────────────────────────────────────────────────

def load_profile(
    risk_profiles_cfg: dict[str, Any],
    name: str | None = None,
) -> RiskProfile:
    """Look up a profile by name. Falls back to the first available
    profile (not a hardcoded "default") when name is missing/unknown."""
    if name and name in risk_profiles_cfg:
        return RiskProfile(name=name, raw=dict(risk_profiles_cfg[name]))
    if not risk_profiles_cfg:
        return RiskProfile(name="empty", raw={})
    first_name = next(iter(risk_profiles_cfg))
    return RiskProfile(name=first_name, raw=dict(risk_profiles_cfg[first_name]))


def profile_to_filter_params(profile: RiskProfile) -> dict[str, Any]:
    """Map a RiskProfile to the params dict consumed by src.risk.filters.

    Lets the scanner/orchestrator say `apply_filters(c, profile_to_filter_params(p))`
    without juggling field names.
    """
    return {
        # account
        "account_balance": profile.starting_balance,
        "contracts_per_trade": profile.contracts_per_trade,
        # spread + stop
        "spread_width": profile.default_spread_width,
        "stop_variant": profile.default_stop_variant,
        # per-trade caps (None → filter is a no-op)
        "max_planned_trade_loss_percent": profile.max_planned_trade_loss_percent,
        "max_planned_trade_loss_dollars": profile.max_planned_trade_loss_dollars,
        "max_theoretical_trade_loss_percent": profile.max_theoretical_trade_loss_percent,
        "max_theoretical_trade_loss_dollars": profile.max_theoretical_trade_loss_dollars,
        # candidate filters
        "min_credit": profile.raw.get("minimum_credit_afternoon")
                       or profile.raw.get("minimum_credit_morning") or 0.0,
        "max_bid_ask_width": profile.raw.get("max_bid_ask_width"),
        "min_distance_from_spot_points": profile.raw.get("min_distance_from_spot", 0),
        "minimum_reward_risk": profile.raw.get("minimum_reward_risk", 0.0),
    }


def daily_loss_breach(realized_pnl: float, profile: RiskProfile) -> tuple[bool, str | None]:
    cap_d = profile.max_daily_loss_dollars
    if cap_d is not None and realized_pnl <= -cap_d:
        return (True, f"daily loss ${-realized_pnl:.0f} >= cap ${cap_d:.0f}")
    cap_p = profile.max_daily_loss_percent
    if cap_p is not None and realized_pnl < 0:
        pct = abs(realized_pnl) / max(1.0, profile.starting_balance)
        if pct >= cap_p:
            return (True, f"daily loss {pct*100:.1f}% >= cap {cap_p*100:.1f}%")
    return (False, None)


def position_cap_breach(open_count: int, profile: RiskProfile) -> tuple[bool, str | None]:
    if open_count >= profile.max_open_positions:
        return (True, f"open positions {open_count} >= cap {profile.max_open_positions}")
    return (False, None)


# Legacy shorthand kept for src/paper/account.py.force_stop
def stop_debit_for(credit: float, variant: str) -> float:
    return credit * STOP_DEBIT_MULT.get(variant, float("inf"))
