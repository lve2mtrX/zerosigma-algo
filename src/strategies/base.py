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
RejectionType = Literal[
    "selected",
    "score_below_threshold",
    "filter_rejected",
    "no_candidates",
    "missing_quotes",
    "missing_structure",
]

# Keys in `score_breakdown` that are META values, not weighted components.
# `weak_components_of` skips these when ranking the weakest contributors.
SCORE_META_KEYS: frozenset[str] = frozenset({
    "final_score",
    "no_trade_threshold",
    "score_gap_to_threshold",
})


def weak_components_of(
    breakdown: dict[str, float] | None,
    *,
    n: int = 2,
    exclude: frozenset[str] = SCORE_META_KEYS,
) -> list[str]:
    """Return the `n` weakest scoring components from a breakdown dict.

    Output items are "<key>=<value:.2f>" strings, ascending by value. Meta
    keys (final_score / no_trade_threshold / score_gap_to_threshold) are
    skipped — they're descriptive, not weighted inputs.

    Example: ['maxvol_alignment=0.00', 'credit_size=0.43']
    """
    if not breakdown:
        return []
    items = [
        (k, float(v))
        for k, v in breakdown.items()
        if k not in exclude and v is not None and isinstance(v, (int, float))
    ]
    items.sort(key=lambda kv: kv[1])
    return [f"{k}={v:.2f}" for k, v in items[: max(0, int(n))]]


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

    # filled in by select() — observability fields
    score_threshold: float | None = None
    score_gap_to_threshold: float | None = None
    weak_components: list[str] = field(default_factory=list)
    rejection_type: RejectionType | None = None

    # Phase 4.1 — score-edge observability (no decision impact)
    #   score_edge        = score - threshold              (signed)
    #   score_edge_passed = score_edge >= MIN_SCORE_EDGE   (Phase 5 will use)
    #   marginal_score    = score >= threshold AND score_edge < MIN_SCORE_EDGE
    #
    # Phase 5 will widen `RejectionType` to include a "marginal_edge" literal;
    # for now, these stay as additive Candidate fields and the decision branch
    # is UNTOUCHED — observability only.
    score_edge: float | None = None
    score_edge_passed: bool | None = None
    marginal_score: bool | None = None

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
    # observability: surfaced from select() so the decision log shows
    # exactly which threshold + best candidate produced the outcome.
    threshold_used: float | None = None
    rejection_type: RejectionType | None = None
    best_score: float | None = None
    weak_components: list[str] = field(default_factory=list)


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
