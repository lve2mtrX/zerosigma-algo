"""Phase 9H — pure helpers: Wing Stack, primary/secondary gamma, DDOI-advanced,
operator decision layer, run/selection mismatch, profile grouping.

All assertions hit PURE helpers — no Streamlit runtime, no execution surface.
"""

from __future__ import annotations

import src.app.cockpit_helpers as ch
import src.app.operator_mode as om
from src.providers.structure.types import ExposureContext

SPOT = 5800.0
_EX = ExposureContext(
    gamma_regime="negative", da_gex_signed=-2.0,
    call_wall=5825.0, put_wall=5780.0, gamma_flip=5795.0,
    put_ceiling_2k=5815.0, put_ceiling_5k=5810.0,
    call_floor_2k=5785.0, call_floor_5k=5790.0,
    gamma_primary=5805.0, gamma_secondary=5825.0,
)


# ── Wing Stack ───────────────────────────────────────────────────────────────

def test_wing_stack_tiers_and_nearest_primary():
    ws = ch.wing_stack(_EX, SPOT)
    # 2K/5K available, 10K not (None on this exposure)
    assert [e["available"] for e in ws["put_ceilings"]] == [True, True, False]
    assert [e["available"] for e in ws["call_floors"]] == [True, True, False]
    # nearest wing = min |distance| (call_floor_2k 5785 is 15 from spot; put_ceiling_5k 5810 is 10)
    assert ws["nearest_wing"]["strike"] == 5810.0
    # primary wing = strongest available tier (5K here, since 10K absent)
    assert ws["primary_wing"]["tier"] == "5k"
    assert ws["any_available"] is True


def test_wing_stack_distance_signed():
    ws = ch.wing_stack(_EX, SPOT)
    pc2 = ws["put_ceilings"][0]   # 5815 → +15
    assert pc2["distance"] == 15.0
    assert pc2["distance_fmt"] == "+15"


def test_wing_stack_10k_present_becomes_primary():
    ex = ExposureContext(put_ceiling_2k=5815.0, put_ceiling_10k=5840.0,
                         call_floor_2k=5785.0)
    ws = ch.wing_stack(ex, SPOT)
    assert ws["put_ceilings"][2]["available"] is True
    assert ws["primary_wing"]["tier"] == "10k"   # strongest tier wins


def test_wing_stack_all_unavailable():
    ws = ch.wing_stack(ExposureContext(), SPOT)
    assert ws["any_available"] is False
    assert ws["nearest_wing"] is None and ws["primary_wing"] is None


# ── primary / secondary gamma ────────────────────────────────────────────────

def test_gamma_payload_cluster_source():
    g = ch.primary_secondary_gamma(_EX, SPOT)
    assert g["source"] == "payload_cluster"
    assert g["primary"] == 5805.0 and g["secondary"] == 5825.0
    assert g["available"] is True


def test_gamma_derived_from_walls_when_no_clusters():
    ex = ExposureContext(call_wall=5825.0, put_wall=5780.0, gamma_flip=5795.0)
    g = ch.primary_secondary_gamma(ex, SPOT)
    assert g["source"] == "derived_from_walls"
    # nearest to spot 5800: flip 5795 (5), put_wall 5780 (20), call_wall 5825 (25)
    assert g["primary"] == 5795.0 and g["secondary"] == 5780.0


def test_gamma_unavailable():
    g = ch.primary_secondary_gamma(ExposureContext(), SPOT)
    assert g["source"] == "unavailable" and g["available"] is False
    assert "unavailable" in g["note"].lower()


# ── DDOI advanced-only (never a prime card) ──────────────────────────────────

def test_ddoi_advanced_present_and_absent():
    present = ch.ddoi_advanced(ExposureContext(ddoi_pin=5800.0))
    assert present["available"] is True and present["value_fmt"] == "5800"
    absent = ch.ddoi_advanced(ExposureContext())
    assert absent["available"] is False and "Unavailable" in absent["note"]
    assert ch.DDOI_HELP and "dealer-positioning" in ch.DDOI_HELP


# ── operator decision layer ──────────────────────────────────────────────────

def test_decision_layer_has_all_parts_and_references_gamma():
    g = ch.primary_secondary_gamma(_EX, SPOT)
    ws = ch.wing_stack(_EX, SPOT)
    dl = ch.operator_decision_layer(
        spot=SPOT, gamma_regime=_EX.gamma_regime, da_gex=_EX.da_gex_signed,
        gamma=g, wings=ws,
        best_eligible={"side": "CALL_CREDIT", "short": 5815, "long": 5820,
                       "score": 0.64, "credit": 0.60, "reason": "balanced winner"})
    for k in ("structure_read", "trade_bias", "candidate_risk",
              "best_eligible_setup", "why_why_not"):
        assert dl[k]
    assert "primary gamma" in dl["structure_read"].lower()
    assert "negative" in dl["trade_bias"].lower()
    assert "CALL_CREDIT" in dl["best_eligible_setup"]
    # DDOI must never appear in the operator decision copy
    assert "ddoi" not in " ".join(dl.values()).lower()


def test_decision_layer_gamma_unavailable_text():
    g = ch.primary_secondary_gamma(ExposureContext(), SPOT)
    ws = ch.wing_stack(ExposureContext(), SPOT)
    dl = ch.operator_decision_layer(spot=SPOT, gamma_regime=None, da_gex=None,
                                    gamma=g, wings=ws, best_eligible=None)
    assert "unavailable" in dl["structure_read"].lower()
    assert "unavailable" in dl["trade_bias"].lower()
    assert "no eligible setup" in dl["best_eligible_setup"].lower()


def test_decision_layer_no_chain():
    g = ch.primary_secondary_gamma(_EX, SPOT)
    ws = ch.wing_stack(_EX, SPOT)
    dl = ch.operator_decision_layer(spot=SPOT, gamma_regime="negative", da_gex=-1.0,
                                    gamma=g, wings=ws, best_eligible=None,
                                    chain_available=False)
    assert "quote chain unavailable" in dl["best_eligible_setup"].lower()


def test_fmt_distance():
    assert ch.fmt_distance(5.0) == "+5"
    assert ch.fmt_distance(-12.0) == "-12"
    assert ch.fmt_distance(None) == "—"


# ── run/selection mismatch ───────────────────────────────────────────────────

def test_run_profile_mismatch():
    mm = om.run_profile_mismatch("morning_5k_dynamic_tp75", "vertical_wing_best_credit_1dte")
    assert mm["mismatch"] is True
    assert "different profile" in mm["message"]
    assert om.run_profile_mismatch("a", "a")["mismatch"] is False
    assert om.run_profile_mismatch("a", None)["mismatch"] is False


# ── profile grouping by purpose ──────────────────────────────────────────────

def test_profile_category_mapping():
    # Phase 9I — trader-friendly relabel
    assert om.profile_category("dynamic") == "Main Strategies"
    assert om.profile_category("control") == "Comparison Tests"
    assert om.profile_category("regime") == "Research / Disabled"
    assert om.profile_category("observe") == "Research / Disabled"
    assert om.profile_category(None) == "Legacy / Archived"


def test_group_profiles_primary_first():
    sums = [
        {"profile_id": "vertical_wing_no_trade", "preset_kind": None},
        {"profile_id": "regime_put_credit_test", "preset_kind": "regime"},
        {"profile_id": "eod_5k_call_tp50_control", "preset_kind": "control"},
        {"profile_id": "morning_5k_dynamic_tp75", "preset_kind": "dynamic"},
    ]
    grouped = om.group_profiles_by_category(sums)
    assert grouped[0][0] == "Main Strategies"
    assert grouped[0][1] == ["morning_5k_dynamic_tp75"]
    cats = [c for c, _ in grouped]
    assert cats == ["Main Strategies", "Comparison Tests", "Research / Disabled", "Legacy / Archived"]
    assert om.profiles_in_category(sums, "Comparison Tests") == ["eod_5k_call_tp50_control"]
    assert om.DEFAULT_SIMPLE_CATEGORY == "Main Strategies"
