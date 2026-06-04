"""Phase 10A — wing CORRIDOR validity (CW1 < Spot < PW1).

Mandatory rule: wing structure is only ACTIVE when the call floor (CW1) is below
spot AND the put ceiling (PW1) is above spot. A call floor above spot is NOT an
active floor. WDS may still be computed as RAW context but must not be treated as
active structure.

Pure helpers + operator read; nothing here executes or previews an order.
"""

from __future__ import annotations

import src.app.cockpit_helpers as ch
from src.providers.structure.types import ExposureContext

# ── wing_corridor_status ─────────────────────────────────────────────────────

def test_corridor_valid_when_cw1_below_spot_below_pw1():
    r = ch.wing_corridor_status(7570, 7560, 7600)
    assert r["corridor_valid"] is True
    assert r["cw1"] == 7560 and r["pw1"] == 7600 and r["spot"] == 7570


def test_corridor_invalid_cw1_above_spot():
    r = ch.wing_corridor_status(7553.68, 7600, 7800)
    assert r["corridor_valid"] is False
    assert "CW1 is not below spot" in r["reason"]
    assert "above spot" in r["side_read"]


def test_corridor_invalid_pw1_below_spot():
    r = ch.wing_corridor_status(7700, 7560, 7600)
    assert r["corridor_valid"] is False
    assert "PW1 is not above spot" in r["reason"]
    assert "below spot" in r["side_read"]


def test_corridor_invalid_missing_cw1():
    r = ch.wing_corridor_status(7570, None, 7600)
    assert r["corridor_valid"] is False and "CW1" in r["reason"]


def test_corridor_invalid_missing_pw1():
    r = ch.wing_corridor_status(7570, 7560, None)
    assert r["corridor_valid"] is False and "PW1" in r["reason"]


# ── WDS is raw (not active) when corridor invalid ────────────────────────────

def _ex_call_floor_above_spot():
    # CALL_FLOOR 10K at 7600 is ABOVE spot 7553.68 → corridor invalid.
    # (also include a put ceiling above spot so only the CW1 rule fails)
    return ExposureContext(
        call_floor_2k=7550,
        call_floor_10k=7600, call_floor_10k_volume=12000,
        call_floor_10k_w2_strike=7595, call_floor_10k_w2_volume=3720,   # raw WDS ~0.69
        put_ceiling_10k=7800, put_ceiling_10k_volume=11000,
        put_ceiling_10k_w2_strike=7805, put_ceiling_10k_w2_volume=8000)


def test_wds_raw_not_active_when_corridor_invalid():
    wd = ch.wing_dominance(_ex_call_floor_above_spot(), spot=7553.68)
    assert wd["corridor_valid"] is False
    assert wd["wds_active"] is False
    # active dominant is unavailable, but raw WDS is still computed for context
    assert wd["dominant_wing_side"] == "unavailable"
    assert wd["wds_source"] == "unavailable"
    assert wd["raw_wds_source"] == "true"
    assert wd["raw_dominant_label"] == "CALL_FLOOR 10K"
    assert wd["raw_dominant_wds"] is not None


def test_wds_active_when_corridor_valid():
    ex = ExposureContext(
        call_floor_10k=7540, call_floor_10k_volume=15000,
        call_floor_10k_w2_strike=7535, call_floor_10k_w2_volume=4500,   # CALL WDS 0.70
        put_ceiling_10k=7600, put_ceiling_10k_volume=12000,
        put_ceiling_10k_w2_strike=7605, put_ceiling_10k_w2_volume=8400)  # PUT WDS 0.30
    wd = ch.wing_dominance(ex, spot=7557.0)
    assert wd["corridor_valid"] is True and wd["wds_active"] is True
    assert wd["dominant_wing_side"] == "CALL" and wd["wds_source"] == "true"


# ── operator read must not call a call-floor-above-spot an active floor ──────

def test_operator_read_inactive_corridor():
    ex = _ex_call_floor_above_spot()
    spot = 7553.68
    dl = ch.operator_decision_layer(
        spot=spot, gamma_regime="negative", da_gex=-2.0,
        gamma=ch.primary_secondary_gamma(ex, spot), wings=ch.wing_stack(ex, spot),
        wds=ch.wing_dominance(ex, spot))
    sr = dl["structure_read"]
    assert "Structure status: Inactive — corridor not formed." in sr
    # the 7600 call floor (above spot) is NOT described as an active dominant floor
    assert "Dominant wing is CALL_FLOOR 10K" not in sr
    assert "not acting as the active floor" in sr
    # nearest 2K wing is local risk, the 10K corridor is explicitly not formed
    assert "the full 10K wing corridor is not formed" in sr
    # candidate risk must NOT name a primary 10K structure when corridor invalid
    assert "Primary structure is the dominant" not in dl["candidate_risk"]


def test_operator_read_active_corridor_names_dominant():
    ex = ExposureContext(
        call_floor_2k=7550,
        call_floor_10k=7540, call_floor_10k_volume=15000,
        call_floor_10k_w2_strike=7535, call_floor_10k_w2_volume=4500,
        put_ceiling_10k=7600, put_ceiling_10k_volume=12000,
        put_ceiling_10k_w2_strike=7605, put_ceiling_10k_w2_volume=8400)
    spot = 7557.0
    dl = ch.operator_decision_layer(
        spot=spot, gamma_regime="negative", da_gex=-2.0,
        gamma=ch.primary_secondary_gamma(ex, spot), wings=ch.wing_stack(ex, spot),
        wds=ch.wing_dominance(ex, spot))
    assert "Structure status: Active corridor." in dl["structure_read"]
    assert "Dominant wing is CALL_FLOOR 10K at 7540" in dl["structure_read"]
