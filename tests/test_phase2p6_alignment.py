"""Phase 2.6 — structure↔quote alignment + zero-candidate diagnostics."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

from src.providers.quotes.mock_provider import MockQuoteProvider
from src.providers.quotes.types import OptionType, QuoteRequest
from src.providers.structure.types import ExposureContext, StructureSnapshot
from src.strategies.vertical_wing.strategy import VerticalWingV1

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── helpers ────────────────────────────────────────────────────────────

def _real_structure(spot: float = 7574.55, expiry: str = "2026-06-01") -> StructureSnapshot:
    """A StructureSnapshot that looks like a live SPX snapshot (levels ~7580)."""
    return StructureSnapshot(
        symbol="SPX", spot=spot,
        quote_ts=datetime(2026, 6, 1, 14, 30),
        exposures=ExposureContext(
            total_gex_bn=1234.56, total_vex_bn=-45.6,
            da_gex_signed=1150.0, gamma_regime="positive",
            gamma_flip=7570.0, call_wall=7600.0, put_wall=7550.0,
            maxvol=7580.0,
            put_ceiling_2k=7600.0, put_ceiling_5k=7585.0,
            call_floor_2k=7560.0,  call_floor_5k=7570.0,
            ddoi_pin=None,
        ),
        expiry=expiry, dte=0, source="zerosigma_api",
    )


# ── MockQuoteProvider re-centers around spot_hint ──────────────────────

def test_mock_quote_provider_recenters_around_spot_hint():
    """Without alignment the mock chain centers at 5800. With spot_hint=7580
    the chain must include strikes spanning that area."""
    p = MockQuoteProvider()
    req = QuoteRequest(symbol="SPX", spot_hint=7580.0)
    chain = p.get_option_chain("SPX", expiry="2026-06-01", request=req)
    assert chain is not None
    strikes = chain.strikes()
    assert min(strikes) <= 7580.0 - 20  # at least 20pt below the hint
    assert max(strikes) >= 7580.0 + 20
    # spot field on the snapshot reflects the requested center
    assert chain.spot == 7580.0


def test_mock_quote_provider_includes_every_required_strike():
    p = MockQuoteProvider()
    required = (7560.0, 7555.0, 7600.0, 7605.0)
    req = QuoteRequest(symbol="SPX", spot_hint=7580.0, required_strikes=required)
    chain = p.get_option_chain("SPX", request=req)
    assert chain is not None
    chain_strikes = set(chain.strikes())
    for k in required:
        assert k in chain_strikes, f"required strike {k} missing"
        # both sides present
        assert chain.find(k, OptionType.CALL) is not None
        assert chain.find(k, OptionType.PUT)  is not None


def test_mock_quote_provider_default_mode_still_centers_at_5800():
    """Back-compat: when no QuoteRequest is passed, the static MOCK_CHAIN
    drives the chain — same as Phase 1.5 / 2 / 2.5."""
    p = MockQuoteProvider()
    chain = p.get_option_chain("SPX", expiry="2026-06-01")  # no request kwarg
    assert chain is not None
    assert chain.spot == 5800.0
    # canonical static chain ranges from 5780 to 5830
    assert 5780.0 in chain.strikes()
    assert 5830.0 in chain.strikes()


def test_mock_quote_provider_request_supersedes_static_chain():
    """When a request is passed, the chain centers on the hint AND covers
    every required strike — even if those strikes are outside the static
    MOCK_CHAIN range."""
    p = MockQuoteProvider()
    req = QuoteRequest(symbol="SPX", spot_hint=7580.0,
                       required_strikes=(7600.0,))
    chain = p.get_option_chain("SPX", request=req)
    assert chain is not None
    assert chain.find(7600.0, OptionType.CALL) is not None


# ── VW exposes required strikes ────────────────────────────────────────

def test_vertical_wing_required_quote_strikes_uses_2k_tier_by_default():
    structure = _real_structure()
    strat = VerticalWingV1(default_parameters={"volume_threshold": 2000, "spread_width": 5})
    out = strat.required_quote_strikes(structure, {})
    # PUT_CEILING_CALL_CREDIT short=7600 long=7605
    # CALL_FLOOR_PUT_CREDIT  short=7560 long=7555
    assert set(out) == {7600.0, 7605.0, 7560.0, 7555.0}


def test_vertical_wing_required_quote_strikes_uses_5k_tier_when_threshold_high():
    structure = _real_structure()
    strat = VerticalWingV1(default_parameters={"volume_threshold": 5000, "spread_width": 5})
    out = strat.required_quote_strikes(structure, {})
    # PUT_CEILING_5K = 7585, CALL_FLOOR_5K = 7570
    assert set(out) == {7585.0, 7590.0, 7570.0, 7565.0}


def test_vertical_wing_returns_empty_when_no_anchors():
    """When structure provides no ceilings/floors, the required-strikes
    list is empty — scanner will fall back to mock_default spot."""
    structure = StructureSnapshot(
        symbol="SPX", spot=0.0,
        quote_ts=datetime(2026, 6, 1, 14, 30),
        exposures=ExposureContext(),  # all None
        expiry=None, dte=None, source="zerosigma_api",
    )
    strat = VerticalWingV1()
    assert strat.required_quote_strikes(structure, {}) == []


# ── VW builds CALL_CREDIT and PUT_CREDIT against aligned mock chain ────

def test_vw_produces_both_sides_against_real_like_structure_plus_mock_chain():
    structure = _real_structure()
    strat = VerticalWingV1()
    required = strat.required_quote_strikes(structure, strat.default_parameters)
    req = QuoteRequest(
        symbol="SPX",
        spot_hint=structure.spot,
        required_strikes=tuple(required),
    )
    chain = MockQuoteProvider().get_option_chain("SPX", request=req)
    assert chain is not None
    candidates = strat.generate_candidates(structure, chain, strat.default_parameters)
    sides = {c.side for c in candidates}
    assert "CALL_CREDIT" in sides
    assert "PUT_CREDIT" in sides
    # all candidates carry a credit > 0
    for c in candidates:
        assert c.credit > 0


# ── scanner decision log gains the new audit fields ───────────────────

def _spawn_scanner(tmp_path: Path, extra_env: dict | None = None) -> Path:
    env = dict(os.environ)
    for k in list(env.keys()):
        if k.startswith("ZS_API_") or k == "ZS_STRUCTURE_PROVIDER":
            env.pop(k, None)
    env["ZS_STRUCTURE_PROVIDER"] = "stub"
    env["ZS_API_AUTH_MODE"]      = "none"
    env["OUTPUT_DIR"]            = str(tmp_path / "outputs")
    env["PYTHONPATH"]            = str(REPO_ROOT)
    if extra_env:
        env.update(extra_env)
    rc = subprocess.call(
        [sys.executable, "-m", "scripts.run_scanner"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert rc == 0
    return tmp_path / "outputs" / "latest" / "decision_log.jsonl"


def test_scanner_decision_log_includes_phase2p6_diagnostics(tmp_path: Path):
    log_path = _spawn_scanner(tmp_path)
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records
    summary = records[0]["snapshot_summary"]
    for key in (
        "required_strikes", "quote_chain_min_strike", "quote_chain_max_strike",
        "missing_required_quote_strikes", "quote_spot_source", "quote_spot_hint",
    ):
        assert key in summary, f"missing snapshot_summary key {key}"
    # stub structure provides spot=5800 → source must be "structure_spot"
    assert summary["quote_spot_source"] == "structure_spot"
    assert summary["quote_spot_hint"] == 5800.0
    # at least 2 required strikes (ceiling + long-leg, floor + long-leg)
    assert len(summary["required_strikes"]) >= 4
    # quote chain covers them
    assert summary["missing_required_quote_strikes"] == []


def test_scanner_candidate_csv_has_quote_columns_under_aligned_mode(tmp_path: Path):
    _spawn_scanner(tmp_path)
    csv_path = tmp_path / "outputs" / "latest" / "ranked_candidates.csv"
    with csv_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    for r in rows:
        # leg bid/ask/mid populated from the aligned chain
        assert float(r["short_bid"]) > 0
        assert float(r["short_ask"]) >= float(r["short_bid"])


# ── zero-candidate explanation distinguishes failure modes ─────────────

def test_zero_candidate_explanation_no_structure_anchors():
    """When structure has no ceilings/floors at all, the refined
    explanation must call that out rather than blaming filters."""
    from scripts.run_scanner import _refine_decision_explanation
    from src.strategies.base import StrategyDecision

    empty_structure = StructureSnapshot(
        symbol="SPX", spot=0.0,
        quote_ts=datetime(2026, 6, 1, 14, 30),
        exposures=ExposureContext(), expiry=None, dte=None, source="zerosigma_api",
    )
    d = StrategyDecision(
        strategy_id="vertical_wing_v1", decision="NO_TRADE",
        selected=None, all_candidates=[],
        explanation="No surviving candidates (all rejected by filters).",
    )
    d2 = _refine_decision_explanation(
        d, required_strikes=[], missing_required_quote_strikes=[],
        structure=empty_structure,
    )
    assert "no structure anchors" in d2.explanation.lower()


def test_zero_candidate_explanation_quote_chain_missing_legs():
    """Anchors exist in structure but the (broken) chain missed required
    strikes — explanation must point at the chain, not filters."""
    from scripts.run_scanner import _refine_decision_explanation
    from src.strategies.base import StrategyDecision

    structure = _real_structure()
    d = StrategyDecision(
        strategy_id="vertical_wing_v1", decision="NO_TRADE",
        selected=None, all_candidates=[],
        explanation="No surviving candidates (all rejected by filters).",
    )
    d2 = _refine_decision_explanation(
        d, required_strikes=[7600.0, 7605.0, 7560.0, 7555.0],
        missing_required_quote_strikes=[7605.0, 7555.0],
        structure=structure,
    )
    assert "quote chain missing required" in d2.explanation


def test_zero_candidate_explanation_preserves_real_rejection_text():
    """When candidates WERE generated and `select()` filtered them, do not
    rewrite the explanation."""
    from scripts.run_scanner import _refine_decision_explanation
    from src.strategies.base import Candidate, StrategyDecision

    c = Candidate(strategy_id="t", side="CALL_CREDIT", symbol="SPX",
                  expiry="2026-06-01", short_strike=5810, long_strike=5815,
                  credit=0.20, max_risk=4.80, reward_risk=0.04,
                  breakeven=5810.20, distance_from_spot=10)
    c.rejected = True
    c.rejection_reasons.append("credit below floor 0.30")
    d = StrategyDecision(
        strategy_id="vertical_wing_v1", decision="NO_TRADE",
        selected=None, all_candidates=[c],
        explanation="No surviving candidates (all rejected by filters).",
    )
    d2 = _refine_decision_explanation(
        d, required_strikes=[5810.0, 5815.0],
        missing_required_quote_strikes=[],
        structure=_real_structure(),
    )
    # Untouched — `all_candidates` was non-empty.
    assert d2.explanation == d.explanation


# ── debug-shape sanitizer never emits secrets ─────────────────────────

def test_debug_shape_redacts_secret_keys_and_string_values():
    from scripts.smoke_zs_api import _shape_of

    payload = {
        "symbol": "SPX",
        "spot": 7574.55,
        "timestamp": "2026-06-01T14:30:00",
        "access_token": "super-secret-jwt-token",
        "api_key": "another-secret",
        "exposures": {
            "total_gex_1pct": 1234.56,
            "wings": {"call_floor": 7560.0, "put_ceiling": 7600.0},
            "password": "should-not-appear",
        },
    }
    shape = _shape_of(payload)
    s = json.dumps(shape)
    assert "super-secret-jwt-token" not in s
    assert "another-secret"          not in s
    assert "should-not-appear"       not in s
    # Numeric scalars come through (useful for diagnosing spot=0.0)
    assert shape["spot"] == 7574.55
    assert shape["exposures"]["total_gex_1pct"] == 1234.56
    # Strings are reduced to type+length (never echoed back)
    assert isinstance(shape["timestamp"], str) and shape["timestamp"].startswith("<str ")


def test_endpoint_probe_via_mocked_provider(monkeypatch, capsys):
    """`smoke_zs_api --endpoint exposures` must hit /api/v1/market/exposures
    via the provider's internal client, render a sanitized summary, and
    never emit Authorization-bearing strings."""
    from src.providers.structure.zerosigma_api import ZeroSigmaApiStructureProvider

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/market/exposures":
            return httpx.Response(200, json={
                "symbol": "SPX",
                "total_gex_1pct": 999.99,
                "total_da_gex_1pct": 444.44,
                "wings": {"call_floor": 5560.0, "put_ceiling": 5610.0},
            })
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    def factory():
        return httpx.Client(base_url="https://api.test.example", transport=transport)
    fake = ZeroSigmaApiStructureProvider(
        base_url="https://api.test.example", auth_mode="public_only",
        symbol="SPX", client_factory=factory,
    )

    monkeypatch.setattr(
        "src.providers.structure.factory.build_structure_provider",
        lambda cfg, override=None: (fake, "zerosigma_api"),
    )
    monkeypatch.setattr(sys, "argv",
                        ["scripts.smoke_zs_api", "--endpoint", "exposures",
                         "--debug-shape", "--symbol", "SPX"])
    import scripts.smoke_zs_api as smoke
    rc = smoke.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "total_gex_1pct" in out
    assert "999.99" in out
    # Sensitive substrings never appear
    for needle in ("authorization", "bearer ", "password", "service_key"):
        assert needle not in out.lower()
