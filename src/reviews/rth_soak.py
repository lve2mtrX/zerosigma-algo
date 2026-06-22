"""Read-only summaries for RTH local-paper soak artifacts.

This module consumes existing journals and CSV outputs. It never fetches market
data, routes alerts, evaluates exits, previews orders, or touches a brokerage.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.alerts.journal import AlertJournal
from src.paper import ledger

REPO_ROOT = Path(__file__).resolve().parents[2]

ALERT_FIELDS = (
    "timestamp", "event_id", "source", "severity", "symbol", "profile_id",
    "trade_id", "suppressed", "suppression_reason", "reason_codes",
    "delivery_attempts", "deliveries", "delivery_reasons",
)
REGIME_FIELDS = (
    "timestamp", "symbol", "old_regime", "new_regime", "old_daily_regime",
    "daily_regime", "old_context_regime", "context_regime", "trigger",
    "severity", "affects_open_positions", "da_gex_sign_changes",
    "maxvol_migration", "newly_missing_greek_fields",
    "newly_available_greek_fields", "nearby_paper_action", "reason_codes",
)
PAPER_FIELDS = (
    "paper_trade_id", "profile_id", "symbol", "side", "status", "opened_at",
    "closed_at", "entry_credit", "entry_debit", "current_mark", "realized_pnl",
    "exit_reason", "exit_category", "entered_events", "held_events",
    "marked_events", "exit_events", "entry_regime", "current_regime",
)
GREEK_FIELDS = (
    "timestamp", "source", "paper_trade_id", "daily_regime", "context_regime",
    "status", "available_count", "missing_count", "available_fields",
    "missing_fields", "appeared_fields", "disappeared_fields",
)


def _list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    separator = ";" if ";" in text else ","
    return [item.strip() for item in text.split(separator) if item.strip()]


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def summarize_alerts(
    events: Iterable[dict[str, Any]],
    deliveries: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    event_rows = list(events)
    delivery_rows = list(deliveries)
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for delivery in delivery_rows:
        by_event[str(delivery.get("event_id") or "")].append(delivery)

    reason_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for event in event_rows:
        reasons = _list(event.get("reason_codes"))
        reason_counts.update(reasons)
        source_counts[str(event.get("source") or "UNKNOWN")] += 1
        severity_counts[str(event.get("severity") or "UNKNOWN")] += 1
        results = by_event.get(str(event.get("event_id") or ""), [])
        rows.append({
            "timestamp": event.get("timestamp"),
            "event_id": event.get("event_id"),
            "source": event.get("source"),
            "severity": event.get("severity"),
            "symbol": event.get("symbol"),
            "profile_id": event.get("profile_id"),
            "trade_id": event.get("trade_id"),
            "suppressed": _bool(event.get("suppressed")),
            "suppression_reason": event.get("suppression_reason"),
            "reason_codes": "; ".join(reasons),
            "delivery_attempts": sum(_bool(row.get("attempted")) for row in results),
            "deliveries": sum(_bool(row.get("delivered")) for row in results),
            "delivery_reasons": "; ".join(
                sorted({str(row.get("reason")) for row in results if row.get("reason")})
            ),
        })

    suppressed = sum(row["suppressed"] for row in rows)
    cooldown_suppressed = sum(
        row["suppressed"] and "cooldown" in str(row["suppression_reason"] or "").lower()
        for row in rows
    )
    total = len(rows)
    if total < 5:
        noise = "insufficient_alert_volume"
    elif suppressed / total >= 0.5:
        noise = "high_duplicate_or_cooldown_noise"
    else:
        noise = "no_excessive_cooldown_noise_detected"
    summary = {
        "alert_count": total,
        "suppressed_count": suppressed,
        "suppression_rate": round(suppressed / total, 4) if total else 0.0,
        "cooldown_duplicate_count": cooldown_suppressed,
        "delivery_attempt_count": sum(_bool(row.get("attempted")) for row in delivery_rows),
        "delivered_count": sum(_bool(row.get("delivered")) for row in delivery_rows),
        "alerts_linked_to_trades": sum(bool(row.get("trade_id")) for row in rows),
        "source_distribution": _counter_rows(source_counts),
        "severity_distribution": _counter_rows(severity_counts),
        "top_reason_codes": _counter_rows(reason_counts)[:10],
        "noise_assessment": noise,
    }
    return summary, rows


def _regime_snapshots(
    marks: Iterable[dict[str, Any]],
    trades: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for mark in marks:
        snapshot = _dict(mark.get("current_regime_snapshot"))
        if snapshot:
            snapshots.append({
                **snapshot,
                "_source": "paper_mark",
                "_paper_trade_id": mark.get("paper_trade_id"),
                "_timestamp": mark.get("timestamp") or snapshot.get("timestamp"),
            })
    for trade in trades:
        for key, source, timestamp_key in (
            ("entry_regime_json", "paper_entry", "opened_at"),
            ("current_regime_json", "paper_current", "closed_at"),
        ):
            snapshot = _dict(trade.get(key))
            if snapshot:
                snapshots.append({
                    **snapshot,
                    "_source": source,
                    "_paper_trade_id": trade.get("paper_trade_id"),
                    "_timestamp": trade.get(timestamp_key) or snapshot.get("timestamp"),
                })
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for snapshot in snapshots:
        key = (
            snapshot.get("_timestamp"), snapshot.get("_paper_trade_id"),
            snapshot.get("daily_regime_code"), snapshot.get("context_regime_code"),
            snapshot.get("da_gex_sign_changes"), snapshot.get("maxvol_strike"),
            tuple(_list(snapshot.get("greek_api_missing_fields"))),
        )
        unique[key] = snapshot
    return sorted(unique.values(), key=lambda row: str(row.get("_timestamp") or ""))


def _nearby_action(
    event_timestamp: Any,
    journal: Iterable[dict[str, Any]],
    *,
    window_seconds: int = 300,
) -> str | None:
    event_time = _timestamp(event_timestamp)
    if event_time is None:
        return None
    nearby: list[tuple[float, str]] = []
    for row in journal:
        action_time = _timestamp(row.get("timestamp"))
        if action_time is None:
            continue
        try:
            delta = abs((action_time - event_time).total_seconds())
        except TypeError:
            continue
        if delta <= window_seconds:
            nearby.append((delta, str(row.get("action") or "UNKNOWN")))
    return min(nearby)[1] if nearby else None


def summarize_regimes(
    regime_events: Iterable[dict[str, Any]],
    marks: Iterable[dict[str, Any]],
    trades: Iterable[dict[str, Any]],
    journal: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    events = list(regime_events)
    snapshots = _regime_snapshots(marks, trades)
    daily_counts: Counter[str] = Counter(
        str(row.get("daily_regime_code"))
        for row in snapshots if row.get("daily_regime_code")
    )
    context_counts: Counter[str] = Counter(
        str(row.get("context_regime_code"))
        for row in snapshots if row.get("context_regime_code")
    )
    rows: list[dict[str, Any]] = []
    for event in events:
        levels = _dict(event.get("levels_involved"))
        rows.append({
            "timestamp": event.get("timestamp"),
            "symbol": event.get("symbol"),
            "old_regime": event.get("old_regime"),
            "new_regime": event.get("new_regime"),
            "old_daily_regime": levels.get("old_daily_regime"),
            "daily_regime": levels.get("daily_regime"),
            "old_context_regime": levels.get("old_context_regime"),
            "context_regime": levels.get("context_regime"),
            "trigger": event.get("trigger"),
            "severity": event.get("severity"),
            "affects_open_positions": _bool(event.get("affects_open_positions")),
            "da_gex_sign_changes": levels.get("da_gex_sign_changes"),
            "maxvol_migration": levels.get("maxvol_migration"),
            "newly_missing_greek_fields": "; ".join(
                _list(levels.get("newly_missing_greek_fields"))
            ),
            "newly_available_greek_fields": "; ".join(
                _list(levels.get("newly_available_greek_fields"))
            ),
            "nearby_paper_action": _nearby_action(event.get("timestamp"), journal),
            "reason_codes": "; ".join(_list(event.get("reason_codes"))),
        })

    event_reasons = [reason for row in events for reason in _list(row.get("reason_codes"))]
    latest = snapshots[-1] if snapshots else {}
    sign_changes = [
        int(number) for number in (
            _float(row.get("da_gex_sign_changes")) for row in snapshots
        ) if number is not None
    ]
    summary = {
        "transition_count": len(events),
        "daily_regime_distribution": _counter_rows(daily_counts),
        "context_regime_distribution": _counter_rows(context_counts),
        "da_gex_sign_changes_latest": max(sign_changes) if sign_changes else 0,
        "r3_whipsaw_events": sum(
            row.get("daily_regime_code") == "R3_WHIPSAW" for row in snapshots
        ),
        "maxvol_migration_events": sum(
            "maxvol_migrated" in str(row.get("trigger") or "")
            or "maxvol_migrated_materially" in _list(row.get("reason_codes"))
            for row in events
        ),
        "greek_degradation_events": event_reasons.count("greek_api_field_disappeared"),
        "greek_recovery_events": event_reasons.count("greek_api_field_appeared"),
        "transitions_near_paper_actions": sum(bool(row["nearby_paper_action"]) for row in rows),
        "latest_daily_regime": latest.get("daily_regime_code"),
        "latest_context_regime": latest.get("context_regime_code"),
        "latest_core_regime": latest.get("final_regime_label"),
    }
    return summary, rows, snapshots


def summarize_greeks(
    snapshots: Iterable[dict[str, Any]],
    regime_events: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    missing_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for snapshot in snapshots:
        available = _list(snapshot.get("greek_api_available_fields"))
        missing = _list(snapshot.get("greek_api_missing_fields"))
        status = "Unavailable" if not available else "Degraded" if missing else "Available"
        status_counts[status] += 1
        missing_counts.update(missing)
        rows.append({
            "timestamp": snapshot.get("_timestamp") or snapshot.get("timestamp"),
            "source": snapshot.get("_source"),
            "paper_trade_id": snapshot.get("_paper_trade_id"),
            "daily_regime": snapshot.get("daily_regime_code"),
            "context_regime": snapshot.get("context_regime_code"),
            "status": status,
            "available_count": len(available),
            "missing_count": len(missing),
            "available_fields": "; ".join(available),
            "missing_fields": "; ".join(missing),
            "appeared_fields": "",
            "disappeared_fields": "",
        })
    for event in regime_events:
        levels = _dict(event.get("levels_involved"))
        appeared = _list(levels.get("newly_available_greek_fields"))
        disappeared = _list(levels.get("newly_missing_greek_fields"))
        if not appeared and not disappeared:
            continue
        missing_counts.update(disappeared)
        rows.append({
            "timestamp": event.get("timestamp"),
            "source": "regime_event",
            "paper_trade_id": None,
            "daily_regime": levels.get("daily_regime"),
            "context_regime": levels.get("context_regime"),
            "status": "Degraded" if disappeared else "Recovered",
            "available_count": None,
            "missing_count": len(disappeared),
            "available_fields": "",
            "missing_fields": "; ".join(disappeared),
            "appeared_fields": "; ".join(appeared),
            "disappeared_fields": "; ".join(disappeared),
        })
    latest_status = rows[-1]["status"] if rows else "No data"
    summary = {
        "observation_count": len(rows),
        "latest_status": latest_status,
        "status_distribution": _counter_rows(status_counts),
        "missing_field_counts": _counter_rows(missing_counts),
        "degraded_observations": sum(row["status"] == "Degraded" for row in rows),
    }
    return summary, rows


def _exit_category(reason: Any) -> str:
    value = str(reason or "").lower()
    if "take_profit" in value or value == "tp":
        return "TP"
    if "stop_loss" in value or value == "sl":
        return "SL"
    if "eod" in value:
        return "EOD"
    if "regime" in value or "thesis" in value:
        return "REGIME"
    if any(token in value for token in ("quote", "stale", "wide", "invalid")):
        return "QUOTE"
    return "OPEN" if not value else "OTHER"


def _quote_issue_count(
    journal: Iterable[dict[str, Any]],
    candidates: Iterable[dict[str, Any]],
) -> tuple[int, Counter[str]]:
    counts: Counter[str] = Counter()
    for row in [*journal, *candidates]:
        text = " ".join(
            [
                str(row.get("quote_quality_bucket") or ""),
                str(row.get("quote_validation_reason") or ""),
                str(row.get("quote_rejection_reason") or ""),
                " ".join(_list(row.get("reason_codes"))),
            ]
        ).lower()
        for label, tokens in {
            "stale": ("stale",),
            "wide": ("wide", "spread_abs", "spread_pct"),
            "invalid": ("invalid", "crossed", "zero_bid", "missing_bid"),
        }.items():
            if any(token in text for token in tokens):
                counts[label] += 1
                break
    return sum(counts.values()), counts


def summarize_paper_trades(
    open_trades: Iterable[dict[str, Any]],
    closed_trades: Iterable[dict[str, Any]],
    journal: Iterable[dict[str, Any]],
    candidates: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    open_rows, closed_rows, journal_rows = list(open_trades), list(closed_trades), list(journal)
    by_trade: dict[str, Counter[str]] = defaultdict(Counter)
    for event in journal_rows:
        trade_id = str(event.get("paper_trade_id") or "")
        if trade_id:
            by_trade[trade_id][str(event.get("action") or "UNKNOWN").upper()] += 1
    rows: list[dict[str, Any]] = []
    for trade in [*open_rows, *closed_rows]:
        trade_id = str(trade.get("paper_trade_id") or "")
        actions = by_trade.get(trade_id, Counter())
        entry_regime = _dict(trade.get("entry_regime_json"))
        current_regime = _dict(trade.get("current_regime_json"))
        rows.append({
            "paper_trade_id": trade_id,
            "profile_id": trade.get("profile_id"),
            "symbol": trade.get("symbol"),
            "side": trade.get("side"),
            "status": trade.get("status") or ("closed" if trade in closed_rows else "open"),
            "opened_at": trade.get("opened_at"),
            "closed_at": trade.get("closed_at"),
            "entry_credit": trade.get("entry_credit"),
            "entry_debit": trade.get("entry_debit"),
            "current_mark": trade.get("current_mark"),
            "realized_pnl": trade.get("realized_pnl"),
            "exit_reason": trade.get("exit_reason"),
            "exit_category": _exit_category(trade.get("exit_reason")),
            "entered_events": actions["ENTERED"],
            "held_events": actions["HELD"],
            "marked_events": actions["MARKED"],
            "exit_events": actions["EXITED"],
            "entry_regime": entry_regime.get("final_regime_label"),
            "current_regime": current_regime.get("final_regime_label"),
        })

    actions = Counter(str(row.get("action") or "UNKNOWN").upper() for row in journal_rows)
    exit_counts = Counter(row["exit_category"] for row in rows if row["exit_category"] != "OPEN")
    regime_rows = [row for row in rows if row["exit_category"] == "REGIME"]
    helped = sum((_float(row.get("realized_pnl")) or 0.0) > 0 for row in regime_rows)
    hurt = sum((_float(row.get("realized_pnl")) or 0.0) < 0 for row in regime_rows)
    quote_issues, quote_distribution = _quote_issue_count(journal_rows, candidates)
    summary = {
        "paper_trade_count": len(rows),
        "open_trade_count": len(open_rows),
        "closed_trade_count": len(closed_rows),
        "entered_count": actions["ENTERED"],
        "held_count": actions["HELD"],
        "exited_count": actions["EXITED"],
        "candidate_rejection_count": actions["CANDIDATE_REJECTED"],
        "exit_distribution": _counter_rows(exit_counts),
        "quote_issue_count": quote_issues,
        "quote_issue_distribution": _counter_rows(quote_distribution),
        "regime_exit_count": len(regime_rows),
        "regime_exits_helped": helped,
        "regime_exits_hurt": hurt,
        "regime_exit_assessment": (
            "insufficient_regime_exit_data" if not regime_rows
            else "helped" if helped > hurt else "hurt" if hurt > helped else "mixed"
        ),
    }
    return summary, rows


def build_rth_soak_review(
    *,
    manifest: dict[str, Any] | None,
    heartbeat: dict[str, Any] | None,
    alert_events: Iterable[dict[str, Any]],
    alert_deliveries: Iterable[dict[str, Any]],
    regime_events: Iterable[dict[str, Any]],
    open_trades: Iterable[dict[str, Any]],
    closed_trades: Iterable[dict[str, Any]],
    journal: Iterable[dict[str, Any]],
    marks: Iterable[dict[str, Any]],
    candidates: Iterable[dict[str, Any]] = (),
    generated_at: str | None = None,
) -> dict[str, Any]:
    open_rows, closed_rows = list(open_trades), list(closed_trades)
    journal_rows, regime_rows = list(journal), list(regime_events)
    alert_summary, alert_quality = summarize_alerts(alert_events, alert_deliveries)
    open_ids = {str(row.get("paper_trade_id")) for row in open_rows if row.get("paper_trade_id")}
    alert_summary["alerts_linked_to_open_trades"] = sum(
        str(row.get("trade_id") or "") in open_ids for row in alert_quality
    )
    regime_summary, regime_review, snapshots = summarize_regimes(
        regime_rows, marks, [*open_rows, *closed_rows], journal_rows
    )
    greek_summary, greek_review = summarize_greeks(snapshots, regime_rows)
    paper_summary, paper_review = summarize_paper_trades(
        open_rows, closed_rows, journal_rows, candidates
    )
    has_data = any((
        alert_summary["alert_count"], regime_summary["transition_count"],
        paper_summary["paper_trade_count"], len(journal_rows),
    ))
    if not has_data:
        next_action = "Run readiness, then collect an RTH local-paper soak."
    elif greek_summary["latest_status"] in {"Degraded", "Unavailable"}:
        next_action = "Review missing Greek fields before the next soak."
    elif paper_summary["quote_issue_count"]:
        next_action = "Review quote-quality blockers before the next soak."
    else:
        next_action = "Collect another RTH session and compare alert and exit quality."
    if not has_data:
        narrative = "No soak data was found. The review completed without inferring results."
    else:
        narrative = (
            f"Reviewed {alert_summary['alert_count']} alerts, "
            f"{regime_summary['transition_count']} regime transitions, and "
            f"{paper_summary['paper_trade_count']} paper trades. Alert noise: "
            f"{alert_summary['noise_assessment']}. Regime exits: "
            f"{paper_summary['regime_exit_assessment']}."
        )
    return {
        "generated_at": generated_at or datetime.now().astimezone().isoformat(),
        "run_id": (manifest or {}).get("portfolio_run_id"),
        "run_status": (manifest or {}).get("status") or (heartbeat or {}).get("status"),
        "last_heartbeat": (heartbeat or {}).get("latest_tick_time"),
        "profiles": list((manifest or {}).get("profiles") or []),
        "has_data": has_data,
        "insufficient_data": not has_data,
        "alert_summary": alert_summary,
        "regime_summary": regime_summary,
        "greek_summary": greek_summary,
        "paper_summary": paper_summary,
        "narrative": narrative,
        "next_action": next_action,
        "alert_quality": alert_quality,
        "regime_transition_review": regime_review,
        "paper_trade_review": paper_review,
        "greek_availability_review": greek_review,
        "safety": {
            "read_only_review": True,
            "no_broker_order_sent": True,
            "no_order_preview": True,
            "no_alerts_sent_during_review": True,
        },
    }


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _candidate_rows(run_dir: Path | None) -> list[dict[str, Any]]:
    if run_dir is None:
        return []
    rows: list[dict[str, Any]] = []
    for path in run_dir.glob("scanner/**/ranked_candidates.csv"):
        rows.extend(_read_csv(path))
    return rows


def collect_rth_soak_review(
    *,
    portfolio_root: Path | str | None = None,
    alert_output_root: Path | str | None = None,
    run_ref: str = "latest",
) -> dict[str, Any]:
    run_dir = ledger.resolve_portfolio_run_dir(run_ref, portfolio_root)
    alert_root = Path(alert_output_root or REPO_ROOT / "outputs")
    run_alerts = AlertJournal(run_dir / "alerts") if run_dir is not None else None
    alerts = (
        run_alerts
        if run_alerts is not None
        and (run_alerts.events_path.is_file() or run_alerts.deliveries_path.is_file())
        else AlertJournal.under_output_root(alert_root)
    )
    return build_rth_soak_review(
        manifest=ledger.load_manifest(run_ref, portfolio_root),
        heartbeat=ledger.load_heartbeat(run_ref, portfolio_root),
        alert_events=alerts.load_events(),
        alert_deliveries=alerts.load_deliveries(),
        regime_events=ledger.load_regime_events(run_ref, portfolio_root),
        open_trades=ledger.load_open_trades(run_ref, portfolio_root),
        closed_trades=ledger.load_closed_trades(run_ref, portfolio_root),
        journal=ledger.load_execution_journal(run_ref, portfolio_root),
        marks=ledger.load_paper_marks(run_ref, portfolio_root),
        candidates=_candidate_rows(run_dir),
    )


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _markdown(report: dict[str, Any]) -> str:
    alerts = report["alert_summary"]
    regimes = report["regime_summary"]
    paper = report["paper_summary"]
    greeks = report["greek_summary"]
    return "\n".join([
        "# RTH Live-Paper Soak Review",
        "",
        "READ-ONLY REVIEW - NO BROKER ORDER SENT.",
        "",
        f"- Run: `{report.get('run_id') or 'not available'}`",
        f"- Status: `{report.get('run_status') or 'not available'}`",
        f"- Profiles: {', '.join(report.get('profiles') or []) or 'not available'}",
        f"- Alerts: {alerts['alert_count']} ({alerts['suppressed_count']} suppressed)",
        f"- Paper trades: {paper['paper_trade_count']} "
        f"({paper['open_trade_count']} open / {paper['closed_trade_count']} closed)",
        f"- Regime transitions: {regimes['transition_count']}",
        f"- Latest daily/context regime: "
        f"{regimes.get('latest_daily_regime') or 'unavailable'} / "
        f"{regimes.get('latest_context_regime') or 'unavailable'}",
        f"- Greek availability: {greeks['latest_status']}",
        f"- Quote-quality events: {paper['quote_issue_count']}",
        "",
        "## Assessment",
        "",
        report["narrative"],
        "",
        "## Next action",
        "",
        report["next_action"],
        "",
    ])


def write_rth_soak_review(
    report: dict[str, Any],
    output_root: Path | str = "outputs/reviews",
) -> Path:
    root = Path(output_root)
    if not root.is_absolute():
        root = REPO_ROOT / root
    destination = root / "latest"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "rth_soak_review.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (destination / "rth_soak_review.md").write_text(_markdown(report), encoding="utf-8")
    _write_csv(destination / "alert_quality.csv", report["alert_quality"], ALERT_FIELDS)
    _write_csv(
        destination / "regime_transition_review.csv",
        report["regime_transition_review"],
        REGIME_FIELDS,
    )
    _write_csv(
        destination / "paper_trade_review.csv", report["paper_trade_review"], PAPER_FIELDS
    )
    _write_csv(
        destination / "greek_availability_review.csv",
        report["greek_availability_review"],
        GREEK_FIELDS,
    )
    return destination


def load_latest_rth_soak_review(output_root: Path | str | None = None) -> dict[str, Any]:
    root = Path(output_root or REPO_ROOT / "outputs")
    path = root / "reviews" / "latest" / "rth_soak_review.json"
    if not path.is_file():
        return {"available": False, "path": str(path)}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "path": str(path)}
    return {"available": isinstance(report, dict), "path": str(path), **report}


def sample_fixture_review() -> dict[str, Any]:
    """Return one deterministic, no-network soak lifecycle for smoke/tests."""
    entered = "2026-06-22T10:00:00-04:00"
    exited = "2026-06-22T10:10:00-04:00"
    first_regime = {
        "timestamp": entered,
        "final_regime_label": "ABSORPTION",
        "daily_regime_code": "R1_NEGATIVE_TREND",
        "context_regime_code": "R4_PRE_OPEX_CHARM_BUILD",
        "da_gex_sign_changes": 0,
        "greek_api_available_fields": ["da_gex", "dex", "vex", "charm", "vanna"],
        "greek_api_missing_fields": [],
    }
    last_regime = {
        "timestamp": exited,
        "final_regime_label": "TRANSITION",
        "daily_regime_code": "R3_WHIPSAW",
        "context_regime_code": "R5_OPEX_WEEK_MAGNET",
        "da_gex_sign_changes": 1,
        "maxvol_strike": 5820.0,
        "maxvol_migration": 10.0,
        "greek_api_available_fields": ["da_gex", "dex", "vex", "charm"],
        "greek_api_missing_fields": ["vanna"],
    }
    trade = {
        "paper_trade_id": "paper_fixture_1",
        "profile_id": "morning_5k_call_tp75_control",
        "symbol": "SPX",
        "side": "CALL_CREDIT",
        "status": "closed",
        "opened_at": entered,
        "closed_at": exited,
        "entry_credit": 1.0,
        "current_mark": 0.4,
        "realized_pnl": 60.0,
        "exit_reason": "take_profit",
        "entry_regime_json": json.dumps(first_regime),
        "current_regime_json": json.dumps(last_regime),
    }
    journal = [
        {"timestamp": entered, "action": "ENTERED", "paper_trade_id": "paper_fixture_1",
         "profile_id": trade["profile_id"], "reason_codes": ["local_chain_mid_fill"]},
        {"timestamp": "2026-06-22T10:05:00-04:00", "action": "HELD",
         "paper_trade_id": "paper_fixture_1", "profile_id": trade["profile_id"],
         "reason_codes": ["no_exit_condition_met"]},
        {"timestamp": exited, "action": "EXITED", "paper_trade_id": "paper_fixture_1",
         "profile_id": trade["profile_id"], "reason_codes": ["take_profit_threshold_hit"]},
    ]
    regime_event = {
        "timestamp": exited,
        "symbol": "SPX",
        "old_regime": "ABSORPTION",
        "new_regime": "TRANSITION",
        "trigger": "daily_da_gex_regime_changed+opex_context_regime_changed+"
                   "maxvol_migrated+greek_data_degraded",
        "levels_involved": {
            "old_daily_regime": "R1_NEGATIVE_TREND",
            "daily_regime": "R3_WHIPSAW",
            "old_context_regime": "R4_PRE_OPEX_CHARM_BUILD",
            "context_regime": "R5_OPEX_WEEK_MAGNET",
            "maxvol_migration": 10.0,
            "newly_missing_greek_fields": ["vanna"],
            "newly_available_greek_fields": [],
        },
        "severity": "WARN",
        "affects_open_positions": True,
        "reason_codes": [
            "daily_da_gex_regime_changed", "da_gex_path_flipped_or_whipsawed",
            "opex_context_regime_changed", "maxvol_migrated_materially",
            "greek_api_field_disappeared",
        ],
    }
    alert = {
        "timestamp": exited,
        "event_id": "alert_fixture_1",
        "source": "REGIME_CHANGE",
        "severity": "WARNING",
        "symbol": "SPX",
        "profile_id": trade["profile_id"],
        "trade_id": "paper_fixture_1",
        "reason_codes": regime_event["reason_codes"],
        "suppressed": False,
    }
    duplicate = {
        **alert,
        "event_id": "alert_fixture_duplicate",
        "suppressed": True,
        "suppression_reason": "suppressed_by_cooldown",
    }
    deliveries = [{
        "event_id": "alert_fixture_1", "backend": "cockpit", "attempted": True,
        "delivered": True, "reason": "local_feed_accepted", "timestamp": exited,
    }]
    marks = [
        {"timestamp": entered, "paper_trade_id": "paper_fixture_1",
         "current_regime_snapshot": json.dumps(first_regime)},
        {"timestamp": exited, "paper_trade_id": "paper_fixture_1",
         "current_regime_snapshot": json.dumps(last_regime)},
    ]
    return build_rth_soak_review(
        manifest={
            "portfolio_run_id": "fixture_rth_soak", "status": "completed",
            "profiles": [trade["profile_id"]],
        },
        heartbeat={"status": "completed", "latest_tick_time": exited},
        alert_events=[alert, duplicate],
        alert_deliveries=deliveries,
        regime_events=[regime_event],
        open_trades=[],
        closed_trades=[trade],
        journal=journal,
        marks=marks,
        candidates=[],
        generated_at="2026-06-22T10:15:00-04:00",
    )
