"""Local paper-trade lifecycle data models.

LOCAL PAPER ACCOUNTING ONLY. These dataclasses describe simulated positions and
the lifecycle configuration that governs them. Nothing here places,
previews, submits, or routes an order — there is no brokerage anywhere in this
module. Every portfolio ledger stamps ``no_execution=True`` and
``execution_mode="local_paper_lifecycle_only"``.

Credit-spread P&L convention (shared with src/paper/manual_tracker.py — we reuse
its math, never re-derive it):
  * entry_credit is POSITIVE (cash received to open).
  * current value to close is a POSITIVE debit.
  * unrealized_pnl = (entry_credit - current_debit) * 100 * contracts.
  * realized_pnl   = (entry_credit - exit_debit)    * 100 * contracts.
  * max_profit = entry_credit * 100 * contracts.
  * max_loss (magnitude) = (spread_width - entry_credit) * 100 * contracts.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal

# Audit markers so a grep can prove there is no execution surface here.
NO_EXECUTION = True
EXECUTION_MODE = "local_paper_lifecycle_only"

Side = Literal["CALL_CREDIT", "PUT_CREDIT", "LONG_CALL", "LONG_PUT"]
TradeStatus = Literal["open", "closed", "duplicate_skipped", "error"]
ExitReason = Literal[
    "take_profit",
    "stop_loss",
    "eod_exit",
    "manual_mark_closed",
    "quote_unavailable_no_exit",
    "quote_invalid",
    "quote_failure_limit",
    "regime_thesis_invalidated",
    "error",
]
EventType = Literal["open", "update", "close", "duplicate_skipped", "blocked_by_limits"]


@dataclass
class PaperTrade:
    """One simulated position and its running lifecycle state.

    All fields default so the record can be built incrementally and serialized
    to CSV/JSONL via :meth:`to_row`. ``trade_identity`` is an internal dedup /
    reconciliation key (not part of the spec's field list but persisted so the
    reconciler can detect duplicate-open identities)."""

    paper_trade_id: str = ""
    run_id: str = ""
    profile_id: str = ""
    profile_hash: str = ""
    strategy_id: str = ""
    symbol: str = ""
    side: str = ""
    selected_expiry: str | None = None
    target_dte: int | None = None
    opened_at: str | None = None
    closed_at: str | None = None
    status: str = "open"
    short_strike: float | None = None
    long_strike: float | None = None
    spread_width: float | None = None
    contracts: int = 1
    entry_credit: float | None = None
    entry_bid: float | None = None
    entry_ask: float | None = None
    entry_mid: float | None = None
    entry_quote_timestamp: str | None = None
    current_mark: float | None = None
    current_bid: float | None = None
    current_ask: float | None = None
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    planned_stop_risk_dollars: float | None = None
    theoretical_max_loss_dollars: float | None = None
    tp_rule: str | None = None
    sl_rule: str | None = None
    exit_rule: str | None = None
    exit_reason: str | None = None
    exit_credit_or_debit: float | None = None
    mae: float | None = None  # max adverse excursion ($, most negative unrealized seen)
    mfe: float | None = None  # max favorable excursion ($, most positive unrealized seen)
    ticks_held: int = 0
    notes: str | None = None
    # internal (dedup / reconciliation) — persisted, not in the spec field list
    trade_identity: str | None = None
    # Phase 11D extensions. Appended for backward-compatible CSV readers.
    source_candidate_id: str | None = None
    archetype: str = "CALL_CREDIT_SPREAD"
    legs_json: str | None = None
    entry_price_type: str = "credit"
    entry_debit: float | None = None
    risk_reward: float | None = None
    risk_quality_label: str | None = None
    entry_regime_json: str | None = None
    current_regime_json: str | None = None
    entry_reason_codes: str | None = None
    latest_reason_codes: str | None = None
    exit_reason_codes: str | None = None
    thesis: str | None = None
    target_mark: float | None = None
    stop_mark: float | None = None
    invalidation_level: float | None = None
    current_quote_timestamp: str | None = None
    missing_quote_marks: int = 0
    credit_kept_pct: float | None = None
    distance_to_short_strike: float | None = None
    latest_decision: str = "HOLD"
    latest_explanation: str | None = None
    local_paper_only: bool = True
    no_broker_order_sent: bool = True

    def to_row(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PaperTrade:
        """Build from a CSV/JSON row, coercing numeric strings and ignoring
        unknown keys. Tolerant: never raises on a malformed value."""
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for k, v in row.items():
            if k not in known:
                continue
            kwargs[k] = _coerce(k, v)
        return cls(**kwargs)


# field-name sets used for tolerant coercion when reading ledger rows back
_INT_FIELDS = frozenset({"target_dte", "contracts", "ticks_held", "missing_quote_marks"})
_FLOAT_FIELDS = frozenset({
    "short_strike", "long_strike", "spread_width", "entry_credit", "entry_bid",
    "entry_ask", "entry_mid", "current_mark", "current_bid", "current_ask",
    "unrealized_pnl", "realized_pnl", "max_profit", "max_loss",
    "planned_stop_risk_dollars", "theoretical_max_loss_dollars",
    "exit_credit_or_debit", "mae", "mfe", "entry_debit", "risk_reward",
    "target_mark", "stop_mark", "invalidation_level", "credit_kept_pct",
    "distance_to_short_strike",
})
_BOOL_FIELDS = frozenset({"local_paper_only", "no_broker_order_sent"})


def _coerce(key: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    if key in _INT_FIELDS:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    if key in _FLOAT_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if key in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return value


@dataclass(frozen=True)
class PaperTradeTicket:
    """Immutable local-only entry ticket built from an accepted candidate."""

    ticket_id: str
    source_candidate_id: str
    profile_id: str
    profile_hash: str
    symbol: str
    archetype: str
    contracts: int
    dte: int
    expiry: str
    legs: tuple[dict[str, Any], ...]
    entry_credit: float | None
    entry_debit: float | None
    max_profit: float | None
    max_loss: float | None
    risk_reward: float | None
    risk_quality_label: str
    regime_snapshot_at_entry: dict[str, Any] | None
    entry_reason_codes: tuple[str, ...]
    plain_english_thesis: str
    target_mark: float | None = None
    stop_mark: float | None = None
    invalidation_level: float | None = None
    local_paper_only: bool = True
    no_broker_order_sent: bool = True

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["legs"] = list(self.legs)
        row["entry_reason_codes"] = list(self.entry_reason_codes)
        return row


@dataclass(frozen=True)
class PaperMark:
    timestamp: str
    paper_trade_id: str
    current_leg_quote_values: tuple[dict[str, Any], ...]
    current_mark: float | None
    unrealized_pnl: float | None
    credit_kept_pct: float | None
    distance_to_short_strike: float | None
    current_regime_snapshot: dict[str, Any] | None
    exit_checks: dict[str, bool]
    decision: str
    reason_codes: tuple[str, ...]
    plain_english_reason: str

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["current_leg_quote_values"] = list(self.current_leg_quote_values)
        row["reason_codes"] = list(self.reason_codes)
        return row

    def to_csv_row(self) -> dict[str, Any]:
        import json

        row = self.to_dict()
        row["current_leg_quote_values"] = json.dumps(
            row["current_leg_quote_values"], sort_keys=True
        )
        row["current_regime_snapshot"] = json.dumps(
            row["current_regime_snapshot"], sort_keys=True
        )
        row["exit_checks"] = json.dumps(row["exit_checks"], sort_keys=True)
        row["reason_codes"] = "; ".join(self.reason_codes)
        return row


@dataclass(frozen=True)
class ExecutionJournalEvent:
    timestamp: str
    action: str
    paper_trade_id: str | None
    profile_id: str | None
    quote_values_used: dict[str, Any]
    regime_snapshot_summary: str | None
    risk_quality_summary: str | None
    reason_codes: tuple[str, ...]
    plain_english_explanation: str
    pnl_impact: float | None
    local_paper_only: bool = True
    no_broker_order_sent: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["reason_codes"] = list(self.reason_codes)
        return row


# ── lifecycle configuration ──────────────────────────────────────────────────

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


@dataclass
class PaperLifecycleConfig:
    """TP/SL/EOD + duplicate/limit knobs. Sourced from env (PAPER_*) with CLI
    overrides on top. NOT part of the Phase 6 strategy-profile schema — lifecycle
    config lives in env / CLI / an optional portfolio_profiles.yaml so the Phase 6
    profile schema is left unchanged (no execution intent ever enters a profile)."""

    enabled: bool = True
    contracts: int = 1
    take_profit_pct: float = 0.50  # close when debit <= entry_credit * this
    stop_loss_pct: float = 1.50    # close when debit >= entry_credit * this
    exit_on_eod: bool = True
    eod_exit_time: str = "15:55"   # ET "HH:MM"
    allow_multiple_open_per_profile: bool = False
    allow_duplicate_strikes: bool = False
    max_open_trades_total: int = 5
    max_open_trades_per_profile: int = 1
    position_reconciliation_mode: str = "local_only"
    max_quote_age_seconds: float = 120.0
    max_missing_quote_marks: int = 3
    regime_exit_enabled: bool = True
    slippage_points: float = 0.0

    @classmethod
    def from_env(cls) -> PaperLifecycleConfig:
        return cls(
            enabled=_env_bool("PAPER_LIFECYCLE_ENABLED", True),
            contracts=_env_int("PAPER_CONTRACTS", 1),
            take_profit_pct=_env_float("PAPER_TAKE_PROFIT_PCT", 0.50),
            stop_loss_pct=_env_float("PAPER_STOP_LOSS_PCT", 1.50),
            exit_on_eod=_env_bool("PAPER_EXIT_ON_EOD", True),
            eod_exit_time=_env_str("PAPER_EOD_EXIT_TIME", "15:55"),
            allow_multiple_open_per_profile=_env_bool(
                "PAPER_ALLOW_MULTIPLE_OPEN_PER_PROFILE", False),
            allow_duplicate_strikes=_env_bool("PAPER_ALLOW_DUPLICATE_STRIKES", False),
            max_open_trades_total=_env_int("PAPER_MAX_OPEN_TRADES_TOTAL", 5),
            max_open_trades_per_profile=_env_int("PAPER_MAX_OPEN_TRADES_PER_PROFILE", 1),
            position_reconciliation_mode=_env_str(
                "PAPER_POSITION_RECONCILIATION_MODE", "local_only"),
            max_quote_age_seconds=_env_float("PAPER_MAX_QUOTE_AGE_SECONDS", 120.0),
            max_missing_quote_marks=_env_int("PAPER_MAX_MISSING_QUOTE_MARKS", 3),
            regime_exit_enabled=_env_bool("PAPER_REGIME_EXIT_ENABLED", True),
            slippage_points=_env_float("PAPER_SLIPPAGE_POINTS", 0.0),
        )

    @classmethod
    def from_env_and_overrides(cls, overrides: dict[str, Any] | None = None) -> PaperLifecycleConfig:
        cfg = cls.from_env()
        for k, v in (overrides or {}).items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg

    def tp_rule_str(self) -> str:
        return f"take_profit@{self.take_profit_pct:g}x_credit"

    def sl_rule_str(self) -> str:
        return f"stop_loss@{self.stop_loss_pct:g}x_credit"

    def exit_rule_str(self) -> str:
        eod = f"eod>={self.eod_exit_time}" if self.exit_on_eod else "eod_off"
        return f"{self.tp_rule_str()};{self.sl_rule_str()};{eod}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
