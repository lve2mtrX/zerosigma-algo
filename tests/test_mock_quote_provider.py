"""MockQuoteProvider chain + spot determinism + chain shape."""

from __future__ import annotations

from src.providers.quotes.mock_provider import MockQuoteProvider
from src.providers.quotes.types import (
    OptionChainSnapshot,
    OptionQuote,
    OptionType,
    QuoteProviderStatus,
    SpreadQuote,
)


def test_get_spot_returns_spot_quote_with_timestamp():
    p = MockQuoteProvider()
    s = p.get_spot("SPX")
    assert s is not None
    assert s.symbol == "SPX"
    assert s.last == 5800.0
    assert s.ts is not None


def test_get_option_chain_returns_full_call_and_put_chain():
    p = MockQuoteProvider()
    chain = p.get_option_chain("SPX")
    assert isinstance(chain, OptionChainSnapshot)
    assert chain.underlying == "SPX"
    assert chain.spot == 5800.0
    assert chain.provider_name == "mock"
    # both sides present at every strike
    strikes = chain.strikes()
    assert len(strikes) >= 8
    for k in strikes:
        c = chain.find(k, OptionType.CALL)
        pt = chain.find(k, OptionType.PUT)
        assert c is not None and pt is not None
        # bid/ask/mid populated
        assert c.bid is not None and c.ask is not None and c.mid is not None
        assert pt.bid is not None and pt.ask is not None and pt.mid is not None
        # volume + OI present
        assert c.volume is not None and c.open_interest is not None


def test_chain_is_deterministic_across_calls():
    p1 = MockQuoteProvider().get_option_chain("SPX")
    p2 = MockQuoteProvider().get_option_chain("SPX")
    assert p1 is not None and p2 is not None
    # same set of strikes + same mid prices each time
    mids_1 = {(q.strike, str(q.option_type)): q.mid for q in p1.quotes}
    mids_2 = {(q.strike, str(q.option_type)): q.mid for q in p2.quotes}
    assert mids_1 == mids_2


def test_chain_credit_changes_when_mock_data_would_change():
    """Sanity: spread credit IS derived from quote mids, not from structure."""
    p = MockQuoteProvider()
    chain = p.get_option_chain("SPX")
    assert chain is not None
    short_call = chain.find(5815, OptionType.CALL)
    long_call  = chain.find(5820, OptionType.CALL)
    assert short_call is not None and long_call is not None
    expected_credit = short_call.mid - long_call.mid
    spread = SpreadQuote.from_legs(short_call, long_call)
    assert abs(spread.credit_mid - expected_credit) < 1e-9


def test_provider_status_reflects_recent_reads():
    p = MockQuoteProvider()
    status_before: QuoteProviderStatus = p.status()
    assert status_before.connected is True
    assert status_before.last_chain_ts is None  # no reads yet
    p.get_option_chain("SPX")
    status_after = p.status()
    assert status_after.last_chain_ts is not None


def test_get_option_quote_back_compat_returns_optionquote():
    p = MockQuoteProvider()
    q = p.get_option_quote("SPX", "2026-06-01", 5815.0, "C")
    assert isinstance(q, OptionQuote)
    assert q.option_type == OptionType.CALL
    assert q.strike == 5815.0
    assert q.bid is not None and q.ask is not None
