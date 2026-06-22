from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from scripts import alerts_smoke
from src.alerts.adapters import paper_journal_to_alert, regime_change_to_alert
from src.alerts.journal import AlertJournal
from src.alerts.router import AlertPreferences, AlertRouter
from src.alerts.templates import render_prompt
from src.alerts.types import (
    AlertAction,
    AlertDeliveryResult,
    AlertEvent,
    AlertSeverity,
    AlertSource,
)
from src.notifications.cockpit import CockpitNotificationBackend
from src.notifications.pushover import PushoverNotificationBackend
from src.notifications.voice import VoiceNotificationBackend
from src.paper.models import ExecutionJournalEvent
from src.regime.types import (
    RegimeAction,
    RegimeChangeEvent,
    RegimeLabel,
    RegimeSeverity,
)

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 22, 14, 30, tzinfo=UTC)


def _alert(**overrides) -> AlertEvent:  # type: ignore[no-untyped-def]
    values = {
        "event_id": "alert_fixture",
        "timestamp": NOW.isoformat(),
        "source": AlertSource.REGIME_CHANGE,
        "severity": AlertSeverity.WARNING,
        "title": "Regime changed",
        "message": "SPX moved from Compression to Acceleration.",
        "symbol": "SPX",
        "profile_id": "fixture_profile",
        "trade_id": "paper_1",
        "regime_label": "ACCELERATION",
        "old_regime": "COMPRESSION",
        "new_regime": "ACCELERATION",
        "suggested_action": "WATCH",
        "reason_codes": ("regime_label_changed",),
        "metadata": {"trigger": "fixture"},
        "local_only": True,
        "no_broker_order_sent": True,
        "delivery_action": AlertAction.ALL,
    }
    values.update(overrides)
    return AlertEvent(**values)


def _regime_event() -> RegimeChangeEvent:
    return RegimeChangeEvent(
        timestamp=NOW.isoformat(),
        symbol="SPX",
        old_regime=RegimeLabel.COMPRESSION,
        new_regime=RegimeLabel.ACCELERATION,
        trigger="gamma_flip_crossed",
        levels_involved={"gamma_flip": 6000.0},
        severity=RegimeSeverity.CRITICAL,
        suggested_action=RegimeAction.EXIT,
        affects_open_positions=True,
        reason_codes=("gamma_flip_crossed_against_position",),
        plain_english_alert="Gamma crossed against the open local paper position.",
    )


def _paper_event(action: str, reasons: tuple[str, ...]) -> ExecutionJournalEvent:
    return ExecutionJournalEvent(
        timestamp=NOW.isoformat(),
        action=action,
        paper_trade_id="paper_1",
        profile_id="fixture_profile",
        quote_values_used={"current_mark": 0.5},
        regime_snapshot_summary="Acceleration is active.",
        risk_quality_summary="ACCEPTABLE",
        reason_codes=reasons,
        plain_english_explanation="Deterministic local paper event.",
        pnl_impact=50.0,
    )


def test_alert_models_round_trip_and_require_reason_codes():
    event = _alert()
    assert AlertEvent.from_dict(event.to_dict()) == event
    result = AlertDeliveryResult(
        event.event_id, "cockpit", True, True, "local_feed_accepted", None, NOW.isoformat()
    )
    assert result.to_dict()["delivered"] is True
    with pytest.raises(ValueError, match="reason code"):
        _alert(reason_codes=())


def test_prompt_rendering_and_unknown_fallback_are_deterministic():
    normal = render_prompt(
        "REGIME_CHANGED",
        symbol="SPX",
        old_regime="Compression",
        new_regime="Acceleration",
        detail="Review.",
    )
    blunt = render_prompt(
        "REGIME_CHANGED",
        operator_style=True,
        symbol="SPX",
        old_regime="Compression",
        new_regime="Acceleration",
        detail="Review.",
    )
    fallback = render_prompt("NOT_A_TRANSITION", source="PAPER_MARK", detail="Inspect it.")
    assert normal.message == "SPX moved from Compression to Acceleration. Review."
    assert "Hey idiot" in blunt.message
    assert fallback.used_fallback is True and fallback.message.endswith("Inspect it.")


def test_cockpit_backend_is_local_and_voice_is_a_bounded_queue(tmp_path: Path):
    cockpit = CockpitNotificationBackend(enabled=True)
    assert cockpit.send(_alert()).delivered is True
    assert cockpit.payloads[0]["no_broker_order_sent"] is True

    queue_path = tmp_path / "voice.jsonl"
    voice = VoiceNotificationBackend(enabled=True, queue_path=queue_path, max_queue_size=1)
    voice.send(_alert(event_id="first"))
    result = voice.send(_alert(event_id="second"))
    assert result.reason == "queued_tts_deferred"
    assert len(voice.queued) == 1 and voice.queued[0]["event_id"] == "second"
    assert len(queue_path.read_text(encoding="utf-8").splitlines()) == 2


def test_pushover_disabled_by_default_and_missing_credentials_do_not_attempt():
    disabled = PushoverNotificationBackend()
    assert disabled.send(_alert()).reason == "backend_disabled"
    missing = PushoverNotificationBackend(enabled=True)
    result = missing.send(_alert())
    assert result.attempted is False and result.reason == "credentials_missing"


def test_pushover_uses_mocked_network_and_never_leaks_secrets():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(500, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = PushoverNotificationBackend(
        enabled=True,
        user_key="SECRET_USER_KEY",
        api_token="SECRET_API_TOKEN",
        client=client,
    )
    result = backend.send(_alert(severity=AlertSeverity.EMERGENCY))
    client.close()
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    assert result.attempted is True and result.delivered is False
    assert result.error_type == "HTTPStatusError"
    assert "priority=2" in captured["body"] and "retry=60" in captured["body"]
    assert "SECRET_USER_KEY" not in serialized and "SECRET_API_TOKEN" not in serialized


def test_router_journals_delivery_and_suppresses_duplicate_within_cooldown(tmp_path: Path):
    journal = AlertJournal(tmp_path / "alerts")
    cockpit = CockpitNotificationBackend(enabled=True)
    router = AlertRouter(
        backends=[cockpit],
        journals=[journal],
        preferences=AlertPreferences(delivery_enabled=True, default_cooldown_seconds=300),
    )
    first = router.route(_alert(), now=NOW)
    duplicate = router.route(_alert(event_id="same_meaning_new_id"), now=NOW + timedelta(seconds=30))
    later = router.route(_alert(event_id="later"), now=NOW + timedelta(seconds=301))
    assert first[0].delivered is True
    assert duplicate[0].reason == "suppressed_by_cooldown"
    assert later[0].delivered is True
    events = journal.load_events()
    deliveries = journal.load_deliveries()
    assert [row["suppressed"] for row in events] == [False, True, False]
    assert all(row["no_broker_order_sent"] is True for row in events + deliveries)


def test_router_journals_when_global_delivery_is_disabled(tmp_path: Path):
    journal = AlertJournal(tmp_path)
    router = AlertRouter(
        backends=[CockpitNotificationBackend(enabled=True)],
        journals=[journal],
        preferences=AlertPreferences(delivery_enabled=False),
    )
    result = router.route(_alert(), now=NOW)
    assert result[0].reason == "delivery_disabled_alert_journal_only"
    assert len(journal.load_events()) == 1


def test_regime_and_paper_adapters_preserve_reasoned_safety_context():
    regime_alert = regime_change_to_alert(_regime_event())
    assert regime_alert.source == AlertSource.REGIME_CHANGE
    assert regime_alert.severity == AlertSeverity.CRITICAL
    assert regime_alert.metadata["template_key"] == "GAMMA_FLIP_AGAINST"

    exit_alert = paper_journal_to_alert(
        _paper_event("EXITED", ("paper_stop_loss_hit",)), symbol="SPX"
    )
    assert exit_alert.source == AlertSource.PAPER_EXIT
    assert exit_alert.severity == AlertSeverity.CRITICAL
    assert exit_alert.title == "Paper stop hit"
    assert exit_alert.local_only is True and exit_alert.no_broker_order_sent is True

    rejected = paper_journal_to_alert(
        _paper_event("CANDIDATE_REJECTED", ("risk_reward_below_minimum",)), symbol="SPX"
    )
    assert rejected.source == AlertSource.RISK_QUALITY
    assert rejected.reason_codes == ("risk_reward_below_minimum",)


def test_alert_smoke_fixtures_write_local_journals(tmp_path: Path):
    for fixture in ("regime_change", "paper_exit"):
        assert alerts_smoke.main([
            "--fixture", fixture, "--output-dir", str(tmp_path),
        ]) == 0
        journal = AlertJournal(tmp_path / fixture)
        assert len(journal.load_events()) == 1
        assert journal.load_deliveries()[0]["backend"] == "cockpit"


def test_alert_center_and_runner_integration_are_additive_and_local_only():
    ui_path = REPO / "src/app/streamlit_main.py"
    runner_path = REPO / "scripts/run_portfolio_forward.py"
    ui_source = ui_path.read_text(encoding="utf-8")
    runner_source = runner_path.read_text(encoding="utf-8")
    ast.parse(ui_source)
    ast.parse(runner_source)
    assert "def render_alert_center" in ui_source
    assert "Alert Center" in ui_source
    assert "Pushover and voice remain" in ui_source
    assert "if not simple_mode" in ui_source
    assert "_route_new_alerts" in runner_source
    assert "local alert routing failed safely" in runner_source


def test_phase11e_modules_add_no_dash_redis_or_execution_paths():
    paths = [
        *sorted((REPO / "src/alerts").glob("*.py")),
        *sorted((REPO / "src/notifications").glob("*.py")),
        REPO / "scripts/alerts_smoke.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)
    for forbidden in (
        "import dash",
        "from dash",
        "import redis",
        "from redis",
        "place_order(",
        "submit_order(",
        "preview_order(",
        "order_preview(",
        "execute_trade(",
    ):
        assert forbidden not in combined
    assert "stone_research" not in combined
