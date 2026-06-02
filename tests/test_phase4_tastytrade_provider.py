"""Phase 4 — TastytradeQuoteProvider + QuoteValidation + factory tests.

Scope (intentionally narrow):
  * QuoteValidation thresholds enforce crossed / zero-bid / wide / stale
  * validation_from_env reads TASTY_QUOTE_* env vars
  * Factory precedence: CLI > env > YAML > mock; null + unknown handling
  * Factory raises on Tasty misconfig when fallback_on_misconfig=False
  * TastytradeQuoteProvider constructor strict-mode failure
  * TastytradeQuoteProvider.get_option_chain happy path via monkey-patched
    probe — no real HTTP, no real Tasty creds
  * Validation result is attached to each leg (passed + reason)
  * Root resolution metadata rides on the returned chain
  * Provider exposes NO order paths
  * _candidate_row(...) populates the new Phase 4 columns
  * Scanner CLI accepts --quote-provider tastytrade as a choice

No live broker calls. All HTTP is mocked at the probe boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.providers.quotes.factory import (
    VALID_QUOTE_PROVIDER_NAMES,
    build_quote_provider,
    resolve_quote_provider_name,
)
from src.providers.quotes.mock_provider import MockQuoteProvider
from src.providers.quotes.null_provider import NullQuoteProvider
from src.providers.quotes.tasty_probe import TastyProbeConfig
from src.providers.quotes.tastytrade_provider import (
    TastytradeConfigurationError,
    TastytradeQuoteProvider,
    validation_from_env,
)
from src.providers.quotes.types import (
    OptionQuote,
    OptionType,
    QuoteRequest,
    QuoteValidation,
)

# ─────────────────────────────────────────────────────────────────────────
# QuoteValidation thresholds
# ─────────────────────────────────────────────────────────────────────────

def _q(bid: float | None, ask: float | None, *, mid: float | None = None,
       quote_time: datetime | None = None) -> OptionQuote:
    return OptionQuote(
        underlying="SPXW",
        expiry="2026-06-01",
        option_type=OptionType.CALL,
        strike=5800.0,
        bid=bid, ask=ask, mid=mid,
        volume=0, open_interest=0,
        quote_time=quote_time or datetime(2026, 6, 1, 16, 0, tzinfo=UTC),
    )


class TestQuoteValidation:
    def test_good_quote_passes(self):
        v = QuoteValidation()
        ok, reason = v.validate(_q(0.50, 0.60, mid=0.55))
        assert ok is True
        assert reason is None

    def test_missing_bid_or_ask_rejected(self):
        v = QuoteValidation()
        ok, reason = v.validate(_q(None, 0.60))
        assert ok is False
        assert reason == "missing_bid_or_ask"
        ok, reason = v.validate(_q(0.50, None))
        assert ok is False
        assert reason == "missing_bid_or_ask"

    def test_crossed_market_rejected(self):
        v = QuoteValidation()
        ok, reason = v.validate(_q(0.60, 0.50))   # bid > ask
        assert ok is False
        assert reason and reason.startswith("crossed_market")

    def test_zero_bid_rejected(self):
        v = QuoteValidation()
        ok, reason = v.validate(_q(0.0, 0.10))
        assert ok is False
        assert reason == "zero_bid"

    def test_spread_abs_rejected(self):
        # bid=1.00, ask=10.00 → spread=9.00 > default max_abs=5.00
        v = QuoteValidation()
        ok, reason = v.validate(_q(1.00, 10.00, mid=5.50))
        assert ok is False
        assert reason and reason.startswith("spread_abs")

    def test_spread_pct_rejected(self):
        # spread=1.0, mid=1.0 → 100% > default 50%
        v = QuoteValidation()
        ok, reason = v.validate(_q(0.50, 1.50, mid=1.0))
        assert ok is False
        assert reason and reason.startswith("spread_pct")

    def test_stale_quote_rejected(self):
        v = QuoteValidation(max_age_seconds=10.0)
        now = datetime(2026, 6, 1, 16, 0, tzinfo=UTC)
        old = now - timedelta(seconds=60)
        ok, reason = v.validate(_q(0.50, 0.60, mid=0.55, quote_time=old), now=now)
        assert ok is False
        assert reason and reason.startswith("stale")

    def test_disabling_a_check_lets_it_pass(self):
        # Default rejects zero_bid AND crossed. Disable both, then verify
        # quotes that would ONLY fail those checks now pass.
        v = QuoteValidation(reject_zero_bid=False, reject_crossed_market=False)
        # bid=0 / ask=0.10 with explicit mid=0.50 → spread 0.10, pct = 20% (< 50%),
        # abs (< 5.00). No zero-bid check, no crossed-market check → passes.
        ok, reason = v.validate(_q(0.0, 0.10, mid=0.50))
        assert ok is True, reason
        # Crossed (bid=0.60 > ask=0.50) with mid=0.55 → spread = -0.10 so abs/pct
        # are no-ops; with crossed check off → passes.
        ok, reason = v.validate(_q(0.60, 0.50, mid=0.55))
        assert ok is True, reason


# ─────────────────────────────────────────────────────────────────────────
# validation_from_env
# ─────────────────────────────────────────────────────────────────────────

class TestValidationFromEnv:
    def test_defaults_when_unset(self, monkeypatch):
        for k in ("TASTY_QUOTE_MAX_AGE_SECONDS", "TASTY_QUOTE_MAX_SPREAD_PCT",
                  "TASTY_QUOTE_MAX_SPREAD_ABS", "TASTY_REJECT_ZERO_BID",
                  "TASTY_REJECT_CROSSED_MARKET"):
            monkeypatch.delenv(k, raising=False)
        v = validation_from_env()
        assert v == QuoteValidation()

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("TASTY_QUOTE_MAX_AGE_SECONDS", "30")
        monkeypatch.setenv("TASTY_QUOTE_MAX_SPREAD_PCT", "0.10")
        monkeypatch.setenv("TASTY_QUOTE_MAX_SPREAD_ABS", "2.5")
        monkeypatch.setenv("TASTY_REJECT_ZERO_BID", "false")
        monkeypatch.setenv("TASTY_REJECT_CROSSED_MARKET", "1")
        v = validation_from_env()
        assert v.max_age_seconds == 30.0
        assert v.max_spread_pct == 0.10
        assert v.max_spread_abs == 2.5
        assert v.reject_zero_bid is False
        assert v.reject_crossed_market is True

    def test_garbage_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("TASTY_QUOTE_MAX_AGE_SECONDS", "not-a-number")
        v = validation_from_env()
        assert v.max_age_seconds == 10.0


# ─────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────

class TestQuoteProviderFactory:
    def test_default_is_mock(self, monkeypatch):
        monkeypatch.delenv("QUOTE_PROVIDER", raising=False)
        prov, name = build_quote_provider()
        assert name == "mock"
        assert isinstance(prov, MockQuoteProvider)

    def test_env_picks_null(self, monkeypatch):
        monkeypatch.setenv("QUOTE_PROVIDER", "null")
        prov, name = build_quote_provider()
        assert name == "null"
        assert isinstance(prov, NullQuoteProvider)

    def test_yaml_used_when_no_env_or_cli(self, monkeypatch):
        monkeypatch.delenv("QUOTE_PROVIDER", raising=False)
        prov, name = build_quote_provider(yaml_active="null")
        assert name == "null"
        assert isinstance(prov, NullQuoteProvider)

    def test_cli_override_beats_env(self, monkeypatch):
        monkeypatch.setenv("QUOTE_PROVIDER", "null")
        prov, name = build_quote_provider(override="mock")
        assert name == "mock"
        assert isinstance(prov, MockQuoteProvider)

    def test_unknown_name_warns_and_falls_back_to_mock(self, monkeypatch):
        monkeypatch.delenv("QUOTE_PROVIDER", raising=False)
        prov, name = build_quote_provider(override="banana")
        assert name == "mock"
        assert isinstance(prov, MockQuoteProvider)

    def test_resolve_quote_provider_name_precedence(self, monkeypatch):
        monkeypatch.setenv("QUOTE_PROVIDER", "null")
        assert resolve_quote_provider_name("tastytrade") == "tastytrade"
        assert resolve_quote_provider_name(None, yaml_active="mock") == "null"
        monkeypatch.delenv("QUOTE_PROVIDER", raising=False)
        assert resolve_quote_provider_name(None, yaml_active="mock") == "mock"
        assert resolve_quote_provider_name(None) == "mock"

    def test_valid_names_includes_tastytrade(self):
        assert "tastytrade" in VALID_QUOTE_PROVIDER_NAMES
        assert "mock" in VALID_QUOTE_PROVIDER_NAMES
        assert "null" in VALID_QUOTE_PROVIDER_NAMES

    def test_tastytrade_unconfigured_raises_by_default(self, monkeypatch):
        for k in ("TASTY_CLIENT_ID", "TASTY_CLIENT_SECRET", "TASTY_REFRESH_TOKEN",
                  "TASTY_USERNAME", "TASTY_PASSWORD"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("QUOTE_PROVIDER", "tastytrade")
        with pytest.raises(TastytradeConfigurationError):
            build_quote_provider()

    def test_tastytrade_unconfigured_falls_back_when_allowed(self, monkeypatch):
        for k in ("TASTY_CLIENT_ID", "TASTY_CLIENT_SECRET", "TASTY_REFRESH_TOKEN",
                  "TASTY_USERNAME", "TASTY_PASSWORD"):
            monkeypatch.delenv(k, raising=False)
        prov, name = build_quote_provider(
            override="tastytrade", fallback_on_misconfig=True,
        )
        assert name == "mock"
        assert isinstance(prov, MockQuoteProvider)


# ─────────────────────────────────────────────────────────────────────────
# TastytradeQuoteProvider — construction
# ─────────────────────────────────────────────────────────────────────────

def _configured_oauth_config() -> TastyProbeConfig:
    """A `TastyProbeConfig` with OAuth fields populated — enough to satisfy
    `.is_configured()` so we don't trip the strict guard."""
    return TastyProbeConfig(
        env="certification",
        base_url="https://api.cert.tastyworks.com",
        client_id="cid-test",
        client_secret="secret-test",
        refresh_token="refresh-test",
        scopes=["read"],
        allow_trade_scope=False,
    )


class TestProviderConstruction:
    def test_strict_raises_when_unconfigured(self):
        empty = TastyProbeConfig()
        with pytest.raises(TastytradeConfigurationError) as exc:
            TastytradeQuoteProvider(tasty_config=empty, strict=True)
        # Must NEVER leak secret VALUES — env-var NAMES like TASTY_PASSWORD
        # are fine (they're hints to the operator), so we check for the
        # canned secret values used elsewhere in tests.
        msg = str(exc.value).lower()
        for forbidden in ("secret-test", "refresh-test", "cid-test", "bearer "):
            assert forbidden not in msg

    def test_strict_skips_secret_values_in_error(self):
        # Even with a partially-configured cfg (no usable mode), the error
        # must not echo the partial secret value back.
        partial = TastyProbeConfig(client_id="cid-test")  # missing secret + refresh
        with pytest.raises(TastytradeConfigurationError) as exc:
            TastytradeQuoteProvider(tasty_config=partial, strict=True)
        assert "cid-test" not in str(exc.value).lower()

    def test_strict_off_defers_failure(self):
        empty = TastyProbeConfig()
        # Should NOT raise — auth attempted later
        TastytradeQuoteProvider(tasty_config=empty, strict=False)

    def test_does_not_expose_order_methods(self):
        prov = TastytradeQuoteProvider(
            tasty_config=_configured_oauth_config(), strict=True,
        )
        for forbidden in ("submit_order", "preview_order", "place_order",
                          "submit", "dry_run_vertical"):
            assert not hasattr(prov, forbidden), (
                f"Provider must NOT expose {forbidden!r} in Phase 4"
            )


# ─────────────────────────────────────────────────────────────────────────
# TastytradeQuoteProvider — get_option_chain happy path
# ─────────────────────────────────────────────────────────────────────────

class _FakeProbe:
    """Drop-in for `TastyProbeClient` — only the three methods the
    production provider actually calls. Lets tests exercise the parsing /
    validation pipeline without any HTTP."""

    def __init__(self, *, quotes_payload):
        self.quotes_payload = quotes_payload
        self.login_calls   = 0
        self.resolve_calls: list[tuple[str, str]] = []
        self.fetch_calls:   list[list[str]] = []

    def login(self):
        self.login_calls += 1
        return {"auth_success": True, "auth_mode": "oauth", "http_status": 200}

    def resolve_root_for(self, symbol: str, expiry: str):
        self.resolve_calls.append((symbol, expiry))
        return {
            "ok": True,
            "root_symbol": "SPXW",
            "source": "auto_chain",
            "available_roots": ["SPX", "SPXW"],
        }

    def get_option_quotes(self, occ_symbols: list[str]):
        self.fetch_calls.append(list(occ_symbols))
        return {"ok": True, "http_status": 200, "quotes": self.quotes_payload}


def _occ(strike_milli: int, right: str) -> str:
    """Pad SPXW   YYMMDD R STRIKE(8) → 21 chars. Strike is in milli-dollars."""
    return f"SPXW  260601{right}{strike_milli:08d}"


def _make_provider_with_fake_probe(*, quotes_payload):
    prov = TastytradeQuoteProvider(
        tasty_config=_configured_oauth_config(),
        validation=QuoteValidation(),
        strict=True,
    )
    fake = _FakeProbe(quotes_payload=quotes_payload)
    prov._probe = fake             # type: ignore[attr-defined]
    return prov, fake


class TestGetOptionChain:
    def test_requires_required_strikes(self):
        prov, _ = _make_provider_with_fake_probe(quotes_payload=[])
        # Without required_strikes → None + log warning
        out = prov.get_option_chain(
            "SPX", expiry="2026-06-01",
            request=QuoteRequest(symbol="SPX", expiry="2026-06-01"),
        )
        assert out is None

    def test_happy_path_returns_chain_with_validation_and_root_meta(self):
        # Two strikes; emit good-bid/ask quotes for all 4 sides.
        payload = [
            {"symbol": _occ(5_800_000, "C"), "bid": 12.30, "ask": 12.40, "mid": 12.35},
            {"symbol": _occ(5_800_000, "P"), "bid":  8.10, "ask":  8.20, "mid":  8.15},
            {"symbol": _occ(5_820_000, "C"), "bid": 10.10, "ask": 10.20, "mid": 10.15},
            {"symbol": _occ(5_820_000, "P"), "bid":  9.10, "ask":  9.20, "mid":  9.15},
        ]
        prov, fake = _make_provider_with_fake_probe(quotes_payload=payload)
        out = prov.get_option_chain(
            "SPX", expiry="2026-06-01",
            request=QuoteRequest(
                symbol="SPX", expiry="2026-06-01",
                required_strikes=(5800.0, 5820.0),
                spot_hint=5810.0,
            ),
        )
        assert out is not None
        assert out.provider_name == "tastytrade"
        assert out.resolved_root_symbol == "SPXW"
        assert out.root_resolution_source == "auto_chain"
        assert out.spot == 5810.0
        assert {q.strike for q in out.quotes} == {5800.0, 5820.0}
        for q in out.quotes:
            assert q.validation_passed is True
            assert q.validation_rejection_reason is None
            assert q.vendor_symbol and q.vendor_symbol.startswith("SPXW")

        # Probe was asked to fetch 8 symbols (2 strikes × 2 sides), sorted
        assert fake.login_calls == 1
        assert fake.resolve_calls == [("SPX", "2026-06-01")]
        assert len(fake.fetch_calls) == 1
        assert len(fake.fetch_calls[0]) == 4   # 2 strikes × 2 sides

    def test_failed_quote_kept_with_rejection_reason(self):
        # Crossed quote — provider keeps it but flags it
        payload = [
            {"symbol": _occ(5_800_000, "C"), "bid": 12.40, "ask": 12.30, "mid": 12.35},
            {"symbol": _occ(5_800_000, "P"), "bid":  8.10, "ask":  8.20, "mid":  8.15},
        ]
        prov, _ = _make_provider_with_fake_probe(quotes_payload=payload)
        out = prov.get_option_chain(
            "SPX", expiry="2026-06-01",
            request=QuoteRequest(
                symbol="SPX", expiry="2026-06-01",
                required_strikes=(5800.0,),
            ),
        )
        assert out is not None
        bad  = next(q for q in out.quotes if q.option_type == OptionType.CALL)
        good = next(q for q in out.quotes if q.option_type == OptionType.PUT)
        assert bad.validation_passed is False
        assert bad.validation_rejection_reason and bad.validation_rejection_reason.startswith("crossed_market")
        assert good.validation_passed is True

    def test_auth_failure_returns_none(self):
        prov, fake = _make_provider_with_fake_probe(quotes_payload=[])

        def _bad_login():
            return {"auth_success": False, "auth_mode": "oauth", "http_status": 401,
                    "reason": "invalid_grant"}

        fake.login = _bad_login   # type: ignore[assignment]
        prov._authed = False      # type: ignore[attr-defined]
        out = prov.get_option_chain(
            "SPX", expiry="2026-06-01",
            request=QuoteRequest(
                symbol="SPX", expiry="2026-06-01",
                required_strikes=(5800.0,),
            ),
        )
        assert out is None

    def test_unresolved_root_returns_none(self):
        prov, fake = _make_provider_with_fake_probe(quotes_payload=[])
        fake.resolve_root_for = lambda *a, **k: {   # type: ignore[assignment]
            "ok": False, "reason": "no_chain_for_expiry",
            "available_roots": ["SPX"],
        }
        out = prov.get_option_chain(
            "SPX", expiry="2099-01-01",
            request=QuoteRequest(
                symbol="SPX", expiry="2099-01-01",
                required_strikes=(5800.0,),
            ),
        )
        assert out is None


# ─────────────────────────────────────────────────────────────────────────
# Status + heartbeat
# ─────────────────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_reports_safety_gate_off(self):
        prov, _ = _make_provider_with_fake_probe(quotes_payload=[])
        s = prov.status()
        assert s.provider_name == "tastytrade"
        assert "execution_blocked=true" in (s.notes or "")
        assert "probe_exposes_submit_path=false" in (s.notes or "")


# ─────────────────────────────────────────────────────────────────────────
# Candidate row emits the new Phase 4 columns
# ─────────────────────────────────────────────────────────────────────────

def test_candidate_row_includes_phase4_quote_columns():
    """End-to-end: build a tiny Candidate-like + chain, call _candidate_row,
    assert new columns appear with the right values."""
    # Import the scanner module — _candidate_row lives at module scope
    import importlib
    from datetime import datetime as _dt

    from src.app.session_state import SessionConfig
    from src.providers.quotes.types import OptionChainSnapshot
    from src.risk.limits import RiskProfile
    rs = importlib.import_module("scripts.run_scanner")

    # Minimal Candidate stand-in. We use the real dataclass.
    from src.strategies.base import Candidate

    quote_time = _dt(2026, 6, 1, 16, 0, 0, tzinfo=UTC)
    c = Candidate(
        strategy_id="vertical_wing_v1",
        side="CALL_CREDIT",
        symbol="SPX",
        expiry="2026-06-01",
        short_strike=5800.0,
        long_strike=5820.0,
        credit=0.55,
        max_risk=19.45,
        reward_risk=0.028,
        breakeven=5800.55,
        distance_from_spot=-10.0,
        meta={
            "short_leg": {
                "bid": 0.50, "ask": 0.60, "mid": 0.55,
                "quote_time": quote_time.isoformat(),
                "validation_passed": True,
                "validation_rejection_reason": None,
            },
            "long_leg": {
                "bid": 0.0, "ask": 0.10, "mid": 0.05,
                "quote_time": quote_time.isoformat(),
                "validation_passed": False,
                "validation_rejection_reason": "zero_bid",
            },
        },
    )
    chain = OptionChainSnapshot(
        underlying="SPX",
        spot=5810.0,
        expiry="2026-06-01",
        quotes=[],
        quote_ts=quote_time,
        provider_name="tastytrade",
        resolved_root_symbol="SPXW",
        root_resolution_source="auto_chain",
    )
    session = SessionConfig.from_profile(RiskProfile(
        name="t",
        raw={"starting_balance": 10_000, "contracts_per_trade": 1,
             "default_stop_variant": "BASELINE_CASH_SETTLE"},
    ))
    # ts AFTER the quote_time so age math is non-negative
    ts = quote_time + timedelta(seconds=3)
    row = rs._candidate_row("vertical_wing_v1", c, session, ts, "NO_TRADE", chain=chain)

    assert row["quote_provider"] == "tastytrade"
    assert row["quote_chain_root"] == "SPXW"
    assert row["quote_root_resolution_source"] == "auto_chain"
    assert row["short_validation_passed"] is True
    assert row["short_rejection_reason"] is None
    assert row["long_validation_passed"] is False
    assert row["long_rejection_reason"] == "zero_bid"
    # Overall = False because the LONG leg failed
    assert row["quote_validation_passed"] is False
    assert "zero_bid" in (row["quote_rejection_reason"] or "")
    # Age must be present and ~3s
    assert row["quote_age_seconds"] is not None
    assert 2.0 <= row["quote_age_seconds"] <= 4.0


def test_candidate_row_unvalidated_legs_overall_is_none():
    """Mock chain leaves validation_passed=None on every leg. The CSV must
    not pretend that's a pass — overall should be None."""
    import importlib
    from datetime import datetime as _dt

    from src.app.session_state import SessionConfig
    from src.providers.quotes.types import OptionChainSnapshot
    from src.risk.limits import RiskProfile
    from src.strategies.base import Candidate
    rs = importlib.import_module("scripts.run_scanner")

    c = Candidate(
        strategy_id="vertical_wing_v1",
        side="CALL_CREDIT",
        symbol="SPX",
        expiry="2026-06-01",
        short_strike=5800.0,
        long_strike=5820.0,
        credit=0.55, max_risk=19.45, reward_risk=0.028,
        breakeven=5800.55, distance_from_spot=-10.0,
        meta={
            "short_leg": {"bid": 0.50, "ask": 0.60, "mid": 0.55},
            "long_leg":  {"bid": 0.05, "ask": 0.15, "mid": 0.10},
        },
    )
    qt = _dt(2026, 6, 1, 16, 0, 0, tzinfo=UTC)
    chain = OptionChainSnapshot(
        underlying="SPX", spot=5810.0, expiry="2026-06-01",
        quotes=[], quote_ts=qt, provider_name="mock",
    )
    session = SessionConfig.from_profile(RiskProfile(
        name="t",
        raw={"starting_balance": 10_000, "contracts_per_trade": 1,
             "default_stop_variant": "BASELINE_CASH_SETTLE"},
    ))
    row = rs._candidate_row("vertical_wing_v1", c, session, qt, "NO_TRADE", chain=chain)
    assert row["quote_provider"] == "mock"
    assert row["quote_validation_passed"] is None
    assert row["quote_rejection_reason"] is None
    assert row["short_validation_passed"] is None
    assert row["long_validation_passed"]  is None


# ─────────────────────────────────────────────────────────────────────────
# Scanner CLI surface
# ─────────────────────────────────────────────────────────────────────────

def test_scanner_cli_accepts_quote_provider_choice():
    """argparse should accept all three quote-provider names."""
    import os
    import subprocess
    import sys
    # --help exits 0 and prints choices; if argparse rejected our addition
    # this would fail. Force UTF-8 so the σ in the scanner's description
    # doesn't crash Windows cp1252 stdout.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"]       = "1"
    out = subprocess.run(
        [sys.executable, "-m", "scripts.run_scanner", "--help"],
        capture_output=True, text=True, timeout=30, env=env,
        encoding="utf-8", errors="replace",
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    assert "--quote-provider" in out.stdout
    for name in ("mock", "null", "tastytrade"):
        assert name in out.stdout
