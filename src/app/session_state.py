"""Session-scoped risk + strategy overrides for the Streamlit cockpit.

The user picks a `RiskProfile` template at startup; this module then layers
an editable, in-memory `SessionConfig` over it. Every field that the
dashboard exposes as a control surface lives here. Changes are diffed
against the template and appended to `config_change_log.jsonl`.

Headless contexts (the scanner runner, tests) can use `SessionConfig`
directly without any Streamlit dependency — it's a plain dataclass with
helper constructors. `to_filter_params` mirrors `RiskProfile.to_filter_params`
so the filter chain accepts either input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from src.risk.limits import RiskProfile

# All editable fields the dashboard exposes. Order matters for the UI.
EDITABLE_FIELDS: tuple[str, ...] = (
    "starting_balance",
    "contracts_per_trade",
    "max_open_positions",
    "max_daily_loss_dollars",
    "max_daily_loss_percent",
    "max_planned_trade_loss_dollars",
    "max_planned_trade_loss_percent",
    "max_theoretical_trade_loss_dollars",
    "max_theoretical_trade_loss_percent",
    "default_spread_width",
    "default_stop_variant",
    "profit_targets",
    "no_trade_score_threshold",
    "min_credit",
    "max_bid_ask_width",
    "min_distance_from_spot",
)


@dataclass
class SessionConfig:
    """Per-session risk + filter overrides on top of a profile template."""

    # identity
    profile_name: str
    profile_label: str
    paper_only: bool = True

    # account
    starting_balance: float = 10000.0
    contracts_per_trade: int = 1
    max_open_positions: int = 1

    # daily caps
    max_daily_loss_dollars: float | None = None
    max_daily_loss_percent: float | None = None

    # per-trade caps (planned = primary gate; theoretical = ceiling)
    max_planned_trade_loss_dollars: float | None = None
    max_planned_trade_loss_percent: float | None = None
    max_theoretical_trade_loss_dollars: float | None = None
    max_theoretical_trade_loss_percent: float | None = None

    # spreads + stops
    default_spread_width: int = 5
    default_stop_variant: str = "BASELINE_CASH_SETTLE"
    profit_targets: list[float] = field(default_factory=list)

    # decision
    no_trade_score_threshold: float = 0.60

    # candidate filters
    min_credit: float = 0.30
    max_bid_ask_width: float = 0.20
    min_distance_from_spot: float = 10.0
    minimum_reward_risk: float = 0.10

    # ── construction helpers ──────────────────────────────────────────

    @classmethod
    def from_profile(cls, profile: RiskProfile) -> SessionConfig:
        """Snapshot the template into a mutable session config."""
        return cls(
            profile_name=profile.name,
            profile_label=profile.label,
            paper_only=profile.paper_only,
            starting_balance=profile.starting_balance,
            contracts_per_trade=profile.contracts_per_trade,
            max_open_positions=profile.max_open_positions,
            max_daily_loss_dollars=profile.max_daily_loss_dollars,
            max_daily_loss_percent=profile.max_daily_loss_percent,
            max_planned_trade_loss_dollars=profile.max_planned_trade_loss_dollars,
            max_planned_trade_loss_percent=profile.max_planned_trade_loss_percent,
            max_theoretical_trade_loss_dollars=profile.max_theoretical_trade_loss_dollars,
            max_theoretical_trade_loss_percent=profile.max_theoretical_trade_loss_percent,
            default_spread_width=profile.default_spread_width,
            default_stop_variant=profile.default_stop_variant,
            profit_targets=list(profile.profit_targets),
            no_trade_score_threshold=profile.no_trade_score_threshold,
            min_credit=float(profile.raw.get("minimum_credit_afternoon")
                             or profile.raw.get("minimum_credit_morning")
                             or 0.30),
            max_bid_ask_width=float(profile.raw.get("max_bid_ask_width", 0.20)),
            min_distance_from_spot=float(profile.raw.get("min_distance_from_spot", 10.0)),
            minimum_reward_risk=float(profile.raw.get("minimum_reward_risk", 0.10)),
        )

    def clone(self) -> SessionConfig:
        return replace(self, profit_targets=list(self.profit_targets))

    # ── serialization ─────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_filter_params(self) -> dict[str, Any]:
        """Shape that `src.risk.filters.apply_filters` consumes."""
        return {
            "account_balance": self.starting_balance,
            "contracts_per_trade": self.contracts_per_trade,
            "stop_variant": self.default_stop_variant,
            "spread_width": self.default_spread_width,
            "max_planned_trade_loss_percent": self.max_planned_trade_loss_percent,
            "max_planned_trade_loss_dollars": self.max_planned_trade_loss_dollars,
            "max_theoretical_trade_loss_percent": self.max_theoretical_trade_loss_percent,
            "max_theoretical_trade_loss_dollars": self.max_theoretical_trade_loss_dollars,
            "min_credit": self.min_credit,
            "max_bid_ask_width": self.max_bid_ask_width,
            "min_distance_from_spot_points": self.min_distance_from_spot,
            "minimum_reward_risk": self.minimum_reward_risk,
        }

    # ── diffing ───────────────────────────────────────────────────────

    def diff_against(self, baseline: SessionConfig) -> dict[str, tuple[Any, Any]]:
        """Return {field: (baseline_value, our_value)} for every editable field that differs."""
        out: dict[str, tuple[Any, Any]] = {}
        for f in EDITABLE_FIELDS:
            if getattr(self, f) != getattr(baseline, f):
                out[f] = (getattr(baseline, f), getattr(self, f))
        return out
