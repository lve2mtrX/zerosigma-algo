"""Phase 9D — cockpit UX polish helpers.

NO network, NO credentials, NO broker execution. Pure-helper tests + log-export
helpers exercised against tmp dirs. Streamlit shell checked for clean import.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from src.app import cockpit_helpers as ch
from src.app import profile_builder as pb

REPO = Path(__file__).resolve().parents[1]


# ── compact number formatting ────────────────────────────────────────────────

def test_fmt_exposure_billions_and_millions():
    assert ch.fmt_exposure(4.181966) == "4.18B"
    assert ch.fmt_exposure(0.735) == "735M"
    assert ch.fmt_exposure(-1.2) == "-1.20B"
    assert ch.fmt_exposure(0) == "0B"
    assert ch.fmt_exposure(None) == "—"
    assert ch.fmt_exposure("nope") == "—"


def test_fmt_strike_price_pct_money_count():
    assert ch.fmt_strike(5815.0) == "5815"
    assert ch.fmt_strike(5817.5) == "5817.50"
    assert ch.fmt_strike(None) == "—"
    assert ch.fmt_price(7609.78) == "7,609.78"
    assert ch.fmt_price(None) == "—"
    assert ch.fmt_money(1234.56) == "$1,234.56"
    assert ch.fmt_pct(0.0731) == "7.31%"
    assert ch.fmt_pct(7.31, as_fraction=False) == "7.31%"
    assert ch.fmt_count(2200) == "2,200"


def test_gamma_regime_badge():
    assert ch.gamma_regime_badge("positive") == "positive ↑"
    assert ch.gamma_regime_badge("negative") == "negative ↓"
    assert ch.gamma_regime_badge(None, 4.0) == "positive ↑"     # derived from DA-GEX sign
    assert ch.gamma_regime_badge(None, -1.2) == "negative ↓"
    assert ch.gamma_regime_badge(None, None) == "—"


# ── spot fallback ────────────────────────────────────────────────────────────

def test_spot_fallback_prefers_quote_then_structure():
    val, src = ch.spot_with_source(7609.78, 7600.0)
    assert val == 7609.78 and src == "quote"
    # quote spot missing → structure spot
    val2, src2 = ch.spot_with_source(None, 7600.0)
    assert val2 == 7600.0 and src2 == "Zσ structure"
    # quote spot 0.0 is treated as missing (provider error sentinel)
    val3, src3 = ch.spot_with_source(0.0, 7600.0)
    assert val3 == 7600.0 and src3 == "Zσ structure"
    # all missing but a separate last quote present
    val4, src4 = ch.spot_with_source(None, None, 7590.0)
    assert val4 == 7590.0 and src4 == "quote (last)"
    # nothing
    assert ch.spot_with_source(None, None) == (None, "—")


# ── provider configured detection + defaults ─────────────────────────────────

def test_tasty_configured_env_presence_only():
    assert ch.tasty_configured({"TASTY_CLIENT_ID": "x", "TASTY_CLIENT_SECRET": "y",
                                 "TASTY_REFRESH_TOKEN": "z"}) is True
    assert ch.tasty_configured({"TASTY_USERNAME": "u", "TASTY_PASSWORD": "p"}) is True
    assert ch.tasty_configured({}) is False
    assert ch.tasty_configured({"TASTY_CLIENT_ID": "x"}) is False  # partial OAuth


def test_zs_configured_env_presence_only():
    assert ch.zs_configured({"ZS_API_BASE_URL": "https://x", "ZS_API_AUTH_MODE": "bearer"}) is True
    assert ch.zs_configured({"ZS_API_BASE_URL": "https://x", "ZS_API_AUTH_MODE": "public_only"}) is True
    assert ch.zs_configured({"ZS_API_AUTH_MODE": "bearer"}) is False     # no base url
    assert ch.zs_configured({"ZS_API_BASE_URL": "https://x", "ZS_API_AUTH_MODE": "none"}) is False
    assert ch.zs_configured({}) is False


def test_default_provider_prefers_configured():
    opts_q = ["tastytrade", "mock", "null"]
    assert ch.default_provider(opts_q, preferred="tastytrade", sandbox="mock", configured=True) == "tastytrade"
    assert ch.default_provider(opts_q, preferred="tastytrade", sandbox="mock", configured=False) == "mock"
    opts_s = ["zerosigma_api", "stub"]
    assert ch.default_provider(opts_s, preferred="zerosigma_api", sandbox="stub", configured=True) == "zerosigma_api"
    assert ch.default_provider(opts_s, preferred="zerosigma_api", sandbox="stub", configured=False) == "stub"


def test_provider_index_and_label():
    assert ch.provider_index(["a", "b", "c"], "b") == 1
    assert ch.provider_index(["a", "b"], "zzz") == 0
    assert "sandbox" in ch.provider_label("mock")
    assert "live" in ch.provider_label("tastytrade")
    assert ch.provider_label("tastytrade") != "tastytrade"


def test_chain_unavailable_actions():
    acts = ch.chain_unavailable_actions("tastytrade", last_error="429 throttled")
    blob = " ".join(acts).lower()
    assert "rth" in blob and "mock" in blob and "tasty auth" in blob
    assert "429 throttled" in " ".join(acts)
    mock_acts = ch.chain_unavailable_actions("mock")
    assert all("tasty auth" not in a.lower() for a in mock_acts)


# ── strict DTE copy ──────────────────────────────────────────────────────────

def test_strict_dte_label_and_help():
    assert ch.strict_dte_label() == "Require exact DTE match"
    assert ch.STRICT_DTE_LABEL == "Require exact DTE match"
    h = ch.strict_dte_help()
    assert "0DTE" in h and "no trade" in h.lower()


# ── status strip ─────────────────────────────────────────────────────────────

def test_status_strip_cells():
    cells = ch.status_strip_cells(
        run_profile="p1", structure_name="zerosigma_api", quote_name="tastytrade",
        runner_status="running", selected_trade="TRADE_CALL_CREDIT", open_trades=2,
        realized_pnl=125.5,
    )
    labels = [c[0] for c in cells]
    assert labels == ["Run profile", "Structure", "Quote", "Runner", "Selected",
                      "Open paper", "Realized P&L"]
    d = dict(cells)
    assert d["Realized P&L"] == "$125.50" and d["Open paper"] == "2"
    # empty-ish inputs degrade gracefully
    empty = dict(ch.status_strip_cells(run_profile=None, structure_name=None,
                                       quote_name=None, runner_status=None,
                                       selected_trade=None, open_trades=None,
                                       realized_pnl=None))
    assert empty["Runner"] == "stopped" and empty["Open paper"] == "0"


# ── review prompt ────────────────────────────────────────────────────────────

def test_review_prompt_mentions_key_topics():
    p = ch.review_prompt("20260101_000000_x").lower()
    assert "forward run" in p
    assert "no-trade" in p or "no_trade" in p
    assert "quote" in p
    assert "p&l" in p or "pnl" in p
    assert "selection" in p


# ── log export helpers (read-only; graceful when missing) ────────────────────

def test_forward_export_files_missing_is_graceful(tmp_path):
    files = ch.forward_export_files(tmp_path)
    assert len(files) == 3
    assert all(f["exists"] is False and f["text"] is None for f in files)


def test_forward_export_files_reads_seeded_run(tmp_path):
    run = tmp_path / "runs" / "20260101_000000_x"
    run.mkdir(parents=True)
    (run / "tick_log.jsonl").write_text('{"tick_id": 1}\n', encoding="utf-8")
    (run / "signal_log.jsonl").write_text('{"side": "CALL_CREDIT"}\n', encoding="utf-8")
    files = ch.forward_export_files(tmp_path)
    by_name = {f["filename"]: f for f in files}
    assert by_name["tick_log.jsonl"]["exists"] is True
    assert "tick_id" in by_name["tick_log.jsonl"]["text"]
    assert by_name["no_trade_log.jsonl"]["exists"] is False     # not seeded


def test_portfolio_export_files_missing_is_graceful(tmp_path):
    files = ch.portfolio_export_files(tmp_path)
    assert len(files) == 3
    assert all(f["exists"] is False for f in files)


def test_portfolio_export_files_reads_seeded_run(tmp_path):
    run = tmp_path / "runs" / "20260101_000000_portfolio"
    run.mkdir(parents=True)
    (run / "portfolio_summary.json").write_text('{"total_pnl": 0}', encoding="utf-8")
    files = ch.portfolio_export_files(tmp_path)
    by_name = {f["filename"]: f for f in files}
    assert by_name["portfolio_summary.json"]["exists"] is True
    assert "total_pnl" in by_name["portfolio_summary.json"]["text"]


# ── profile builder advanced grouping (Phase 9D metadata) ────────────────────

def test_advanced_field_grouping():
    assert pb.is_advanced("min_selector_score") is True
    assert pb.is_advanced("strict_target_dte") is True
    assert pb.is_advanced("profile_id") is False
    assert pb.is_advanced("daily_selector") is False
    # named expander groups
    expiry = [f["name"] for f in pb.advanced_group_fields("Advanced expiry controls")]
    assert "strict_target_dte" in expiry
    selector = [f["name"] for f in pb.advanced_group_fields("Advanced selector filters")]
    assert "min_selector_score" in selector
    # basics excludes advanced
    basics = {f["name"] for f in pb.basic_fields()}
    assert "profile_id" in basics and "min_selector_score" not in basics


def test_strict_dte_field_renamed():
    fields = {f["name"]: f for f in pb.PROFILE_FIELDS}
    assert fields["strict_target_dte"]["label"] == "Require exact DTE match"
    assert "0DTE" in fields["strict_target_dte"]["help"]


# ── streamlit imports cleanly + no execution surface ─────────────────────────

def test_streamlit_imports_cleanly():
    m = importlib.import_module("src.app.streamlit_main")
    assert m is not None
    ast.parse((REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8"))


def test_no_execution_surface_in_new_helpers():
    forbidden = ("submit_order", "place_order", "preview_order", "create_order",
                 "order_preview", "execute_trade", "broker.")
    for rel in ("src/app/cockpit_helpers.py", "src/app/ui_helpers.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        for tok in forbidden:
            assert tok not in src, f"{rel} must not reference {tok!r}"
