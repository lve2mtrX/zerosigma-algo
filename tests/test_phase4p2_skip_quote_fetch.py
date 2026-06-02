"""Phase 4.2.1 — scanner skips the quote fetch (clean NO_TRADE) instead of
treating an unservable request as a provider failure.

NO network, NO Tasty creds. A recording fake stands in for
TastytradeQuoteProvider; it mimics the REAL provider's whole-chain refusal
(returns None when QuoteRequest.required_strikes is empty) and records every
call so we can assert the scanner NEVER calls it under the skip conditions.

Covered (req 6):
  - tasty path with EMPTY required_strikes → provider NOT called, clean NO_TRADE
  - strict target-DTE unavailable → provider NOT called, clean NO_TRADE
  - normal path WITH required_strikes → provider IS called (with strikes)
  - no whole-chain Tasty pull is ever introduced (refusal branch never hit)
  - mock provider also skips under the same empty-strikes condition (req 3)
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from src.providers.quotes.types import OptionChainSnapshot, QuoteProviderStatus
from src.utils.time import now_et

rs = importlib.import_module("scripts.run_scanner")


class _RecordingTastyLike:
    """Stand-in for TastytradeQuoteProvider. Records calls; refuses whole-chain
    pulls exactly like the real provider (empty required_strikes → None)."""

    name = "tastytrade"

    def __init__(self) -> None:
        self.chain_calls: list[tuple] = []     # required_strikes per get_option_chain call
        self.whole_chain_pulls = 0             # empty-strikes requests = the forbidden pull
        self.spot_calls = 0

    def get_option_chain(self, symbol, expiry=None, request=None):  # type: ignore[no-untyped-def]
        strikes = tuple(request.required_strikes) if request else ()
        self.chain_calls.append(strikes)
        if not strikes:
            # Mirror the real provider's safety boundary — never whole-chain pull.
            self.whole_chain_pulls += 1
            return None
        return OptionChainSnapshot(
            underlying=symbol.upper(),
            spot=(request.spot_hint if request and request.spot_hint else 5800.0),
            expiry=expiry or "2026-06-02",
            quotes=[],                          # empty → zero candidates → NO_TRADE
            quote_ts=now_et(),
            provider_name="tastytrade",
            resolved_root_symbol="SPXW",
            root_resolution_source="auto_chain",
        )

    def get_spot(self, symbol):  # type: ignore[no-untyped-def]
        self.spot_calls += 1
        return None

    def status(self) -> QuoteProviderStatus:
        return QuoteProviderStatus(provider_name="tastytrade", connected=True)


def _run(
    monkeypatch, tmp_path, argv, *,
    rec=None, force_empty_strikes=False, capsys=None,
) -> tuple[int, str]:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("STRICT_TARGET_DTE", raising=False)
    monkeypatch.delenv("TARGET_DTE", raising=False)
    monkeypatch.setattr(sys, "argv", argv)
    importlib.reload(rs)
    if rec is not None:
        monkeypatch.setattr(
            "src.providers.quotes.factory.build_quote_provider",
            lambda **kw: (rec, "tastytrade"),
        )
    if force_empty_strikes:
        monkeypatch.setattr(rs, "_collect_required_strikes", lambda *a, **k: [])
    rc = rs.main()
    out = capsys.readouterr().out if capsys is not None else ""
    return rc, out


def _latest_decisions(tmp_path: Path) -> list[dict]:
    p = tmp_path / "latest" / "decision_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── (1) empty required_strikes → Tasty NOT called ────────────────────────

def test_tasty_empty_required_strikes_skips_provider(monkeypatch, tmp_path, capsys):
    rec = _RecordingTastyLike()
    rc, out = _run(
        monkeypatch, tmp_path,
        ["scripts.run_scanner", "--strategy", "vertical_wing_v1",
         "--quote-provider", "tastytrade", "--structure-provider", "stub",
         "--target-dte", "1", "--print-candidates"],
        rec=rec, force_empty_strikes=True, capsys=capsys,
    )
    assert rc == 0
    assert "Traceback" not in out
    # The provider was NEVER called — no whole-chain pull attempted.
    assert rec.chain_calls == []
    assert rec.whole_chain_pulls == 0
    # Clean NO_TRADE with machine-readable skip fields in the decision log.
    recs = _latest_decisions(tmp_path)
    assert recs and all(r["decision"] == "NO_TRADE" for r in recs)
    snap = recs[0]["snapshot_summary"]
    assert snap["quote_request_skipped"] is True
    assert snap["quote_request_skipped_reason"] == "no_required_strikes"
    assert snap["required_strikes_available"] is False
    assert "no_required_strikes" in snap["selector_blockers"]
    assert "insufficient_structure" in snap["selector_blockers"]
    assert snap["quote_provider"] is None              # no provider call
    assert "target_dte" in snap and "selected_expiry" in snap
    # Audit print shows the skip block (greppable, no provider call).
    assert "QUOTE REQUEST SKIPPED" in out
    assert "quote_request_skipped_reason='no_required_strikes'" in out
    assert "provider_called=False" in out


# ── (2) strict target-DTE unavailable → Tasty NOT called ─────────────────

def test_tasty_strict_unavailable_skips_provider(monkeypatch, tmp_path, capsys):
    rec = _RecordingTastyLike()
    rc, out = _run(
        monkeypatch, tmp_path,
        ["scripts.run_scanner", "--strategy", "vertical_wing_v1",
         "--quote-provider", "tastytrade", "--structure-provider", "stub",
         "--target-dte", "1", "--dte-mode", "trading_days",
         "--strict-target-dte", "--print-candidates"],
        rec=rec, capsys=capsys,
    )
    assert rc == 0
    assert "Traceback" not in out
    assert rec.chain_calls == []                       # provider NOT called
    assert rec.whole_chain_pulls == 0
    assert "strict_target_dte_unavailable" in out
    recs = _latest_decisions(tmp_path)
    assert recs and all(r["decision"] == "NO_TRADE" for r in recs)
    snap = recs[0]["snapshot_summary"]
    assert snap["quote_request_skipped_reason"] == "strict_target_dte_unavailable"
    assert "strict_target_dte_unavailable" in snap["selector_blockers"]
    assert snap["strict_target_dte"] is True
    assert snap["strict_target_dte_passed"] is False


# ── (3) normal path WITH required_strikes → Tasty IS called ──────────────

def test_tasty_normal_path_calls_provider(monkeypatch, tmp_path):
    rec = _RecordingTastyLike()
    rc, _ = _run(
        monkeypatch, tmp_path,
        ["scripts.run_scanner", "--strategy", "vertical_wing_v1",
         "--quote-provider", "tastytrade", "--structure-provider", "stub",
         "--target-dte", "0"],
        rec=rec,
    )
    assert rc == 0
    # Provider WAS called exactly once, WITH a concrete required_strikes list.
    assert len(rec.chain_calls) == 1
    assert len(rec.chain_calls[0]) > 0
    # ...and it was a targeted (not whole-chain) pull.
    assert rec.whole_chain_pulls == 0


# ── (4) no whole-chain pull is ever introduced ───────────────────────────

def test_no_whole_chain_pull_in_skip_or_normal(monkeypatch, tmp_path):
    """Across both the skip path and the normal path, the provider's
    empty-strikes (whole-chain) branch is NEVER triggered."""
    rec_skip = _RecordingTastyLike()
    _run(monkeypatch, tmp_path / "a",
         ["scripts.run_scanner", "--strategy", "vertical_wing_v1",
          "--quote-provider", "tastytrade", "--structure-provider", "stub",
          "--target-dte", "1"],
         rec=rec_skip, force_empty_strikes=True)
    rec_norm = _RecordingTastyLike()
    _run(monkeypatch, tmp_path / "b",
         ["scripts.run_scanner", "--strategy", "vertical_wing_v1",
          "--quote-provider", "tastytrade", "--structure-provider", "stub",
          "--target-dte", "0"],
         rec=rec_norm)
    assert rec_skip.whole_chain_pulls == 0
    assert rec_norm.whole_chain_pulls == 0


# ── (5) mock provider also skips under the same condition (req 3) ─────────

def test_mock_empty_required_strikes_also_skips(monkeypatch, tmp_path):
    """The skip is provider-agnostic: with no required strikes the scanner
    emits a clean NO_TRADE for the mock provider too (same outcome it already
    produced via zero candidates, now via the explicit skip path)."""
    rc, _ = _run(
        monkeypatch, tmp_path,
        ["scripts.run_scanner", "--strategy", "vertical_wing_v1",
         "--quote-provider", "mock", "--structure-provider", "stub",
         "--target-dte", "0"],
        force_empty_strikes=True,
    )
    assert rc == 0
    recs = _latest_decisions(tmp_path)
    assert recs and all(r["decision"] == "NO_TRADE" for r in recs)
    snap = recs[0]["snapshot_summary"]
    assert snap["quote_request_skipped_reason"] == "no_required_strikes"
    assert snap["required_strikes_available"] is False
