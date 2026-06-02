"""Vertical Wing v1 — orchestrates candidate generation, scoring, selection.

Phase 1.5 contract: takes BOTH a `StructureSnapshot` (structure levels) and
an `OptionChainSnapshot` (per-strike quotes). No reach-through into either
provider's internals.
"""

from __future__ import annotations

import os
from typing import Any

from src.providers.quotes.types import OptionChainSnapshot
from src.providers.structure.types import StructureSnapshot
from src.strategies.base import Candidate, StrategyDecision, weak_components_of
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

    def required_quote_strikes(
        self,
        structure,                              # type: ignore[no-untyped-def]
        params: dict[str, Any],
    ) -> list[float]:
        """Anchor strikes + long-leg partners VW will look up in the chain."""
        merged = {**self.default_parameters, **(params or {})}
        threshold = float(merged.get("volume_threshold", 2000))
        width = float(merged.get("spread_width", 5))
        e = structure.exposures
        # Pick 2K or 5K tier per the strategy's threshold (mirrors
        # `_ceiling_for_threshold` / `_floor_for_threshold` in candidates.py).
        if threshold >= 5000 and e.put_ceiling_5k is not None:
            put_ceiling = e.put_ceiling_5k
        else:
            put_ceiling = e.put_ceiling_2k
        if threshold >= 5000 and e.call_floor_5k is not None:
            call_floor = e.call_floor_5k
        else:
            call_floor = e.call_floor_2k

        strikes: list[float] = []
        if put_ceiling is not None:
            strikes.append(float(put_ceiling))
            strikes.append(float(put_ceiling) + width)   # CALL_CREDIT long leg
        if call_floor is not None:
            strikes.append(float(call_floor))
            strikes.append(float(call_floor) - width)    # PUT_CREDIT long leg
        return strikes

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

        # Phase 4.1 — observability-only score-edge threshold. Read once.
        # Phase 5 will gate selection on this; here we only stamp the fields.
        try:
            min_score_edge = float(os.getenv("MIN_SCORE_EDGE", "0.02"))
        except (TypeError, ValueError):
            min_score_edge = 0.02

        # ── annotate EVERY candidate with observability fields ──
        # This runs before the no-survivors / below-threshold branches so
        # the decision log carries weak_components / score_threshold /
        # score_gap_to_threshold for filter-rejected ones too.
        for c in candidates:
            c.score_threshold = threshold
            c.score_gap_to_threshold = threshold - c.score
            c.weak_components = weak_components_of(c.score_breakdown, n=2)
            # Mirror the threshold into the breakdown dict for CSV/JSONL.
            c.score_breakdown["no_trade_threshold"] = threshold
            c.score_breakdown["score_gap_to_threshold"] = c.score_gap_to_threshold
            # Phase 4.1 — score-edge observability (no decision impact)
            edge = float(c.score) - threshold
            c.score_edge = edge
            c.score_edge_passed = edge >= min_score_edge
            c.marginal_score = (c.score >= threshold) and (edge < min_score_edge)
            if c.rejected:
                c.rejection_type = "filter_rejected"
            # other branches set "selected" / "score_below_threshold" below

        active = [c for c in candidates if not c.rejected]
        if not active:
            reasons = [r for c in candidates for r in c.rejection_reasons]
            return StrategyDecision(
                strategy_id=self.id,
                decision="NO_TRADE",
                selected=None,
                all_candidates=candidates,
                explanation=(
                    f"NO_TRADE — all {len(candidates)} candidate(s) rejected by "
                    f"hard filters. Reasons: {reasons or '[]'}"
                ),
                rejection_reasons=reasons,
                threshold_used=threshold,
                rejection_type="filter_rejected",
                best_score=None,
                weak_components=[],
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
            gap = threshold - best.score
            weak = weak_components_of(best.score_breakdown, n=2)
            # Mark every active non-winner as score_below_threshold for the log
            for c in active:
                c.rejection_type = "score_below_threshold"
            return StrategyDecision(
                strategy_id=self.id,
                decision="NO_TRADE",
                selected=None,
                all_candidates=candidates,
                explanation=(
                    f"NO_TRADE — best candidate {best.side} "
                    f"{best.short_strike}/{best.long_strike} @ {best.credit:.2f} "
                    f"scored {best.score:.4f}, below threshold {threshold:.2f} "
                    f"by {gap:.4f}. Weakest components: "
                    f"{', '.join(weak) if weak else '[]'}."
                ),
                threshold_used=threshold,
                rejection_type="score_below_threshold",
                best_score=best.score,
                weak_components=weak,
            )

        # selected — mark the rest as score_below_threshold (they cleared
        # filters but lost the side_priority/score sort)
        best.rejection_type = "selected"
        for c in active:
            if c is not best and c.rejection_type is None:
                c.rejection_type = "score_below_threshold"

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
            threshold_used=threshold,
            rejection_type="selected",
            best_score=best.score,
            weak_components=weak_components_of(best.score_breakdown, n=2),
        )

    def explain(self, decision: StrategyDecision) -> str:
        return decision.explanation
