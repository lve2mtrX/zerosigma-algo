"""Adapters from Phase 11D regime/paper events into local alerts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.alerts.templates import render_prompt
from src.alerts.types import (
    AlertAction,
    AlertEvent,
    AlertSeverity,
    AlertSource,
    deterministic_event_id,
)
from src.paper.models import ExecutionJournalEvent
from src.regime.types import RegimeChangeEvent, RegimeSeverity


def _contains(values: tuple[str, ...], *needles: str) -> bool:
    combined = " ".join(values).lower()
    return any(needle.lower() in combined for needle in needles)


def _regime_template(event: RegimeChangeEvent) -> str:
    signals = (*event.reason_codes, event.trigger)
    if _contains(signals, "gamma_flip", "gamma flip"):
        return "GAMMA_FLIP_AGAINST"
    if _contains(signals, "maxvol", "max_vol"):
        return "MAXVOL_MIGRATED"
    if _contains(signals, "corridor"):
        return "CORRIDOR_BROKE"
    if _contains(signals, "wds", "wing"):
        return "WDS_WEAKENED"
    if _contains(signals, "daily_da_gex", "whipsaw"):
        return "DA_GEX_PATH_CHANGED"
    if _contains(signals, "opex_context"):
        return "OPEX_CONTEXT_CHANGED"
    if _contains(signals, "greek_data", "greek_api_field"):
        return "GREEK_DATA_DEGRADED"
    return "REGIME_CHANGED"


def regime_change_to_alert(
    event: RegimeChangeEvent,
    *,
    profile_id: str | None = None,
    trade_id: str | None = None,
    operator_style: bool = False,
) -> AlertEvent:
    severity = {
        RegimeSeverity.INFO: AlertSeverity.INFO,
        RegimeSeverity.WARN: AlertSeverity.WARNING,
        RegimeSeverity.CRITICAL: AlertSeverity.CRITICAL,
    }[event.severity]
    old_regime = event.old_regime.value
    new_regime = event.new_regime.value
    prompt = render_prompt(
        _regime_template(event),
        operator_style=operator_style,
        symbol=event.symbol,
        old_regime=old_regime.replace("_", " ").title(),
        new_regime=new_regime.replace("_", " ").title(),
        trade_id=trade_id,
        old_level=event.levels_involved.get("old_maxvol"),
        new_level=event.levels_involved.get("new_maxvol"),
        wds_tier=event.levels_involved.get("wds_tier"),
        daily_regime=event.levels_involved.get("daily_regime"),
        context_regime=event.levels_involved.get("context_regime"),
        detail=event.plain_english_alert,
    )
    reasons = event.reason_codes or ("regime_changed",)
    return AlertEvent(
        event_id=deterministic_event_id(
            event.timestamp, event.symbol, old_regime, new_regime, event.trigger, reasons
        ),
        timestamp=event.timestamp,
        source=AlertSource.REGIME_CHANGE,
        severity=severity,
        title=prompt.title,
        message=prompt.message,
        symbol=event.symbol,
        profile_id=profile_id,
        trade_id=trade_id,
        regime_label=new_regime,
        old_regime=old_regime,
        new_regime=new_regime,
        suggested_action=event.suggested_action.value,
        reason_codes=reasons,
        metadata={
            "trigger": event.trigger,
            "levels_involved": event.levels_involved,
            "affects_open_positions": event.affects_open_positions,
            "template_key": prompt.key,
        },
    )


def _paper_event_dict(event: ExecutionJournalEvent | Mapping[str, Any]) -> dict[str, Any]:
    return event.to_dict() if isinstance(event, ExecutionJournalEvent) else dict(event)


def _paper_template(action: str, reasons: tuple[str, ...]) -> str | None:
    if action == "CANDIDATE_REJECTED":
        return "QUOTE_QUALITY_BAD" if _contains(reasons, "quote") else "CANDIDATE_REJECTED_RISK"
    if action != "EXITED" and _contains(reasons, "gamma_flip", "gamma flip"):
        return "GAMMA_FLIP_AGAINST"
    if action != "EXITED" and _contains(reasons, "maxvol", "max_vol"):
        return "MAXVOL_MIGRATED"
    if action != "EXITED" and _contains(reasons, "corridor"):
        return "CORRIDOR_BROKE"
    if action != "EXITED" and _contains(reasons, "wds", "wing"):
        return "WDS_WEAKENED"
    if action == "EXITED":
        if _contains(reasons, "take_profit", "tp_hit", "profit_target"):
            return "PAPER_TP_HIT"
        if _contains(reasons, "stop_loss", "sl_hit", "stop_hit"):
            return "PAPER_SL_HIT"
        if _contains(reasons, "regime"):
            return "PAPER_EXIT_REGIME"
    return None


def _paper_classification(
    action: str, reasons: tuple[str, ...]
) -> tuple[AlertSource, AlertSeverity, AlertAction, str, str | None]:
    template = _paper_template(action, reasons)
    if action == "ENTERED":
        return AlertSource.PAPER_ENTRY, AlertSeverity.INFO, AlertAction.COCKPIT, "Paper entry", template
    if action in {"MARKED", "HELD"}:
        return AlertSource.PAPER_MARK, AlertSeverity.INFO, AlertAction.LOG_ONLY, "Paper position update", template
    if action == "ALERT":
        return AlertSource.PAPER_MARK, AlertSeverity.WATCH, AlertAction.ALL, "Paper thesis alert", template
    if action == "EXITED":
        severity = AlertSeverity.CRITICAL if template == "PAPER_SL_HIT" else AlertSeverity.WARNING
        if template == "PAPER_TP_HIT":
            severity = AlertSeverity.INFO
        return AlertSource.PAPER_EXIT, severity, AlertAction.ALL, "Paper position exited", template
    if action == "CANDIDATE_REJECTED":
        source = AlertSource.RISK_QUALITY if not _contains(reasons, "quote") else AlertSource.CANDIDATE_REJECTED
        return source, AlertSeverity.WARNING, AlertAction.COCKPIT, "Candidate rejected", template
    return AlertSource.SYSTEM, AlertSeverity.INFO, AlertAction.LOG_ONLY, "Paper journal event", template


def paper_journal_to_alert(
    event: ExecutionJournalEvent | Mapping[str, Any],
    *,
    symbol: str | None = None,
    operator_style: bool = False,
) -> AlertEvent:
    row = _paper_event_dict(event)
    action = str(row.get("action") or "UNKNOWN").upper()
    reasons = tuple(str(value) for value in (row.get("reason_codes") or ()))
    reasons = reasons or (f"paper_{action.lower()}",)
    source, severity, delivery_action, default_title, template_key = _paper_classification(
        action, reasons
    )
    trade_id = str(row.get("paper_trade_id") or "") or None
    profile_id = str(row.get("profile_id") or "") or None
    detail = str(row.get("plain_english_explanation") or "Review the local paper journal.")
    if template_key:
        prompt = render_prompt(
            template_key,
            operator_style=operator_style,
            symbol=symbol,
            profile_id=profile_id,
            trade_id=trade_id,
            detail=detail,
        )
        title, message = prompt.title, prompt.message
    else:
        title, message = default_title, detail
    timestamp = str(row.get("timestamp") or "")
    return AlertEvent(
        event_id=deterministic_event_id(
            timestamp, action, symbol, profile_id, trade_id, reasons, row.get("pnl_impact")
        ),
        timestamp=timestamp,
        source=source,
        severity=severity,
        title=title,
        message=message,
        symbol=symbol,
        profile_id=profile_id,
        trade_id=trade_id,
        regime_label=None,
        old_regime=None,
        new_regime=None,
        suggested_action=action,
        reason_codes=reasons,
        metadata={
            "paper_action": action,
            "regime_snapshot_summary": row.get("regime_snapshot_summary"),
            "risk_quality_summary": row.get("risk_quality_summary"),
            "pnl_impact": row.get("pnl_impact"),
            "details": row.get("details") or {},
        },
        local_only=bool(row.get("local_paper_only", True)),
        no_broker_order_sent=bool(row.get("no_broker_order_sent", True)),
        delivery_action=delivery_action,
    )
