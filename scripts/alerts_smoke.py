"""Deterministic local alert smoke fixtures with no network delivery."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.alerts.adapters import paper_journal_to_alert, regime_change_to_alert
from src.alerts.journal import AlertJournal
from src.alerts.router import AlertPreferences, AlertRouter
from src.notifications.cockpit import CockpitNotificationBackend
from src.paper.models import ExecutionJournalEvent
from src.regime.types import (
    RegimeAction,
    RegimeChangeEvent,
    RegimeLabel,
    RegimeSeverity,
)

FIXTURE_TIMESTAMP = "2026-06-22T14:30:00-04:00"


def _regime_fixture():  # type: ignore[no-untyped-def]
    return regime_change_to_alert(RegimeChangeEvent(
        timestamp=FIXTURE_TIMESTAMP,
        symbol="SPX",
        old_regime=RegimeLabel.COMPRESSION,
        new_regime=RegimeLabel.ACCELERATION,
        trigger="gamma_flip_crossed",
        levels_involved={"gamma_flip": 6000.0},
        severity=RegimeSeverity.WARN,
        suggested_action=RegimeAction.WATCH,
        affects_open_positions=True,
        reason_codes=("gamma_flip_crossed_against_position",),
        plain_english_alert="Gamma crossed against an open local paper position.",
    ))


def _paper_exit_fixture():  # type: ignore[no-untyped-def]
    return paper_journal_to_alert(ExecutionJournalEvent(
        timestamp=FIXTURE_TIMESTAMP,
        action="EXITED",
        paper_trade_id="paper_smoke_spx_001",
        profile_id="smoke_call_credit",
        quote_values_used={"exit_mark": 0.45},
        regime_snapshot_summary="Compression remained active.",
        risk_quality_summary="ACCEPTABLE",
        reason_codes=("paper_take_profit_hit",),
        plain_english_explanation="The local paper take-profit rule fired.",
        pnl_impact=55.0,
    ), symbol="SPX")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a deterministic local alert fixture (no network notifications)."
    )
    parser.add_argument("--fixture", choices=("regime_change", "paper_exit"), required=True)
    parser.add_argument("--output-dir", default="outputs/alerts_smoke")
    args = parser.parse_args(argv)

    directory = Path(args.output_dir) / args.fixture
    journal = AlertJournal(directory)
    cockpit = CockpitNotificationBackend(enabled=True)
    router = AlertRouter(
        backends=[cockpit],
        journals=[journal],
        preferences=AlertPreferences(delivery_enabled=True, default_cooldown_seconds=300),
    )
    event = _regime_fixture() if args.fixture == "regime_change" else _paper_exit_fixture()
    results = router.route(event)
    delivered = sum(1 for result in results if result.delivered)
    print(f"alert smoke fixture={args.fixture} event_id={event.event_id} delivered_local={delivered}")
    print(f"  journal: {directory.resolve()}")
    print("  network notifications: disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
