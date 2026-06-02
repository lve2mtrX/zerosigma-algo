"""Phase 9B — local paper-trade lifecycle data models.

LOCAL PAPER ACCOUNTING ONLY. These dataclasses describe a *simulated* credit
spread and the lifecycle configuration that governs it. Nothing here places,
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
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

# Audit markers so a grep can prove there is no execution surface here.
NO_EXECUTION = True
EXECUTION_MODE = "local_paper_lifecycle_only"

Side = Literal["CALL_CREDIT", "PUT_CREDIT"]
TradeStatus = Literal["open", "closed", "duplicate_skipped", "error"]
ExitReason = Literal[
    "take_profit",
    "stop_loss",
    "eod_exit",
    "manual_mark_closed",
    "quote_unavailable_no_exit",
    "error",
]
EventType = Literal["open", "update", "close", "duplicate_skipped", "blocked_by_limits"]


@dataclass
class PaperTrade:
    """One simulated credit spread and its running lifecycle state.

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
_INT_FIELDS = frozenset({"target_dte", "contracts", "ticks_held"})
_FLOAT_FIELDS = frozenset({
    "short_strike", "long_strike", "spread_width", "entry_credit", "entry_bid",
    "entry_ask", "entry_mid", "current_mark", "current_bid", "current_ask",
    "unrealized_pnl", "realized_pnl", "max_profit", "max_loss",
    "planned_stop_risk_dollars", "theoretical_max_loss_dollars",
    "exit_credit_or_debit", "mae", "mfe",
})


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
    return value


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
