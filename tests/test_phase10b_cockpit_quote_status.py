"""Phase 10B hotfix — Live Cockpit quote-status reconciliation.

The cockpit must classify its ACTUAL fetched chain into distinct states (never one
generic "unavailable"): a returned-but-validation-blocked Tasty chain is its own
state. Covers the pure `cockpit_quote_status` + `build_quote_request` helpers and
the `diagnose_cockpit_quote_status` CLI parity tool. No network, no secrets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import scripts.diagnose_cockpit_quote_status as cockpit_cli
import src.app.cockpit_helpers as ch
from src.providers.quotes.types import OptionChainSnapshot, OptionQuote, OptionType

_TS = datetime(2026, 6, 4, 14, 30, 0, tzinfo=UTC)


def _q(strike, *, ot=OptionType.CALL, vp=None, reason=None, bid=0.5, ask=0.6, mid=0.55):
    return OptionQuote(
        underlying="SPX", expiry="2026-06-04", option_type=ot, strike=strike,
        bid=bid, ask=ask, mid=mid, volume=None, open_interest=None, quote_time=_TS,
        validation_passed=vp, validation_rejection_reason=reason)


def _chain(quotes, *, root="SPXW", expiry="2026-06-04", provider="tastytrade"):
    return OptionChainSnapshot(
        underlying="SPX", spot=7585.0, expiry=expiry, quotes=quotes, quote_ts=_TS,
        provider_name=provider, resolved_root_symbol=root, root_resolution_source="auto_chain")


def _status(chain, **kw):
    return ch.cockpit_quote_status(symbol="SPX", resolved_quote_name=kw.pop("name", "tastytrade"),
                                   chain=chain, **kw)


# ── the core: chain returned + validation failed is NOT "unavailable" ────────

def test_chain_returned_validation_failed_is_distinct_state():
    quotes = [_q(7600, vp=False, reason="spread_abs(8.00>5.00)", bid=20.0, ask=28.0),
              _q(7605, vp=False, reason="spread_abs(7.00>5.00)", bid=10.0, ask=17.0)]
    s = _status(_chain(quotes), max_spread_abs=5.0)
    assert s["state"] == "chain_returned_validation_failed"
    assert s["label"] == "chain returned / validation blocked"
    assert s["available"] is False and s["eligible_hint"] == "blocked"
    assert "validation blocked" in s["banner"].lower() and "spread_abs" in s["banner"]
    d = s["details"]
    assert d["chain_returned"] is True and d["quote_count"] == 2
    assert d["validation_failed_count"] == 2 and d["validation_passed_count"] == 0
    assert d["top_blocker"] == "spread_abs" and d["observed_failing_spread"] == 8.0
    assert d["max_spread_abs"] == 5.0 and d["root"] == "SPXW"


def test_stale_blocker_recorded():
    quotes = [_q(7600, vp=False, reason="stale(age=30.0s>10s)"),
              _q(7605, vp=False, reason="stale(age=31.0s>10s)")]
    s = _status(_chain(quotes), max_age_seconds=10.0)
    assert s["state"] == "chain_returned_validation_failed"
    assert s["details"]["top_blocker"] == "stale" and s["details"]["max_age_seconds"] == 10.0


def test_chain_returned_usable_is_available():
    s = _status(_chain([_q(7600, vp=True), _q(7605, vp=None)]))   # passed + not-validated → usable
    assert s["state"] == "chain_returned_usable" and s["label"] == "available"
    assert s["available"] is True and s["banner"] is None and s["eligible_hint"] == "yes"


# ── chain-None states map to distinct reasons ────────────────────────────────

def test_auth_failed_state():
    s = _status(None, quote_status=SimpleNamespace(last_error="auth_failed:HTTP 401"))
    assert s["state"] == "auth_failed" and s["label"] == "auth failed" and s["available"] is False


def test_root_unresolved_state():
    s = _status(None, quote_status=SimpleNamespace(last_error="chain_unresolved:chain_unavailable"))
    assert s["state"] == "root_unresolved" and s["label"] == "root unresolved"


def test_expiration_unavailable_state():
    s = _status(None, quote_status=SimpleNamespace(last_error="chain_unresolved:expiry_not_in_chain"))
    assert s["state"] == "expiration_unavailable"


def test_not_configured_state():
    s = _status(None, quote_provider_error="TastytradeConfigurationError: incomplete")
    assert s["state"] == "not_configured" and "not configured" in s["label"]


def test_generic_banner_only_for_true_no_chain():
    s_none = _status(None)
    assert s_none["state"] == "chain_unavailable"
    assert "market may be closed" in s_none["banner"].lower()
    # validation-blocked must NOT reuse the generic no-chain banner
    s_block = _status(_chain([_q(7600, vp=False, reason="stale(...)")]))
    assert "market may be closed" not in (s_block["banner"] or "").lower()


def test_mock_provider_is_available_and_labeled_mock():
    s = _status(_chain([_q(7600, vp=None)], provider="mock"), name="mock")
    assert s["state"] == "mock" and s["label"] == "mock"
    assert s["available"] is True and s["eligible_hint"] == "sandbox" and s["banner"] is None


def test_missing_strikes_recorded():
    s = _status(_chain([_q(7600, vp=True)]), requested_strikes=[7600.0, 7605.0])
    assert s["details"]["missing_strikes"] == [7605.0]


# ── build_quote_request mirrors the scanner's structure-anchored request ──────

def test_build_quote_request_from_structure():
    structure = SimpleNamespace(spot=7585.0, expiry="2026-06-04",
                                exposures=SimpleNamespace(maxvol=7580.0))
    strat = SimpleNamespace(
        required_quote_strikes=lambda s, p: [7600.0, 7605.0, 7550.0],
        default_parameters={})
    req = ch.build_quote_request("SPX", structure, {"vw": strat})
    assert req.symbol == "SPX" and req.expiry == "2026-06-04"
    assert set(req.required_strikes) == {7550.0, 7600.0, 7605.0}
    assert req.spot_hint == 7585.0


def test_build_quote_request_empty_when_no_anchors():
    structure = SimpleNamespace(spot=0.0, expiry="2026-06-04",
                                exposures=SimpleNamespace(maxvol=None))
    strat = SimpleNamespace(required_quote_strikes=lambda s, p: [], default_parameters={})
    req = ch.build_quote_request("SPX", structure, {"vw": strat})
    assert req.required_strikes == () and req.spot_hint is None


# ── CLI parity (mocked providers — same path as the cockpit, no network) ──────

def test_cockpit_status_cli_parity(monkeypatch, capsys):
    quotes = [_q(7600, vp=False, reason="stale(age=30s>10s)"),
              _q(7605, vp=False, reason="stale(age=31s>10s)")]
    chain = _chain(quotes)
    structure = SimpleNamespace(spot=7585.0, expiry="2026-06-04", source="zerosigma_api",
                                exposures=SimpleNamespace(maxvol=7580.0))
    strat = SimpleNamespace(
        required_quote_strikes=lambda s, p: [7600.0, 7605.0], default_parameters={})
    fake_sp = SimpleNamespace(get_snapshot=lambda sym: structure, name="zerosigma_api")
    fake_qp = SimpleNamespace(
        get_option_chain=lambda sym, expiry=None, request=None: chain,
        status=lambda: SimpleNamespace(last_error=None), name="tastytrade")
    fake_cfg = SimpleNamespace(providers=SimpleNamespace(quotes_active="tastytrade"))

    from src.providers.quotes.types import QuoteValidation
    monkeypatch.setattr("src.utils.config.load_config", lambda root: fake_cfg)
    monkeypatch.setattr("src.strategies.registry.load_strategies", lambda cfg: {"vw": strat})
    monkeypatch.setattr("src.providers.structure.factory.build_structure_provider",
                        lambda cfg, override=None: (fake_sp, "zerosigma_api"))
    monkeypatch.setattr("src.providers.quotes.factory.build_quote_provider",
                        lambda **kw: (fake_qp, "tastytrade"))
    monkeypatch.setattr("src.providers.quotes.tastytrade_provider.validation_from_env",
                        lambda: QuoteValidation())

    rc = cockpit_cli.main(["--symbol", "SPX", "--dte", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cockpit quote provider" in out and "tastytrade" in out
    assert "chain_returned_validation_failed" in out
    assert "chain returned / validation blocked" in out
    assert "stale" in out
    assert "No secrets shown" in out


def test_cli_no_secret_tokens_in_source():
    repo = Path(__file__).resolve().parents[1]
    for rel in ("scripts/diagnose_cockpit_quote_status.py",):
        text = (repo / rel).read_text(encoding="utf-8").lower()
        for tok in ("submit_order", "place_order", "preview_order", "/orders", "client_secret",
                    "refresh_token"):
            assert tok not in text, f"{rel} contains {tok!r}"
