"""Vertical Wing v1 — orchestrates candidate generation, scoring, selection.

Phase 1.5 contract: takes BOTH a `StructureSnapshot` (structure levels) and
an `OptionChainSnapshot` (per-strike quotes). No reach-through into either
provider's internals.
"""

from __future__ import annotations

from typing import Any

from src.providers.quotes.types import OptionChainSnapshot
from src.providers.structure.types import StructureSnapshot
from src.strategies.base import Candidate, StrategyDecision
from src.strategies.vertical_wing.candidates import (
    build_call_floor_put_credit,
    build_put_ceiling_call_credit,
)
from src.strategies.vertical_wing.scoring import score_candidate


class VerticalWingV1:
    def __init__(
        self,
        strategy_id: str = "vertical_wing_v1",
        display_name: str = "Vertical Wing v1 (SPX 0DTE)",
        symbol: str = "SPX",
        default_parameters: dict[str, Any] | None = None,
        **_: object,
    ) -> None:
        self.id = strategy_id
        self.display_name = display_name
        self.symbol = symbol
        self.default_parameters: dict[str, Any] = dict(default_parameters or {})

    def required_data_fields(self) -> list[str]:
        return [
            "structure.exposures.put_ceiling_2k",
            "structure.exposures.put_ceiling_5k",
            "structure.exposures.call_floor_2k",
            "structure.exposures.call_floor_5k",
            "structure.exposures.maxvol",
            "structure.exposures.gamma_regime",
            "chain.quotes",   # bid/ask/mid + volume at the relevant strikes
        ]

    def generate_candidates(
        self,
        structure: StructureSnapshot,
        chain: OptionChainSnapshot,
        params: dict[str, Any],
    ) -> list[Candidate]:
        merged = {**self.default_parameters, **(params or {})}
        threshold = float(merged.get("volume_threshold", 2000))
        width = float(merged.get("spread_width", 5))
        max_ba = float(merged.get("max_bid_ask_width", 0.20))

        out: list[Candidate] = []
        cc = build_put_ceiling_call_credit(
            structure, chain, threshold, width, self.id, max_bid_ask_width=max_ba,
        )
        if cc is not None:
            out.append(cc)
        pc = build_call_floor_put_credit(
            structure, chain, threshold, width, self.id, max_bid_ask_width=max_ba,
        )
        if pc is not None:
            out.append(pc)
        return out

    def score(
        self,
        candidate: Candidate,
        structure: StructureSnapshot,
        chain: OptionChainSnapshot,
        params: dict[str, Any],
    ) -> float:
        merged = {**self.default_parameters, **(params or {})}
        total, breakdown = score_candidate(candidate, structure, chain, merged)
        candidate.score = total
        candidate.score_breakdown = breakdown
        return total

    def select(
        self,
        candidates: list[Candidate],
        params: dict[str, Any],
    ) -> StrategyDecision:
        merged = {**self.default_parameters, **(params or {})}
        threshold = float(merged.get("no_trade_score_threshold", 0.60))
        side_priority = merged.get("side_priority", ["CALL_CREDIT", "PUT_CREDIT"])

        active = [c for c in candidates if not c.rejected]
        if not active:
            return StrategyDecision(
                strategy_id=self.id,
                decision="NO_TRADE",
                selected=None,
                all_candidates=candidates,
                explanation="No surviving candidates (all rejected by filters).",
                rejection_reasons=[r for c in candidates for r in c.rejection_reasons],
            )

        def sort_key(c: Candidate) -> tuple[float, int]:
            try:
                pri = side_priority.index(c.side)
            except ValueError:
                pri = len(side_priority)
            return (-c.score, pri)

        active.sort(key=sort_key)
        best = active[0]

        if best.score < threshold:
            return StrategyDecision(
                strategy_id=self.id,
                decision="NO_TRADE",
                selected=None,
                all_candidates=candidates,
                explanation=(
                    f"Best score {best.score:.2f} below no_trade_score_threshold "
                    f"{threshold:.2f}."
                ),
            )

        decision: str = "TRADE_CALL_CREDIT" if best.side == "CALL_CREDIT" else "TRADE_PUT_CREDIT"
        return StrategyDecision(
            strategy_id=self.id,
            decision=decision,  # type: ignore[arg-type]
            selected=best,
            all_candidates=candidates,
            explanation=(
                f"Selected {best.side} K={best.short_strike}/{best.long_strike} "
                f"credit={best.credit:.2f} score={best.score:.2f}"
            ),
        )

    def explain(self, decision: StrategyDecision) -> str:
        return decision.explanation
