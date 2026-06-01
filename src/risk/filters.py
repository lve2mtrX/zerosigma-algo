"""Hard filters — black/white candidate gates run before scoring.

Each filter returns a (passed, reason) tuple. Failed candidates are *not*
dropped; they keep traveling through the pipeline marked `rejected=True` so
the decision log can show what was filtered and why.

The two risk gates are:
  - _f_planned_trade_loss_within_cap     → primary "can I take this?" gate
  - _f_theoretical_trade_loss_within_cap → hard ceiling on full defined risk

Each is a no-op if its cap (percent OR dollars) is not configured on the
risk profile. See src/risk/limits.py for the risk arithmetic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.risk.limits import (
    planned_loss_dollars,
    theoretical_max_loss_dollars,
)
from src.strategies.base import Candidate

FilterFn = Callable[[Candidate, dict[str, Any]], tuple[bool, str]]


# ──────────────────────────────────────────────────────────────────────
# Candidate-shape filters
# ──────────────────────────────────────────────────────────────────────

def _f_positive_credit(c: Candidate, _: dict[str, Any]) -> tuple[bool, str]:
    return (c.credit > 0, "credit must be positive")


def _f_min_credit(c: Candidate, p: dict[str, Any]) -> tuple[bool, str]:
    floor = float(p.get("min_credit") or 0.0)
    return (c.credit >= floor, f"credit below floor {floor:.2f}")


def _f_min_distance(c: Candidate, p: dict[str, Any]) -> tuple[bool, str]:
    min_d = float(p.get("min_distance_from_spot_points") or 0)
    return (abs(c.distance_from_spot) >= min_d, f"short strike < {min_d}pt from spot")


def _f_reward_risk(c: Candidate, p: dict[str, Any]) -> tuple[bool, str]:
    min_rr = float(p.get("minimum_reward_risk") or 0.0)
    return (c.reward_risk >= min_rr, f"reward:risk {c.reward_risk:.2f} < {min_rr:.2f}")


# ──────────────────────────────────────────────────────────────────────
# Risk-cap filters (both no-op if their cap is None)
# ──────────────────────────────────────────────────────────────────────

def _resolve_dollar_cap(
    pct_cap: float | None,
    dollar_cap: float | None,
    balance: float,
) -> float | None:
    """Effective dollar cap is the TIGHTER of pct×balance and absolute dollars.

    Returns None when neither is configured (filter becomes a no-op).
    """
    caps: list[float] = []
    if pct_cap is not None:
        caps.append(float(pct_cap) * float(balance))
    if dollar_cap is not None:
        caps.append(float(dollar_cap))
    return min(caps) if caps else None


def _f_planned_trade_loss_within_cap(c: Candidate, p: dict[str, Any]) -> tuple[bool, str]:
    """Primary 'can I take this?' gate. Uses planned stop risk under the active stop variant."""
    cap = _resolve_dollar_cap(
        p.get("max_planned_trade_loss_percent"),
        p.get("max_planned_trade_loss_dollars"),
        float(p.get("account_balance", 0)),
    )
    if cap is None:
        return (True, "")  # no cap configured → pass
    contracts = int(p.get("contracts_per_trade", 1))
    stop = str(p.get("stop_variant", "BASELINE_CASH_SETTLE"))
    risk_dollars = planned_loss_dollars(c.credit, c.max_risk, stop, contracts)
    return (
        risk_dollars <= cap,
        (
            f"planned stop risk ${risk_dollars:.0f} > cap ${cap:.0f} "
            f"({stop}, {contracts} contracts)"
        ),
    )


def _f_theoretical_trade_loss_within_cap(c: Candidate, p: dict[str, Any]) -> tuple[bool, str]:
    """Hard ceiling on the full defined-risk loss (spread fully ITM, no stop fires)."""
    cap = _resolve_dollar_cap(
        p.get("max_theoretical_trade_loss_percent"),
        p.get("max_theoretical_trade_loss_dollars"),
        float(p.get("account_balance", 0)),
    )
    if cap is None:
        return (True, "")
    contracts = int(p.get("contracts_per_trade", 1))
    risk_dollars = theoretical_max_loss_dollars(c.max_risk, contracts)
    return (
        risk_dollars <= cap,
        f"theoretical max loss ${risk_dollars:.0f} > cap ${cap:.0f} ({contracts} contracts)",
    )


# ──────────────────────────────────────────────────────────────────────
# Default chain
# ──────────────────────────────────────────────────────────────────────

DEFAULT_FILTERS: list[FilterFn] = [
    _f_positive_credit,
    _f_min_credit,
    _f_min_distance,
    _f_planned_trade_loss_within_cap,
    _f_theoretical_trade_loss_within_cap,
    _f_reward_risk,
]


def apply_filters(
    candidates: list[Candidate],
    params: dict[str, Any],
    filters: list[FilterFn] | None = None,
) -> list[Candidate]:
    """Mark candidates rejected (with reasons) in place; return the same list."""
    fns = filters if filters is not None else DEFAULT_FILTERS
    for c in candidates:
        for fn in fns:
            ok, reason = fn(c, params)
            if not ok:
                c.rejected = True
                c.rejection_reasons.append(reason)
    return candidates
