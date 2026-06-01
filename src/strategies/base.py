"""Strategy interface + shared dataclasses.

Every registered strategy implements `Strategy`. Framework calls in order:
    generate_candidates → (risk filter pass) → score → select → log/execute
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from src.providers.quotes.types import OptionChainSnapshot
from src.providers.structure.types import StructureSnapshot

Decision = Literal["TRADE_CALL_CREDIT", "TRADE_PUT_CREDIT", "NO_TRADE"]
Side = Literal["CALL_CREDIT", "PUT_CREDIT"]


@dataclass
class Candidate:
    """A proposed vertical (or whatever leg-shape future strategies emit)."""
    strategy_id: str
    side: Side
    symbol: str
    expiry: str
    short_strike: float
    long_strike: float
    credit: float
    max_risk: float
    reward_risk: float
    breakeven: float
    distance_from_spot: float

    # filled in by scoring stage
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)

    # filled in by hard filters / risk
    rejected: bool = False
    rejection_reasons: list[str] = field(default_factory=list)

    # arbitrary strategy-specific context
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyDecision:
    strategy_id: str
    decision: Decision
    selected: Candidate | None
    all_candidates: list[Candidate]
    explanation: str
    rejection_reasons: list[str] = field(default_factory=list)


@runtime_checkable
class Strategy(Protocol):
    """A registered strategy.

    Framework dataflow:
        generate_candidates(structure, chain, params)
                 → (risk filters)
                 → score(c, structure, chain, params)
                 → select(candidates, params)
                 → log / execute
    """

    id: str
    display_name: str

    def required_data_fields(self) -> list[str]: ...

    def required_quote_strikes(
        self,
        structure: StructureSnapshot,
        params: dict[str, Any],
    ) -> list[float]:
        """Strikes the strategy needs quoted in the chain given this structure.

        Used by the scanner to build a `QuoteRequest` so synthesis-based
        QuoteProviders (the Phase 1.5 mock) can align their generated chain
        with the structure's anchor levels. Real broker providers ignore
        this hint — they have authoritative chain data.

        Default contract: return an empty list (the scanner will skip the
        hint). VW v1 returns the ceiling/floor anchors plus their long-leg
        partners per spread_width.
        """
        ...

    def generate_candidates(
        self,
        structure: StructureSnapshot,
        chain: OptionChainSnapshot,
        params: dict[str, Any],
    ) -> list[Candidate]: ...

    def score(
        self,
        candidate: Candidate,
        structure: StructureSnapshot,
        chain: OptionChainSnapshot,
        params: dict[str, Any],
    ) -> float: ...

    def select(
        self,
        candidates: list[Candidate],
        params: dict[str, Any],
    ) -> StrategyDecision: ...

    def explain(self, decision: StrategyDecision) -> str: ...
