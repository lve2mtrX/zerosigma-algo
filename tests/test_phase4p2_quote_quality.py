"""Phase 4.2 — relative-aware bid_ask_quality + bucket agreement.

NO network, NO Tasty creds. Three layers:

  1. the PURE shared module src.utils.quote_quality (score endpoints,
     absolute-mode legacy, crossed-leg floor, bucket cutoffs);
  2. bucket + score AGREEMENT — the SAME pct cutoffs drive both, so the live
     4.1 contradiction (PASSED validation yet bid_ask_quality=0.00 with
     bucket='poor') cannot recur;
  3. candidates.py STAMPING — the strategy stamps bid_ask_quality (+ mode /
     reason) and quote_quality_bucket (+ reason) into Candidate.meta.

The headline motivation: a worst leg that is 0.20 WIDE in absolute dollars
scored 0.0 under the old absolute $0.20 cap, even when 0.20 is only ~6.5% of
the leg's mid. Relative mode scores that ~0.63 (bucket 'acceptable').
"""

from __future__ import annotations

import math

import pytest

from src.providers.quotes.types import (
    OptionChainSnapshot,
    OptionQuote,
    OptionType,
)
from src.strategies.vertical_wing.candidates import build_put_ceiling_call_credit
from src.utils.quote_quality import (
    DEFAULT_ACCEPTABLE_PCT,
    DEFAULT_GOOD_PCT,
    DEFAULT_POOR_PCT,
    absolute_score,
    bid_ask_quality,
    quality_bucket,
    relative_score,
)

# ── pure relative_score endpoints (locked per the Phase 4.2 brief) ────────

class TestRelativeScoreEndpoints:
    def test_at_or_below_good_is_one(self):
        assert relative_score(0.0) == 1.0
        assert relative_score(0.01) == 1.0
        assert relative_score(DEFAULT_GOOD_PCT) == 1.0  # 3% boundary

    def test_good_to_acceptable_segment_linear_0p8_to_0p6(self):
        # Just past good → ~0.8; at the acceptable boundary → exactly 0.6.
        assert relative_score(0.0300001) == pytest.approx(0.8, abs=1e-3)
        assert relative_score(0.05) == pytest.approx(0.7, abs=1e-9)  # midpoint
        assert relative_score(DEFAULT_ACCEPTABLE_PCT) == pytest.approx(0.6, abs=1e-9)

    def test_acceptable_to_poor_segment_linear_0p5_to_0p2(self):
        # Just past acceptable → ~0.5; at the poor boundary → exactly 0.2.
        assert relative_score(0.0700001) == pytest.approx(0.5, abs=1e-3)
        assert relative_score(0.11) == pytest.approx(0.35, abs=1e-9)  # midpoint
        assert relative_score(DEFAULT_POOR_PCT) == pytest.approx(0.2, abs=1e-9)

    def test_above_poor_is_zero(self):
        assert relative_score(0.150001) == 0.0
        assert relative_score(0.30) == 0.0

    def test_none_or_negative_is_zero(self):
        assert relative_score(None) == 0.0
        assert relative_score(-0.01) == 0.0

    def test_live_finding_tight_relative_spread_scores_nonzero(self):
        # The Phase 4.1 live case: worst leg 0.20 WIDE on a ~3.10 mid →
        # pct 0.0645 (6.45%). Under the OLD absolute $0.20 cap this scored
        # 0.0; relative mode scores it ~0.63 (in the 0.8..0.6 band).
        score = relative_score(0.0645)
        assert score > 0.0
        assert score == pytest.approx(0.6275, abs=1e-3)


# ── absolute_score (legacy mode, preserved) ───────────────────────────────

class TestAbsoluteScore:
    def test_legacy_linear_cap(self):
        # 1 - worst/cap; cap 0.20 reproduces the Phase 4.1 default.
        assert absolute_score(0.0, max_abs_cap=0.20) == 1.0
        assert absolute_score(0.10, max_abs_cap=0.20) == pytest.approx(0.5)
        assert absolute_score(0.20, max_abs_cap=0.20) == 0.0
        assert absolute_score(0.40, max_abs_cap=0.20) == 0.0  # clamped

    def test_cap_le_zero_branch_returns_half(self):
        # Preserves the legacy candidates._bid_ask_quality_score cap<=0 → 0.5.
        assert absolute_score(0.10, max_abs_cap=0.0) == 0.5

    def test_none_worst_is_zero(self):
        assert absolute_score(None, max_abs_cap=0.20) == 0.0


# ── bid_ask_quality dispatcher (mode + reason + crossed-leg floor) ────────

class TestBidAskQualityDispatcher:
    def test_crossed_or_missing_leg_is_hard_zero_invalid(self):
        # worst_abs None (the strategy couldn't price a leg) → hard 0.0 floor,
        # mode 'invalid', explicit reason. Preserves today's behavior.
        score, mode, reason = bid_ask_quality(worst_abs=None, worst_pct=None)
        assert score == 0.0
        assert mode == "invalid"
        assert reason == "crossed_or_missing_leg"

    def test_relative_default_mode(self):
        score, mode, reason = bid_ask_quality(worst_abs=0.20, worst_pct=0.0645)
        assert mode == "relative"
        assert score == pytest.approx(0.6275, abs=1e-3)
        assert "acceptable" in reason

    def test_relative_with_no_mid_falls_back_to_absolute(self):
        # worst_pct None (no usable mid) → absolute path so a missing mid never
        # silently zeroes a valid quote. Default mode is still 'relative' but
        # the dispatcher routes to absolute.
        score, mode, reason = bid_ask_quality(
            worst_abs=0.10, worst_pct=None, mode="relative", max_abs_cap=0.20,
        )
        assert mode == "absolute"
        assert score == pytest.approx(0.5)
        assert "abs_cap" in reason

    def test_explicit_absolute_mode_is_selectable(self):
        # The legacy absolute calibration remains opt-in via mode='absolute'.
        # With cap 0.20, a 0.20-wide worst leg scores 0.0 (the OLD behavior) —
        # even though its pct would have scored non-zero in relative mode.
        score, mode, _ = bid_ask_quality(
            worst_abs=0.20, worst_pct=0.0645, mode="absolute", max_abs_cap=0.20,
        )
        assert mode == "absolute"
        assert score == 0.0


# ── quality_bucket cutoffs (pct-of-mid) ───────────────────────────────────

class TestQualityBucketCutoffs:
    def test_bands(self):
        kw = dict(short_passed=True, long_passed=True)
        assert quality_bucket(worst_pct=0.01, **kw)[0] == "good"
        assert quality_bucket(worst_pct=0.03, **kw)[0] == "good"
        assert quality_bucket(worst_pct=0.05, **kw)[0] == "acceptable"
        assert quality_bucket(worst_pct=0.07, **kw)[0] == "acceptable"
        assert quality_bucket(worst_pct=0.12, **kw)[0] == "poor"
        assert quality_bucket(worst_pct=0.15, **kw)[0] == "poor"
        assert quality_bucket(worst_pct=0.20, **kw)[0] == "wide"

    def test_validator_failure_short_circuits_to_invalid(self):
        # A failed leg wins over any pct band.
        bucket, reason = quality_bucket(
            worst_pct=0.01, short_passed=False, long_passed=True,
        )
        assert bucket == "invalid"
        assert reason == "validation_failed"

    def test_none_pct_is_unknown(self):
        bucket, reason = quality_bucket(
            worst_pct=None, short_passed=None, long_passed=None,
        )
        assert bucket == "unknown"
        assert reason == "no_leg_width"


# ── bucket + score AGREEMENT (the core 4.2 fix) ───────────────────────────

class TestBucketScoreAgreement:
    @pytest.mark.parametrize(
        "pct,expected_bucket",
        [
            (0.01, "good"),
            (0.05, "acceptable"),
            (0.12, "poor"),
            (0.20, "wide"),
        ],
    )
    def test_bucket_and_score_never_contradict(self, pct, expected_bucket):
        """The SAME cutoffs drive both: a 'good'/'acceptable' bucket implies a
        non-zero score, and a 'wide' bucket implies score 0.0. This is exactly
        the live 4.1 contradiction (PASSED validation yet bid_ask_quality=0.00
        with bucket='poor') that 4.2 makes impossible."""
        score, _, _ = bid_ask_quality(worst_abs=0.20, worst_pct=pct)
        bucket, _ = quality_bucket(worst_pct=pct, short_passed=True, long_passed=True)
        assert bucket == expected_bucket
        if bucket in ("good", "acceptable", "poor"):
            assert score > 0.0, f"{bucket} bucket must have a non-zero score"
        else:  # 'wide'
            assert score == 0.0, "wide bucket must score 0.0"


# ── candidates.py STAMPING (no leak; meta carries everything) ─────────────

def _q(strike: float, side: OptionType, bid: float, ask: float) -> OptionQuote:
    from datetime import UTC, datetime
    return OptionQuote(
        underlying="SPX", expiry="2026-06-01", option_type=side, strike=strike,
        bid=bid, ask=ask, mid=(bid + ask) / 2.0,
        volume=1000.0, open_interest=2000.0,
        quote_time=datetime(2026, 6, 1, 14, 0, tzinfo=UTC),
        validation_passed=True, validation_rejection_reason=None,
    )


def _chain_with(short_q: OptionQuote, long_q: OptionQuote) -> OptionChainSnapshot:
    from datetime import UTC, datetime
    return OptionChainSnapshot(
        underlying="SPX", spot=5800.0, expiry="2026-06-01",
        quotes=[short_q, long_q],
        quote_ts=datetime(2026, 6, 1, 14, 0, tzinfo=UTC),
        provider_name="test",
    )


def _structure_with_put_ceiling(strike: float):
    from datetime import datetime

    from src.providers.structure.types import ExposureContext, StructureSnapshot
    return StructureSnapshot(
        symbol="SPX", spot=5800.0,
        quote_ts=datetime(2026, 6, 1, 14, 0),
        exposures=ExposureContext(
            maxvol=5810.0, gamma_regime="positive",
            put_ceiling_2k=strike, call_floor_2k=5785.0,
        ),
        expiry="2026-06-01", dte=0, source="test",
    )


class TestCandidatesStamping:
    def test_relative_default_stamps_mode_reason_bucket(self):
        # short 5815 (mid 1.10, 0.10 wide → 9.1% of mid), long 5820 (mid 0.50,
        # 0.10 wide → 20% of mid). The WORST (wider relative) leg is 5820 at
        # 20% → bucket 'wide', score 0.0. The point: mode/reason/bucket are
        # all STAMPED so readiness can read them without re-deriving.
        short_q = _q(5815.0, OptionType.CALL, bid=1.05, ask=1.15)
        long_q = _q(5820.0, OptionType.CALL, bid=0.45, ask=0.55)
        chain = _chain_with(short_q, long_q)
        c = build_put_ceiling_call_credit(
            _structure_with_put_ceiling(5815.0), chain,
            threshold=2000.0, spread_width=5.0, strategy_id="vw_v1",
        )
        assert c is not None
        assert c.meta["bid_ask_quality_mode"] == "relative"
        assert isinstance(c.meta["bid_ask_quality_reason"], str)
        # bucket + score agree (worst leg 20% → wide / 0.0)
        assert c.meta["quote_quality_bucket"] == "wide"
        assert c.meta["bid_ask_quality"] == 0.0
        assert isinstance(c.meta["quote_quality_reason"], str)

    def test_relative_tight_spread_scores_nonzero_and_good(self):
        # Both legs 0.02 wide on healthy mids → tight relative market.
        short_q = _q(5815.0, OptionType.CALL, bid=1.09, ask=1.11)  # 0.02/1.10 ≈ 1.8%
        long_q = _q(5820.0, OptionType.CALL, bid=2.49, ask=2.51)   # 0.02/2.50 ≈ 0.8%
        chain = _chain_with(short_q, long_q)
        c = build_put_ceiling_call_credit(
            _structure_with_put_ceiling(5815.0), chain,
            threshold=2000.0, spread_width=5.0, strategy_id="vw_v1",
        )
        assert c is not None
        assert c.meta["quote_quality_bucket"] == "good"
        assert c.meta["bid_ask_quality"] == 1.0
        assert c.meta["bid_ask_quality_mode"] == "relative"

    def test_absolute_mode_threaded_through(self):
        # Selecting absolute mode with the legacy 0.20 cap reproduces the OLD
        # calibration regardless of pct. worst leg 0.10 wide → 1 - 0.10/0.20 = 0.5.
        short_q = _q(5815.0, OptionType.CALL, bid=1.05, ask=1.15)  # 0.10 wide
        long_q = _q(5820.0, OptionType.CALL, bid=2.49, ask=2.51)   # 0.02 wide
        chain = _chain_with(short_q, long_q)
        c = build_put_ceiling_call_credit(
            _structure_with_put_ceiling(5815.0), chain,
            threshold=2000.0, spread_width=5.0, strategy_id="vw_v1",
            bid_ask_quality_mode="absolute", max_abs_cap=0.20,
        )
        assert c is not None
        assert c.meta["bid_ask_quality_mode"] == "absolute"
        assert c.meta["bid_ask_quality"] == pytest.approx(0.5)

    def test_validator_failure_buckets_invalid_even_if_tight(self):
        # A leg that failed broker validation → bucket 'invalid' regardless of
        # how tight the pct is.
        short_q = _q(5815.0, OptionType.CALL, bid=1.09, ask=1.11)
        long_q = OptionQuote(
            underlying="SPX", expiry="2026-06-01", option_type=OptionType.CALL,
            strike=5820.0, bid=2.49, ask=2.51, mid=2.50,
            volume=1000.0, open_interest=2000.0,
            quote_time=short_q.quote_time,
            validation_passed=False, validation_rejection_reason="zero_bid",
        )
        chain = _chain_with(short_q, long_q)
        c = build_put_ceiling_call_credit(
            _structure_with_put_ceiling(5815.0), chain,
            threshold=2000.0, spread_width=5.0, strategy_id="vw_v1",
        )
        assert c is not None
        assert c.meta["quote_quality_bucket"] == "invalid"


def test_module_has_no_vertical_wing_substring():
    """The shared module must never contain the literal 'vertical_wing'
    substring (which is what tests/test_no_vw_leak.py flags on import lines).
    This keeps src/selector/readiness.py free to import it."""
    from pathlib import Path
    text = Path("src/utils/quote_quality.py").read_text(encoding="utf-8")
    assert "vertical_wing" not in text


def test_default_cutoffs_are_monotonic():
    assert 0.0 < DEFAULT_GOOD_PCT < DEFAULT_ACCEPTABLE_PCT < DEFAULT_POOR_PCT
    assert math.isclose(DEFAULT_GOOD_PCT, 0.03)
    assert math.isclose(DEFAULT_ACCEPTABLE_PCT, 0.07)
    assert math.isclose(DEFAULT_POOR_PCT, 0.15)
