"""Phase 2.7 — score breakdown + weak components + rejection_type."""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.providers.quotes.mock_provider import MockQuoteProvider
from src.providers.quotes.types import QuoteRequest
from src.providers.structure.types import ExposureContext, StructureSnapshot
from src.strategies.base import SCORE_META_KEYS, weak_components_of
from src.strategies.vertical_wing.strategy import VerticalWingV1

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── fixtures ───────────────────────────────────────────────────────────

def _structure_at(spot: float = 5800.0) -> StructureSnapshot:
    return StructureSnapshot(
        symbol="SPX", spot=spot,
        quote_ts=datetime(2026, 6, 1, 14, 30),
        exposures=ExposureContext(
            maxvol=5810.0, gamma_regime="positive", da_gex_signed=1.8,
            put_ceiling_2k=5815.0, put_ceiling_5k=5810.0,
            call_floor_2k=5785.0,  call_floor_5k=5790.0,
        ),
        expiry="2026-06-01", dte=0, source="zerosigma_api",
    )


def _scored_candidates(structure: StructureSnapshot | None = None):
    structure = structure or _structure_at()
    strat = VerticalWingV1()
    required = strat.required_quote_strikes(structure, strat.default_parameters)
    chain = MockQuoteProvider().get_option_chain(
        "SPX", request=QuoteRequest(
            symbol="SPX", spot_hint=structure.spot,
            required_strikes=tuple(required),
        ),
    )
    assert chain is not None
    candidates = strat.generate_candidates(structure, chain, strat.default_parameters)
    for c in candidates:
        strat.score(c, structure, chain, strat.default_parameters)
    return strat, structure, chain, candidates


# ── score_breakdown has every component + final_score ──────────────────

def test_score_breakdown_contains_every_component_plus_final_score():
    _, _, _, candidates = _scored_candidates()
    assert candidates
    expected_components = {
        "credit_size", "credit_to_risk", "distance_from_spot",
        "structure_strength", "maxvol_alignment", "gamma_regime",
        "bid_ask_quality", "time_decay_headroom",
    }
    for c in candidates:
        present = set(c.score_breakdown.keys())
        missing = expected_components - present
        assert not missing, f"score_breakdown missing {missing}"
        # final_score is also stamped onto the breakdown
        assert "final_score" in c.score_breakdown
        assert math.isclose(c.score_breakdown["final_score"], c.score, abs_tol=1e-9)


# ── select() stamps threshold + gap onto every candidate ───────────────

def test_select_populates_threshold_and_gap_on_every_candidate():
    strat, _, _, candidates = _scored_candidates()
    decision = strat.select(candidates, strat.default_parameters)
    threshold = float(strat.default_parameters.get("no_trade_score_threshold", 0.60))
    assert decision.threshold_used == threshold
    for c in candidates:
        assert c.score_threshold == threshold
        assert c.score_gap_to_threshold is not None
        assert math.isclose(c.score_gap_to_threshold, threshold - c.score, abs_tol=1e-9)
        # gap is also mirrored into the breakdown for CSV/JSONL
        assert c.score_breakdown["no_trade_threshold"] == threshold
        assert math.isclose(
            c.score_breakdown["score_gap_to_threshold"],
            c.score_gap_to_threshold,
            abs_tol=1e-9,
        )


# ── weak_components helper excludes meta and returns the lowest n ──────

def test_weak_components_of_returns_two_lowest_excluding_meta():
    breakdown = {
        "credit_size": 0.43,
        "credit_to_risk": 0.19,
        "distance_from_spot": 0.50,
        "structure_strength": 0.875,
        "maxvol_alignment": 0.00,
        "gamma_regime": 1.00,
        "bid_ask_quality": 0.50,
        "time_decay_headroom": 0.50,
        "final_score": 0.61,
        "no_trade_threshold": 0.60,
        "score_gap_to_threshold": -0.01,
    }
    weak = weak_components_of(breakdown, n=2)
    assert weak == ["maxvol_alignment=0.00", "credit_to_risk=0.19"]
    # no meta key leaks
    for w in weak:
        for meta in SCORE_META_KEYS:
            assert not w.startswith(f"{meta}=")


def test_weak_components_handles_empty_or_none_input():
    assert weak_components_of(None) == []
    assert weak_components_of({}) == []
    # all-meta input → empty list
    assert weak_components_of(
        {"final_score": 0.5, "no_trade_threshold": 0.6, "score_gap_to_threshold": 0.1},
    ) == []


# ── rejection_type values are correct ──────────────────────────────────

def test_rejection_type_selected_and_score_below_threshold():
    """In the default stub run, CALL_CREDIT scores above threshold → selected;
    PUT_CREDIT scores below → score_below_threshold."""
    strat, _, _, candidates = _scored_candidates()
    decision = strat.select(candidates, strat.default_parameters)
    by_side = {c.side: c for c in candidates}
    if decision.decision == "TRADE_CALL_CREDIT":
        assert by_side["CALL_CREDIT"].rejection_type == "selected"
        assert by_side["PUT_CREDIT"].rejection_type == "score_below_threshold"
    elif decision.decision == "TRADE_PUT_CREDIT":
        assert by_side["PUT_CREDIT"].rejection_type == "selected"
        assert by_side["CALL_CREDIT"].rejection_type == "score_below_threshold"


def test_rejection_type_filter_rejected_marks_candidates_correctly():
    """Manually flag a candidate as filter-rejected before select() and
    verify it's labeled `filter_rejected`."""
    strat, _, _, candidates = _scored_candidates()
    candidates[0].rejected = True
    candidates[0].rejection_reasons.append("test: forced filter")
    strat.select(candidates, strat.default_parameters)
    assert candidates[0].rejection_type == "filter_rejected"


def test_rejection_type_all_filter_rejected_branch():
    """When every candidate is filter-rejected, decision.rejection_type
    is `filter_rejected` and explanation lists the reasons."""
    strat, _, _, candidates = _scored_candidates()
    for c in candidates:
        c.rejected = True
        c.rejection_reasons.append("test: forced filter")
    decision = strat.select(candidates, strat.default_parameters)
    assert decision.decision == "NO_TRADE"
    assert decision.rejection_type == "filter_rejected"
    assert all(c.rejection_type == "filter_rejected" for c in candidates)


# ── below-threshold NO_TRADE explanation is informative ────────────────

def test_below_threshold_explanation_mentions_best_threshold_gap_and_weak():
    """Inflate the threshold above all candidate scores; explanation must
    name best side+strikes+credit+score+threshold+gap+weakest two."""
    strat, _, _, candidates = _scored_candidates()
    params = {**strat.default_parameters, "no_trade_score_threshold": 0.99}
    decision = strat.select(candidates, params)
    assert decision.decision == "NO_TRADE"
    assert decision.rejection_type == "score_below_threshold"
    msg = decision.explanation
    # best candidate descriptor
    assert ("CALL_CREDIT" in msg) or ("PUT_CREDIT" in msg)
    assert "@ " in msg                  # credit shown
    assert "scored " in msg             # score shown
    assert "threshold 0.99" in msg
    assert "by " in msg                 # gap shown
    assert "Weakest components" in msg
    # at least one of the known components surfaces
    assert any(c in msg for c in (
        "credit_size", "credit_to_risk", "distance_from_spot",
        "structure_strength", "maxvol_alignment",
        "gamma_regime", "bid_ask_quality", "time_decay_headroom",
    ))


# ── CSV writer emits the new observability columns ─────────────────────

def _spawn_scanner(tmp_path: Path) -> tuple[Path, Path]:
    env = dict(os.environ)
    for k in list(env.keys()):
        if k.startswith("ZS_API_") or k == "ZS_STRUCTURE_PROVIDER":
            env.pop(k, None)
    env["ZS_STRUCTURE_PROVIDER"] = "stub"
    env["ZS_API_AUTH_MODE"]      = "none"
    env["OUTPUT_DIR"]            = str(tmp_path / "outputs")
    env["PYTHONPATH"]            = str(REPO_ROOT)
    rc = subprocess.call(
        [sys.executable, "-m", "scripts.run_scanner"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert rc == 0
    return (
        tmp_path / "outputs" / "latest" / "ranked_candidates.csv",
        tmp_path / "outputs" / "latest" / "decision_log.jsonl",
    )


def test_csv_includes_score_breakdown_columns_and_json(tmp_path: Path):
    csv_path, _ = _spawn_scanner(tmp_path)
    with csv_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    fields = set(rows[0].keys())
    # per-component score columns
    expected_components = {
        "score_credit_size", "score_credit_to_risk", "score_distance_from_spot",
        "score_structure_strength", "score_maxvol_alignment",
        "score_gamma_regime", "score_bid_ask_quality", "score_time_decay_headroom",
    }
    assert expected_components <= fields, f"missing {expected_components - fields}"
    # meta-style scoring columns
    assert {"score", "final_score", "no_trade_threshold",
            "score_gap_to_threshold", "rejection_type",
            "weak_components", "score_breakdown_json"} <= fields
    # planned_loss_dollars name preserved for back-compat
    assert "planned_loss_dollars" in fields
    # score_breakdown_json parses + matches the per-component columns
    sample = rows[0]
    parsed = json.loads(sample["score_breakdown_json"])
    # CSV column is rounded to 4 decimals; JSON has full precision.
    assert math.isclose(
        parsed["credit_size"], float(sample["score_credit_size"]), abs_tol=1e-3,
    )
    # rejection_type is one of the documented values
    assert sample["rejection_type"] in {
        "selected", "score_below_threshold", "filter_rejected",
        "no_candidates", "missing_quotes", "missing_structure",
    } or sample["rejection_type"] == ""


def test_csv_rejection_type_distinguishes_selected_from_below_threshold(tmp_path: Path):
    csv_path, _ = _spawn_scanner(tmp_path)
    with csv_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    types = {r["rejection_type"] for r in rows}
    # default stub run picks one side and rejects the other on score
    assert "selected" in types
    assert "score_below_threshold" in types


# ── JSONL gains the per-candidate + decision-level fields ──────────────

def test_jsonl_decision_log_carries_phase2p7_fields(tmp_path: Path):
    _, log_path = _spawn_scanner(tmp_path)
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records
    record = records[0]
    # decision-level
    for k in ("threshold_used", "rejection_type", "best_score", "weak_components"):
        assert k in record, f"missing {k}"
    # per-candidate
    for c in record["all_candidates"]:
        assert "score_breakdown"        in c
        assert "score_threshold"        in c
        assert "score_gap_to_threshold" in c
        assert "weak_components"        in c
        assert "rejection_type"         in c


# ── Streamlit imports cleanly under bare mode ──────────────────────────

def test_streamlit_module_imports_cleanly():
    """Catch syntax / import regressions without launching the server."""
    import importlib
    mod = importlib.import_module("src.app.streamlit_main")
    assert mod is not None
