"""Vertical Wing v1 — orchestrates candidate generation, scoring, selection."""

from __future__ import annotations

from typing import Any

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
            "spot",
            "chain.strikes",
            "chain.c_volume", "chain.p_volume",
            "chain.c_bid", "chain.c_ask",
            "chain.p_bid", "chain.p_ask",
        ]

    def generate_candidates(
        self,
        snapshot: StructureSnapshot,
        params: dict[str, Any],
    ) -> list[Candidate]:
        merged = {**self.default_parameters, **(params or {})}
        threshold = float(merged.get("volume_threshold", 2000))
        width = float(merged.get("spread_width", 5))

        out: list[Candidate] = []
        cc = build_put_ceiling_call_credit(snapshot, threshold, width, self.id)
        if cc is not None:
            out.append(cc)
        pc = build_call_floor_put_credit(snapshot, threshold, width, self.id)
        if pc is not None:
            out.append(pc)
        return out

    def score(
        self,
        candidate: Candidate,
        snapshot: StructureSnapshot,
        params: dict[str, Any],
    ) -> float:
        merged = {**self.default_parameters, **(params or {})}
        total, breakdown = score_candidate(candidate, snapshot, merged)
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
