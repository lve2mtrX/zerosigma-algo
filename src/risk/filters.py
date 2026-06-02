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


def _stamp_risk_rejection(
    c: Candidate,
    *,
    type_: str,
    risk_dollars: float,
    cap: float | None,
    passed: bool,
    reason: str | None,
    stop_variant: str | None,
    contracts: int,
) -> None:
    """Phase 4.1 — stamp structured risk-rejection fields onto Candidate.meta.

    The dict ALWAYS records 'passed' explicitly, so downstream consumers
    (readiness, CSV, Streamlit) must consult `passed` rather than treating
    key presence as failure. The scalar `risk_rejection_type` is the most
    recent FAILED cap (or None when both caps passed).

    Per-cap detail under c.meta['risk_rejections'][type_]:
        {type, risk_dollars, cap_dollars, stop_variant, contracts,
         passed, reason}
    Scalar mirrors set on c.meta:
        - planned_stop_risk_dollars / planned_stop_risk_cap_dollars /
          planned_stop_risk_passed             (for type_='planned_loss_cap')
        - theoretical_loss_dollars / theoretical_loss_cap_dollars /
          theoretical_loss_passed              (for type_='theoretical_loss_cap')
        - risk_rejection_type                  (= last failing type or None)
    """
    risk_rejections = c.meta.setdefault("risk_rejections", {})
    risk_rejections[type_] = {
        "type":         type_,
        "risk_dollars": float(risk_dollars),
        "cap_dollars":  (float(cap) if cap is not None else None),
        "stop_variant": stop_variant,
        "contracts":    int(contracts),
        "passed":       bool(passed),
        "reason":       reason,
    }
    if type_ == "planned_loss_cap":
        c.meta["planned_stop_risk_dollars"]     = float(risk_dollars)
        c.meta["planned_stop_risk_cap_dollars"] = (float(cap) if cap is not None else None)
        c.meta["planned_stop_risk_passed"]      = bool(passed)
    elif type_ == "theoretical_loss_cap":
        c.meta["theoretical_loss_dollars"]      = float(risk_dollars)
        c.meta["theoretical_loss_cap_dollars"]  = (float(cap) if cap is not None else None)
        c.meta["theoretical_loss_passed"]       = bool(passed)
    # Scalar `risk_rejection_type` = most-recent failing cap, or None if
    # everything passed so far. Both passes => None; one fails => name it.
    failed = [k for k, v in risk_rejections.items() if v.get("passed") is False]
    c.meta["risk_rejection_type"] = failed[-1] if failed else None


def _f_planned_trade_loss_within_cap(c: Candidate, p: dict[str, Any]) -> tuple[bool, str]:
    """Primary 'can I take this?' gate. Uses planned stop risk under the active stop variant."""
    contracts = int(p.get("contracts_per_trade", 1))
    stop = str(p.get("stop_variant", "BASELINE_CASH_SETTLE"))
    cap = _resolve_dollar_cap(
        p.get("max_planned_trade_loss_percent"),
        p.get("max_planned_trade_loss_dollars"),
        float(p.get("account_balance", 0)),
    )
    risk_dollars = planned_loss_dollars(c.credit, c.max_risk, stop, contracts)
    if cap is None:
        # No cap configured → pass. Still stamp the structured fields so
        # readiness / CSV can observe the planned risk number even when no
        # cap is in force.
        _stamp_risk_rejection(
            c, type_="planned_loss_cap",
            risk_dollars=risk_dollars, cap=None, passed=True, reason=None,
            stop_variant=stop, contracts=contracts,
        )
        return (True, "")
    passed = risk_dollars <= cap
    reason = (
        None if passed else
        f"planned stop risk ${risk_dollars:.0f} > cap ${cap:.0f} "
        f"({stop}, {contracts} contracts)"
    )
    _stamp_risk_rejection(
        c, type_="planned_loss_cap",
        risk_dollars=risk_dollars, cap=cap, passed=passed, reason=reason,
        stop_variant=stop, contracts=contracts,
    )
    return (passed, reason or "")


def _f_theoretical_trade_loss_within_cap(c: Candidate, p: dict[str, Any]) -> tuple[bool, str]:
    """Hard ceiling on the full defined-risk loss (spread fully ITM, no stop fires)."""
    contracts = int(p.get("contracts_per_trade", 1))
    cap = _resolve_dollar_cap(
        p.get("max_theoretical_trade_loss_percent"),
        p.get("max_theoretical_trade_loss_dollars"),
        float(p.get("account_balance", 0)),
    )
    risk_dollars = theoretical_max_loss_dollars(c.max_risk, contracts)
    if cap is None:
        _stamp_risk_rejection(
            c, type_="theoretical_loss_cap",
            risk_dollars=risk_dollars, cap=None, passed=True, reason=None,
            stop_variant=None, contracts=contracts,
        )
        return (True, "")
    passed = risk_dollars <= cap
    reason = (
        None if passed else
        f"theoretical max loss ${risk_dollars:.0f} > cap ${cap:.0f} ({contracts} contracts)"
    )
    _stamp_risk_rejection(
        c, type_="theoretical_loss_cap",
        risk_dollars=risk_dollars, cap=cap, passed=passed, reason=reason,
        stop_variant=None, contracts=contracts,
    )
    return (passed, reason or "")


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
