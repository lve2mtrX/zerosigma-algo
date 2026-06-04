"""Phase 9J — true Wing Dominance Score (WDS).

WDS = 1 - (W2_volume / W1_volume), side-specific volume, CALL W2 one strike LOWER
and PUT W2 one strike HIGHER than W1. Higher WDS = cleaner/more dominant wing.
A 10K wing is NOT automatically strong — it must dominate the adjacent strike.

Pure helpers + mapper derivation; nothing here executes or previews an order.
"""

from __future__ import annotations

import src.app.cockpit_helpers as ch
from src.providers.structure.types import ExposureContext
from src.providers.structure.zerosigma_api import ZeroSigmaApiStructureProvider

# ── formula + percent + tiers ────────────────────────────────────────────────

def test_wds_formula():
    r = ch.compute_wds(100, 1000, 90, 300)   # WSR = 0.30 → WDS = 0.70
    assert r["wsr"] == 0.3 and r["wds"] == 0.7 and r["source"] == "true"
    # WDS = 1 - W2/W1 holds generally
    r2 = ch.compute_wds(7600, 12000, 7595, 3600)
    assert abs(r2["wds"] - (1 - 3600 / 12000)) < 1e-9


def test_wds_percent_format():
    assert ch.wds_pct(0.82) == "82%"
    assert ch.wds_pct(0.70) == "70%"
    assert ch.wds_pct(None) == "—"


def test_wds_tier_thresholds():
    assert ch.wds_tier(0.90) == 1 and ch.wds_tier(0.75) == 1     # >= .75 Tier 1
    assert ch.wds_tier(0.60) == 2 and ch.wds_tier(0.50) == 2     # .50–.75 Tier 2
    assert ch.wds_tier(0.40) == 3 and ch.wds_tier(0.30) == 3     # .30–.50 Tier 3
    assert ch.wds_tier(0.29) == 4 and ch.wds_tier(0.0) == 4      # < .30 Tier 4
    assert ch.wds_tier(None) is None


def test_wds_negative_is_tier4():
    # W2 volume > W1 volume → WSR > 1 → WDS < 0 → very weak (Tier 4)
    r = ch.compute_wds(7600, 10000, 7595, 13000)
    assert r["wsr"] == 1.3 and r["wds"] == -0.3 and r["wds_tier"] == 4


# ── unavailable handling (never invent WDS) ──────────────────────────────────

def test_wds_unavailable_when_w2_missing():
    r = ch.compute_wds(7600, 12000, None, None)
    assert r["source"] == "unavailable" and r["wds"] is None
    assert "W2" in r["reason"] and "unavailable" in r["reason"]


def test_wds_unavailable_when_w1_missing():
    assert ch.compute_wds(None, None, 7595, 3000)["source"] == "unavailable"
    assert ch.compute_wds(7600, 0, 7595, 3000)["source"] == "unavailable"   # W1 vol 0


# ── weak wing is NOT described as strong ─────────────────────────────────────

def test_weak_wds_not_strong():
    r = ch.compute_wds(7600, 12000, 7595, 9840)   # WSR 0.82 → WDS 0.18 → Tier 4
    assert r["wds_tier"] == 4
    assert "weak" in r["reason"].lower()
    assert "dominant" not in r["reason"].lower() and "strong" not in r["reason"].lower()


def test_clean_wing_called_dominant():
    r = ch.compute_wds(7600, 12000, 7595, 3600 * 0.0)  # W2 vol 0 → WSR 0 → WDS 1.0
    assert r["wds_tier"] == 1 and "dominant" in r["reason"].lower()


# ── mapper derives W2 direction correctly (CALL lower, PUT higher) ───────────

def test_mapper_w2_direction():
    prov = ZeroSigmaApiStructureProvider(base_url="http://x", auth_mode="bearer", token="t")
    vol = {"strikes": [7500, 7550, 7595, 7600, 7650],
           "calls":   [100, 400, 9000, 12000, 11000],   # CW1=7600; CW2 should be 7595 (lower)
           "puts":    [12000, 11000, 3000, 500, 100]}    # PW1=7550; PW2 should be 7595 (higher)
    ex = prov._build_exposures({"exposures": {"gamma": {}}}, vol, [])
    assert ex.call_floor_10k == 7600
    assert ex.call_floor_10k_w2_strike == 7595 and ex.call_floor_10k_w2_strike < ex.call_floor_10k
    assert ex.call_floor_10k_w2_volume == 9000
    assert ex.put_ceiling_10k == 7550
    assert ex.put_ceiling_10k_w2_strike == 7595 and ex.put_ceiling_10k_w2_strike > ex.put_ceiling_10k
    assert ex.put_ceiling_10k_w2_volume == 3000


def test_exposure_context_w2_backward_compatible():
    ex = ExposureContext()   # old construction still valid
    assert ex.call_floor_10k_w2_strike is None and ex.put_ceiling_10k_w2_volume is None


# ── wing_dominance: dominant by WDS, nearest is separate breach risk ─────────

def _ex_call_dominant():
    # VALID corridor (CW1 7560 < spot 7573.68 < PW1 7600). CALL 10K WDS 0.70
    # (Tier 2) cleaner than PUT 10K WDS ~0.18 (Tier 4). Nearest = a 2K wing.
    return ExposureContext(
        call_floor_2k=7565, call_floor_5k=7562,
        call_floor_10k=7560, call_floor_10k_volume=12000,
        call_floor_10k_w2_strike=7555, call_floor_10k_w2_volume=3600,
        put_ceiling_2k=7600, put_ceiling_5k=7610,
        put_ceiling_10k=7600, put_ceiling_10k_volume=11000,
        put_ceiling_10k_w2_strike=7605, put_ceiling_10k_w2_volume=9000)


def test_dominant_wing_by_wds_not_distance():
    wd = ch.wing_dominance(_ex_call_dominant(), spot=7573.68)
    assert wd["corridor_valid"] is True and wd["wds_active"] is True
    assert wd["dominant_wing_side"] == "CALL"
    assert wd["dominant_wing_label"] == "CALL_FLOOR 10K"
    assert wd["dominant_wing_tier"] == 2 and wd["wds_source"] == "true"
    # nearest wing is the immediate-risk 2K/5K, NOT the dominant 10K
    assert "10K" not in (wd["nearest_wing_label"] or "")
    assert wd["nearest_wing_strike"] != wd["dominant_wing_strike"]
    assert wd["nearest_wing_distance_points"] is not None


def test_dominant_unavailable_when_no_10k():
    wd = ch.wing_dominance(ExposureContext(call_floor_2k=7570), spot=7573.0)
    assert wd["dominant_wing_side"] == "unavailable" and wd["wds_source"] == "unavailable"
    assert "No qualifying 10K wing" in wd["wds_reason"]


def test_dominant_higher_wds_side_wins():
    # both sides true; PUT has cleaner WDS → PUT dominant
    ex = ExposureContext(
        call_floor_10k=7600, call_floor_10k_volume=12000,
        call_floor_10k_w2_strike=7595, call_floor_10k_w2_volume=9000,   # CALL WDS 0.25 T4
        put_ceiling_10k=7800, put_ceiling_10k_volume=11000,
        put_ceiling_10k_w2_strike=7805, put_ceiling_10k_w2_volume=1100)  # PUT WDS 0.90 T1
    wd = ch.wing_dominance(ex, spot=7700.0)
    assert wd["dominant_wing_side"] == "PUT" and wd["dominant_wing_tier"] == 1


# ── operator read uses dominant WDS wing, frames nearest as breach risk ──────

def test_operator_read_distinguishes_dominant_and_nearest():
    ex = _ex_call_dominant()
    spot = 7573.68
    g = ch.primary_secondary_gamma(ex, spot)
    ws = ch.wing_stack(ex, spot)
    wd = ch.wing_dominance(ex, spot)
    dl = ch.operator_decision_layer(spot=spot, gamma_regime="negative", da_gex=-2.0,
                                    gamma=g, wings=ws, wds=wd)
    sr = dl["structure_read"]
    assert "Structure status: Active corridor." in sr
    assert "Dominant wing is CALL_FLOOR 10K at 7560 with WDS 70%" in sr
    assert "immediate breach risk but not the primary structure" in sr
    # candidate risk names the dominant 10K as primary structure, not the 2K wing
    assert "Primary structure is the dominant CALL_FLOOR 10K" in dl["candidate_risk"]
    assert "Primary wing:" not in dl["candidate_risk"]


def test_operator_read_wds_unavailable_copy():
    ex = ExposureContext(call_floor_2k=7570, call_floor_10k=7600, call_floor_10k_volume=12000)
    spot = 7573.0
    dl = ch.operator_decision_layer(
        spot=spot, gamma_regime="negative", da_gex=-1.0,
        gamma=ch.primary_secondary_gamma(ex, spot), wings=ch.wing_stack(ex, spot),
        wds=ch.wing_dominance(ex, spot))
    assert "true WDS is unavailable" in dl["structure_read"]
    assert "adjacent W2 volume is missing" in dl["structure_read"]
