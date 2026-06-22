from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from src.alerts.adapters import regime_change_to_alert
from src.backtesting import learning, mappers
from src.backtesting.replay_runner import research_regime_fields
from src.providers.structure.greek_parity import PARITY_FIELDS, write_greek_parity_audit
from src.providers.structure.greek_probe import probe_configured_provider
from src.providers.structure.types import ExposureContext, StructureSnapshot
from src.providers.structure.zerosigma_api import ZeroSigmaApiStructureProvider
from src.regime.daily_path import (
    DaGexPathState,
    append_da_gex_observation,
    classify_daily_path,
)
from src.regime.events import RegimeEventDebouncer
from src.regime.opex import classify_opex_context, monthly_opex_date
from src.regime.snapshot import build_regime_snapshot

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 22, 14, 30, tzinfo=UTC)


def _snapshot_payload() -> dict:
    row = {
        "strike": 6000.0,
        "c_volume": 12000.0, "p_volume": 8000.0,
        "c_oi": 100.0, "p_oi": 120.0,
        "c_iv": 0.20, "p_iv": 0.22,
        "c_delta": 0.5, "p_delta": -0.5,
        "c_gamma": 0.01, "p_gamma": 0.01,
        "c_vega": 2.0, "p_vega": 2.1,
        "c_theta": -3.0, "p_theta": -3.1,
        "c_vanna": 0.11, "p_vanna": -0.12,
        "c_charm": 0.21, "p_charm": -0.22,
        "c_vomma": 0.31, "p_vomma": 0.32,
        "c_speed": 0.41, "p_speed": -0.42,
        "c_zomma": 0.51, "p_zomma": 0.52,
        "c_raw_gex_1pct": 1.0, "p_raw_gex_1pct": -0.5,
        "c_da_gex_1pct": 0.8, "p_da_gex_1pct": -0.3,
        "c_dex_1pct": 0.7, "p_dex_1pct": -0.2,
        "c_vex_1vol": 0.6, "p_vex_1vol": -0.1,
        "c_vex_skew_1vol": 0.55, "p_vex_skew_1vol": -0.15,
        "c_cex": 0.4, "p_cex": -0.1,
        "c_charm_skew": 0.20, "p_charm_skew": -0.21,
        "c_cex_skew": 0.39, "p_cex_skew": -0.11,
        "c_speed_exp": 0.09, "p_speed_exp": -0.04,
        "c_vomma_exp": 0.08, "p_vomma_exp": 0.03,
        "c_zomma_exp": 0.07, "p_zomma_exp": -0.02,
    }
    return {
        "symbol": "SPX",
        "timestamp": NOW.isoformat(),
        "spot": {"spot": 5995.0},
        "exposures": {
            "total_gex_1pct": 1.5,
            "total_raw_gex_1pct": 1.2,
            "total_da_gex_1pct": 0.5,
            "total_dex_1pct": 0.4,
            "total_vex_1vol": 0.3,
            "total_cex": 0.2,
            "gamma": {"regime": "Positive", "flip": 5990.0},
        },
        "chain": {"expiry": "2026-06-22", "dte": 0, "rows": [row]},
    }


def _structure(exposures: ExposureContext, timestamp: datetime = NOW) -> StructureSnapshot:
    return StructureSnapshot(
        symbol="SPX", spot=5995.0, quote_ts=timestamp,
        exposures=exposures, expiry=timestamp.date().isoformat(), dte=0,
        source="fixture",
    )


def test_mapper_wires_all_api_available_lower_tier_greeks_and_aggregate_totals():
    provider = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example", auth_mode="public_only"
    )
    snapshot = provider.build_snapshot_from_payload(_snapshot_payload(), symbol="SPX")
    exposures = snapshot.exposures
    assert exposures.total_raw_gex_bn == 1.2
    assert exposures.total_dex_bn == 0.4
    assert exposures.total_vex_bn == 0.3
    assert exposures.total_cex_bn == 0.2
    for metric in (
        "theta", "charm", "vanna", "vomma", "speed", "zomma",
        "raw_gex", "da_gex", "dex", "vex", "cex", "vex_skew",
        "speed_exp", "vomma_exp", "zomma_exp",
    ):
        assert metric in exposures.greek_api_available_fields
        assert exposures.per_strike_greek_series[metric]["strikes"] == (6000.0,)
    assert exposures.maxvol == 6000.0 and exposures.maxvol_volume == 20000.0


def test_unavailable_greek_fields_stay_none_with_explicit_reasons():
    payload = _snapshot_payload()
    payload["chain"] = {"expiry": "2026-06-22", "dte": 0, "rows": []}
    payload["exposures"] = {}
    provider = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example", auth_mode="public_only"
    )
    exposures = provider.build_snapshot_from_payload(payload).exposures
    assert exposures.total_dex_bn is None and exposures.total_cex_bn is None
    assert "charm" in exposures.greek_api_missing_fields
    assert exposures.greek_api_unavailable_reasons["charm"] == (
        "field_not_present_in_market_snapshot_chain"
    )
    assert exposures.greek_api_unavailable_reasons["total_theta"] == (
        "aggregate_theta_not_exposed_by_api"
    )


def test_probe_output_is_shape_only_and_secret_safe():
    class FixtureProvider:
        name = "zerosigma_api"

        def status(self):  # type: ignore[no-untyped-def]
            return {"configured": True, "auth_mode": "bearer", "token": "SECRET"}

        def get_snapshot(self, symbol):  # type: ignore[no-untyped-def]
            return ZeroSigmaApiStructureProvider(
                base_url="https://api.test.example", auth_mode="public_only"
            ).build_snapshot_from_payload(_snapshot_payload(), symbol=symbol)

    report = probe_configured_provider(
        FixtureProvider(), symbol="SPX", metrics=("theta", "vanna", "zomma")
    )
    serialized = json.dumps(report, sort_keys=True)
    assert report["status"] == "ok" and report["sanitized"] is True
    assert report["metrics"]["theta"]["shape"]["strike_count"] == 1
    assert "SECRET" not in serialized and report["contains_raw_payload_values"] is False


def test_dashboard_api_audit_artifacts_are_reproducible(tmp_path: Path):
    md_path, csv_path = write_greek_parity_audit(tmp_path)
    markdown = md_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "not categorically missing" in markdown
    assert "/api/v1/market/snapshot" in markdown
    assert "theta" in csv_text and "volume_weighted_greeks" in csv_text
    assert len(PARITY_FIELDS) >= 20


def test_r0_r1_r2_r3_daily_da_gex_path_classification():
    state = append_da_gex_observation(None, -1.0, "2026-06-22T10:00:00-04:00")
    assert classify_daily_path(state).code == "R0_PROVISIONAL"
    state = append_da_gex_observation(state, -2.0, "2026-06-22T10:05:00-04:00")
    assert classify_daily_path(state).code == "R1_NEGATIVE_TREND"

    positive = DaGexPathState()
    positive = append_da_gex_observation(positive, 1.0, "2026-06-22T10:00:00-04:00")
    positive = append_da_gex_observation(positive, 2.0, "2026-06-22T10:05:00-04:00")
    assert classify_daily_path(positive).code == "R2_POSITIVE_DRIFT"
    whipsaw = append_da_gex_observation(positive, -0.5, "2026-06-22T10:10:00-04:00")
    classified = classify_daily_path(whipsaw)
    assert classified.code == "R3_WHIPSAW" and classified.sign_changes == 1


def test_r4_r5_r6_and_unknown_opex_contexts_are_deterministic():
    assert monthly_opex_date(2026, 6) == date(2026, 6, 18)  # Juneteenth Friday
    assert classify_opex_context(date(2026, 6, 10)).code == "R4_PRE_OPEX_CHARM_BUILD"
    assert classify_opex_context(date(2026, 6, 18)).code == "R5_OPEX_WEEK_MAGNET"
    post = classify_opex_context(date(2026, 6, 22))
    assert post.code == "R6_POST_OPEX_GAMMA_RESET" and post.days_to_opex == -4
    assert classify_opex_context(date(2030, 1, 10)).code == "R_UNKNOWN"


def test_maxvol_daily_context_and_greek_degradation_events_are_reason_coded():
    base_exposures = ExposureContext(
        da_gex_signed=-1.0, gamma_regime="negative", maxvol=6000.0,
        greek_api_available_fields=("theta", "vanna"),
    )
    negative = DaGexPathState()
    negative = append_da_gex_observation(negative, -1.0, "2026-06-22T10:00:00-04:00")
    negative = append_da_gex_observation(negative, -2.0, "2026-06-22T10:05:00-04:00")
    previous = build_regime_snapshot(
        _structure(base_exposures), timestamp="2026-06-22T10:05:00-04:00",
        da_gex_path=negative,
    )
    whipsaw = append_da_gex_observation(
        negative, 1.0, "2026-06-22T10:10:00-04:00"
    )
    degraded_exposures = replace(
        base_exposures,
        da_gex_signed=1.0,
        maxvol=6005.0,
        greek_api_available_fields=("theta",),
        greek_api_missing_fields=("vanna",),
    )
    current = build_regime_snapshot(
        _structure(degraded_exposures), timestamp="2026-06-22T10:10:00-04:00",
        previous=previous, da_gex_path=whipsaw,
    )
    event = RegimeEventDebouncer(cooldown_seconds=0).evaluate(previous, current)
    assert event is not None
    assert "maxvol_migrated_materially" in event.reason_codes
    assert "daily_da_gex_regime_changed" in event.reason_codes
    assert "greek_api_field_disappeared" in event.reason_codes
    assert event.levels_involved["newly_missing_greek_fields"] == ["vanna"]


def test_context_regime_change_uses_phase11e_alert_template():
    exposures = ExposureContext(
        da_gex_signed=1.0, gamma_regime="positive",
        greek_api_available_fields=("theta",),
    )
    previous = build_regime_snapshot(
        _structure(exposures), timestamp="2026-06-10T10:00:00-04:00"
    )
    current = build_regime_snapshot(
        _structure(exposures), timestamp="2026-06-18T10:00:00-04:00",
        previous=previous,
    )
    event = RegimeEventDebouncer(cooldown_seconds=0).evaluate(previous, current)
    assert event is not None and "opex_context_regime_changed" in event.reason_codes
    alert = regime_change_to_alert(event)
    assert alert.title == "Expiration context changed"
    assert alert.metadata["template_key"] == "OPEX_CONTEXT_CHANGED"


def test_replay_research_fields_and_learning_dimensions_are_present():
    t1 = datetime.fromisoformat("2026-06-22T10:00:00-04:00")
    t2 = datetime.fromisoformat("2026-06-22T10:05:00-04:00")
    rows = []
    for timestamp, da_value in ((t1, -1.0), (t2, 1.0)):
        rows.append({
            "_ts": timestamp, "Strike": 6000.0, "SPX_Spot": 5995.0,
            "CALL Volume": 12000.0, "PUT Volume": 8000.0,
            "CALL BID": 1.0, "CALL ASK": 1.2, "PUT BID": 1.0, "PUT ASK": 1.2,
            "NET DELTA-ADJ GEX": da_value,
        })
    structure = mappers.map_structure(rows, t2, "SPX")
    fields = research_regime_fields(rows, [t1, t2], t2, structure, "SPX")
    assert fields["daily_regime_code"] == "R3_WHIPSAW"
    assert fields["context_regime_code"] == "R6_POST_OPEX_GAMMA_RESET"
    assert "da_gex_path_flipped_or_whipsawed" in fields["alert_reason_codes"]
    dimensions = dict(learning._PERFORMANCE_DIMENSIONS)
    assert dimensions["daily_regime"] == "daily_regime_code"
    assert dimensions["context_regime"] == "context_regime_code"
    assert dimensions["greek_data_availability"] == "greek_data_availability"


def test_ui_and_source_boundaries_for_phase11f():
    ui_path = REPO / "src/app/streamlit_main.py"
    ui = ui_path.read_text(encoding="utf-8")
    ast.parse(ui)
    assert "Greek API parity / regime diagnostics" in ui
    assert "Daily DA-GEX path" in ui
    assert "Expiration context" in ui

    paths = [
        REPO / "src/providers/structure/greek_probe.py",
        REPO / "src/regime/daily_path.py",
        REPO / "src/regime/opex.py",
        REPO / "scripts/probe_zs_greek_api.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)
    for forbidden in (
        "import dash", "from dash", "import redis", "from redis",
        "place_order(", "submit_order(", "preview_order(", "execute_trade(",
    ):
        assert forbidden not in combined
