"""Pure, deterministic operator prompt templates adapted from Stone/Pete."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptTemplate:
    title: str
    message: str
    operator_message: str


@dataclass(frozen=True)
class RenderedPrompt:
    key: str
    title: str
    message: str
    used_fallback: bool = False


PROMPT_TEMPLATES: dict[str, PromptTemplate] = {
    "REGIME_CHANGED": PromptTemplate(
        title="Regime changed",
        message="{symbol} moved from {old_regime} to {new_regime}. {detail}",
        operator_message=(
            "Hey idiot, come check this out: {symbol} changed from {old_regime} "
            "to {new_regime}. {detail}"
        ),
    ),
    "GAMMA_FLIP_AGAINST": PromptTemplate(
        title="Gamma flipped against the paper trade",
        message="{symbol} crossed the gamma flip against {trade_id}. {detail}",
        operator_message=(
            "Heads up: gamma flipped against paper trade {trade_id} on {symbol}. {detail}"
        ),
    ),
    "MAXVOL_MIGRATED": PromptTemplate(
        title="MaxVol migrated",
        message="{symbol} MaxVol moved from {old_level} to {new_level}. {detail}",
        operator_message=(
            "Come look at {symbol}: MaxVol migrated from {old_level} to {new_level}. {detail}"
        ),
    ),
    "CORRIDOR_BROKE": PromptTemplate(
        title="Structure corridor broke",
        message="{symbol} is outside the active structure corridor. {detail}",
        operator_message="The {symbol} corridor broke. This needs a human look. {detail}",
    ),
    "WDS_WEAKENED": PromptTemplate(
        title="Wing structure weakened",
        message="{symbol} WDS weakened to {wds_tier}. {detail}",
        operator_message="{symbol} wing structure is weakening at {wds_tier}. {detail}",
    ),
    "PAPER_TP_HIT": PromptTemplate(
        title="Paper take profit hit",
        message="Paper trade {trade_id} reached its take-profit rule. {detail}",
        operator_message="Paper TP hit on {trade_id}. The local ledger recorded the exit. {detail}",
    ),
    "PAPER_SL_HIT": PromptTemplate(
        title="Paper stop hit",
        message="Paper trade {trade_id} reached its stop-loss rule. {detail}",
        operator_message="Paper stop hit on {trade_id}. Check the thesis and mark quality. {detail}",
    ),
    "PAPER_EXIT_REGIME": PromptTemplate(
        title="Paper regime exit",
        message="Paper trade {trade_id} exited after a regime rule fired. {detail}",
        operator_message="Regime exit recorded for paper trade {trade_id}. {detail}",
    ),
    "CANDIDATE_REJECTED_RISK": PromptTemplate(
        title="Candidate rejected on risk quality",
        message="{symbol} candidate {profile_id} was rejected. {detail}",
        operator_message=(
            "Candidate rejected because the risk/reward is trash: {symbol} "
            "{profile_id}. {detail}"
        ),
    ),
    "QUOTE_QUALITY_BAD": PromptTemplate(
        title="Quote quality is not usable",
        message="{symbol} quote quality failed validation. {detail}",
        operator_message="Do not trust this {symbol} mark. Quote quality is bad. {detail}",
    ),
    "DA_GEX_PATH_CHANGED": PromptTemplate(
        title="Daily DA-GEX path changed",
        message="{symbol} daily path is now {daily_regime}. {detail}",
        operator_message="Come check {symbol}: DA-GEX path changed to {daily_regime}. {detail}",
    ),
    "OPEX_CONTEXT_CHANGED": PromptTemplate(
        title="Expiration context changed",
        message="{symbol} context is now {context_regime}. {detail}",
        operator_message="{symbol} moved into {context_regime} context. {detail}",
    ),
    "GREEK_DATA_DEGRADED": PromptTemplate(
        title="Greek data degraded",
        message="{symbol} Greek availability changed. {detail}",
        operator_message="Do not trust the full structure read on {symbol}: Greek data degraded. {detail}",
    ),
}


class _SafeContext(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "unavailable"


def render_prompt(
    template_key: str,
    *,
    operator_style: bool = False,
    **context: Any,
) -> RenderedPrompt:
    """Render a known prompt or a deterministic fallback without side effects."""
    normalized = str(template_key or "UNKNOWN").strip().upper()
    template = PROMPT_TEMPLATES.get(normalized)
    values = _SafeContext(context)
    if template is None:
        source = str(context.get("source") or "system event").replace("_", " ").lower()
        detail = str(context.get("detail") or "Review the local alert journal.")
        return RenderedPrompt(
            key=normalized,
            title="Alert needs review",
            message=f"Unknown {source} transition. {detail}",
            used_fallback=True,
        )
    message_template = template.operator_message if operator_style else template.message
    return RenderedPrompt(
        key=normalized,
        title=template.title.format_map(values),
        message=message_template.format_map(values),
    )
