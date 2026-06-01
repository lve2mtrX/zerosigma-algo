"""Shared deterministic mock SPX 0DTE dataset.

Both `StubStructureProvider` (which derives structure levels — MaxVol,
PUT_CEILING, CALL_FLOOR — from intraday volume) and `MockQuoteProvider`
(which serves bid/ask/mid) read from this single source.

Keeping the dataset here — and NOT inside either provider module — preserves
the architectural rule: the structure provider must not know about the
quote provider's internals (or vice versa). They merely happen to agree
because we, the test harness, fed them from the same canonical data.

Constants are chosen so:
  - PUT_CEILING(2K) = 5815, PUT_CEILING(5K) = 5810
  - CALL_FLOOR(2K)  = 5785, CALL_FLOOR(5K)  = 5790
  - At least one CALL_CREDIT candidate passes filters under aggressive_paper_10k
  - At least one PUT_CREDIT candidate passes filters under aggressive_paper_10k
  - Near-the-money strikes (5800, 5805) carry wide bid/ask + thin volume
    so adding a bid/ask-width filter later has something to reject.
"""

from __future__ import annotations

from dataclasses import dataclass

SPOT = 5800.0


@dataclass(frozen=True)
class MockStrikeRow:
    """One strike's worth of mock data, BOTH sides.

    The bid/ask are derived symmetrically from mid by `bid_ask_width / 2`
    when the quote provider builds OptionQuotes; this struct just records
    the canonical mid + volume so structure and quote providers agree.
    """
    strike:        float
    c_mid:         float | None
    p_mid:         float | None
    c_volume:      float
    p_volume:      float
    c_open_interest: float
    p_open_interest: float
    bid_ask_width: float = 0.10
    # Optional Greek hints (broker chains carry these)
    c_iv:    float | None = 0.16
    p_iv:    float | None = 0.18
    c_delta: float | None = 0.30
    p_delta: float | None = -0.30
    c_gamma: float | None = 0.01
    p_gamma: float | None = 0.01


# Deterministic 5-pt grid from 5780 to 5830 around spot 5800.
# Volumes wired so 2K/5K PUT_CEILING and CALL_FLOOR resolve to different strikes.
MOCK_CHAIN: tuple[MockStrikeRow, ...] = (
    # ── below spot — CALL_FLOOR territory ──
    MockStrikeRow(5780, c_mid=18.50, p_mid=0.85,  c_volume=300,  p_volume=200,  c_open_interest=900, p_open_interest=1100),
    MockStrikeRow(5785, c_mid=14.20, p_mid=1.60,  c_volume=2200, p_volume=300,  c_open_interest=1200, p_open_interest=1000),  # 2K CALL_FLOOR
    MockStrikeRow(5790, c_mid=10.40, p_mid=2.50,  c_volume=5400, p_volume=400,  c_open_interest=1800, p_open_interest=1200),  # also 5K-qualifying
    MockStrikeRow(5795, c_mid=6.80,  p_mid=3.40,  c_volume=600,  p_volume=500,  c_open_interest=1500, p_open_interest=1400),
    # ── at-the-money — thin, wide bid/ask ──
    MockStrikeRow(5800, c_mid=3.60,  p_mid=3.60,  c_volume=120,  p_volume=120,  c_open_interest=2200, p_open_interest=2300, bid_ask_width=0.50),
    MockStrikeRow(5805, c_mid=1.95,  p_mid=4.95,  c_volume=200,  p_volume=180,  c_open_interest=1700, p_open_interest=1800, bid_ask_width=0.40),
    # ── above spot — PUT_CEILING territory ──
    MockStrikeRow(5810, c_mid=1.80,  p_mid=8.80,  c_volume=400,  p_volume=5500, c_open_interest=1300, p_open_interest=2400),  # 5K PUT_CEILING (also 2K-qualifying)
    MockStrikeRow(5815, c_mid=1.10,  p_mid=13.40, c_volume=350,  p_volume=4500, c_open_interest=1100, p_open_interest=2100),  # 2K PUT_CEILING (highest @2K)
    MockStrikeRow(5820, c_mid=0.50,  p_mid=18.20, c_volume=250,  p_volume=400,  c_open_interest=950,  p_open_interest=1500),
    MockStrikeRow(5825, c_mid=0.20,  p_mid=23.10, c_volume=180,  p_volume=300,  c_open_interest=800,  p_open_interest=1300),
    MockStrikeRow(5830, c_mid=0.10,  p_mid=28.05, c_volume=150,  p_volume=200,  c_open_interest=700,  p_open_interest=1100),
)


# ──────────────────────────────────────────────────────────────────────
# Pure helpers — used by BOTH providers to derive consistent answers.
# These do NOT touch wall-clock state.
# ──────────────────────────────────────────────────────────────────────

def highest_strike_where_put_volume_ge(threshold: float) -> float | None:
    qualifying = [r.strike for r in MOCK_CHAIN if r.p_volume >= threshold]
    return max(qualifying) if qualifying else None


def lowest_strike_where_call_volume_ge(threshold: float) -> float | None:
    qualifying = [r.strike for r in MOCK_CHAIN if r.c_volume >= threshold]
    return min(qualifying) if qualifying else None


def maxvol_strike() -> float:
    best = max(MOCK_CHAIN, key=lambda r: r.c_volume + r.p_volume)
    return best.strike
