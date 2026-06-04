"""Phase 9E — Operator Mode + symbol wiring helpers.

NO network, NO credentials, NO broker execution. Pure-helper tests + a symbol
round-trip through the profile-builder layer. Streamlit shell import-checked.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from src.app import operator_mode as om
from src.app import profile_builder as pb

REPO = Path(__file__).resolve().parents[1]


# ── side preference → profile fields ─────────────────────────────────────────

def test_side_preference_mapping():
    assert om.side_preference_to_fields("Calls only") == {
        "allow_call_credit": True, "allow_put_credit": False,
        "daily_selector": "call_credit_only"}
    assert om.side_preference_to_fields("Puts only") == {
        "allow_call_credit": False, "allow_put_credit": True,
        "daily_selector": "put_credit_only"}
    assert om.side_preference_to_fields("Both sides") == {
        "allow_call_credit": True, "allow_put_credit": True}
    obs = om.side_preference_to_fields("Observe only")
    assert obs["daily_selector"] == "no_trade"


def test_selector_style_mapping():
    assert om.selector_style_to_selector("Best score") == "score_best_valid"
    assert om.selector_style_to_selector("Best credit") == "best_credit_valid"
    assert om.selector_style_to_selector("Conservative / lowest breach risk") == "lowest_breach_risk_valid"
    assert om.selector_style_to_selector("No trade / observe only") == "no_trade"
    # reverse map (for seeding the control)
    assert om.selector_to_style("lowest_breach_risk_valid") == "Conservative / lowest breach risk"


def test_build_simple_fields_combines_and_observe_overrides():
    f = om.build_simple_fields(side_preference="Calls only", selector_style="Best score")
    assert f["allow_call_credit"] is True and f["allow_put_credit"] is False
    assert f["daily_selector"] == "score_best_valid"          # selector style overrides default
    fo = om.build_simple_fields(side_preference="Observe only", selector_style="Best score")
    assert fo["daily_selector"] == "no_trade"                  # observe wins regardless of style
    fb = om.build_simple_fields(side_preference="Both sides", selector_style="Best credit")
    assert fb == {"allow_call_credit": True, "allow_put_credit": True,
                  "daily_selector": "best_credit_valid"}


# ── data source → providers (ZerσSigma exposures + Tasty market data) ─────────

def test_data_source_mapping():
    assert om.data_source_to_providers(om.DATA_SOURCE_LIVE) == {
        "structure_provider": "zerosigma_api", "quote_provider": "tastytrade"}
    assert om.data_source_to_providers(om.DATA_SOURCE_SANDBOX) == {
        "structure_provider": "stub", "quote_provider": "mock"}
    assert om.providers_to_data_source("zerosigma_api", "tastytrade") == om.DATA_SOURCE_LIVE
    assert om.providers_to_data_source("stub", "mock") == om.DATA_SOURCE_SANDBOX
    assert om.providers_to_data_source("zerosigma_api", "null") == om.DATA_SOURCE_SANDBOX


def test_data_source_labels_reflect_engine_split():
    # ZerσSigma = exposures; Tasty = market data
    assert "exposures" in om.DATA_SOURCE_LIVE and "market data" in om.DATA_SOURCE_LIVE
    assert "exposures" in om.DATA_SOURCE_SANDBOX and "market data" in om.DATA_SOURCE_SANDBOX
    assert om.EXPOSURE_SOURCE_LABEL == "Exposure source"
    assert om.MARKET_DATA_SOURCE_LABEL == "Market data source"
    assert "ZerσSigma" in om.exposure_engine_label("zerosigma_api")
    assert "Tasty" in om.market_data_engine_label("tastytrade")
    assert "sandbox" in om.market_data_engine_label("mock")


# ── symbol normalization + arbitrary tickers ─────────────────────────────────

def test_symbol_normalization_uppercase_and_default():
    assert om.normalize_symbol(" spy ") == "SPY"
    assert om.normalize_symbol("qqq") == "QQQ"
    assert om.normalize_symbol("") == "SPX"
    assert om.normalize_symbol(None) == "SPX"
    assert om.normalize_symbol("/es") == "/ES"          # arbitrary symbol accepted


def test_arbitrary_symbol_accepted_at_builder_layer(tmp_path):
    base = pb.new_template_dict("ticker_demo")
    built = pb.build_profile_dict({"symbol": om.normalize_symbol("qqq")}, base=base)
    assert built["symbol"] == "QQQ"
    assert pb.validate_dict(built) == []                # arbitrary symbol is valid config
    ok, _msg, _h = pb.save_profile(built, profiles_dir=tmp_path)
    assert ok is True
    rows = {r["profile_id"]: r for r in pb.list_summaries(tmp_path)}
    assert "ticker_demo" in rows and rows["ticker_demo"]["ok"] is True


# ── symbol health (Tasty market data vs ZerσSigma exposures) ─────────────────

def test_symbol_health_distinguishes_engines():
    # arbitrary ticker: Tasty market data OK but no ZerσSigma exposure coverage
    h = om.symbol_health(symbol="QQQ", market_data_available=True, exposures_available=False)
    assert h["eligible"] is False
    assert h["market_data_available"] is True and h["exposures_available"] is False
    assert "ZerσSigma exposures unavailable" in h["reason"]
    # both available → eligible
    ok = om.symbol_health(symbol="SPX", market_data_available=True, exposures_available=True)
    assert ok["eligible"] is True and ok["reason"] == ""
    # neither
    none = om.symbol_health(symbol="ZZZ", market_data_available=False, exposures_available=False)
    assert "No ZerσSigma exposures and no Tasty market data" in none["reason"]
    # market data missing only
    md = om.symbol_health(symbol="SPX", market_data_available=False, exposures_available=True)
    assert "Tasty market data unavailable" in md["reason"]


def test_unsupported_symbol_warnings():
    assert "ZerσSigma exposures unavailable for FOO" in om.exposures_unavailable_warning("FOO")
    assert "Not every ticker" in om.exposures_unavailable_warning("FOO")
    assert "Tasty market data unavailable for FOO" in om.market_data_unavailable_warning("FOO")


# ── branded labels: Zσ Strat Tester, no "Forward Runner" tab ─────────────────

def test_tab_labels_branded_no_forward_runner():
    labels = om.tab_labels()
    assert len(labels) == 6
    joined = " ".join(labels)
    # Phase 10B: the Tester tab is relabeled "🧪 Run Strategy" (trader-first).
    assert "Run Strategy" in joined
    assert "Zσ Strat Tester" not in joined
    assert "Forward Runner" not in joined
    assert any("Paper Portfolio" in lbl for lbl in labels)
    assert any("Strat Builder" in lbl for lbl in labels)   # 9F: "Zσ Strat Builder"


def test_strict_dte_visible_label_replaced():
    fields = {f["name"]: f for f in pb.PROFILE_FIELDS}
    assert fields["strict_target_dte"]["label"] == "Require exact DTE match"
    assert "Strict" not in fields["strict_target_dte"]["label"]


def test_friendly_log_labels():
    assert om.friendly_log_label("tick_log.jsonl") == "Strategy test log"
    assert om.friendly_log_label("signal_log.jsonl") == "Selected trades export"
    assert om.friendly_log_label("no_trade_log.jsonl") == "No-trade reasons export"
    assert om.friendly_log_label("paper_trade_events.jsonl") == "Paper trade events"
    assert om.friendly_log_label("portfolio_summary.json") == "Portfolio summary"
    assert om.friendly_log_label("reconciliation_report.json") == "Reconciliation report"
    assert om.friendly_log_label("mystery.csv") == "mystery.csv"


def test_simple_mode_help_copy():
    assert "Simple Mode gets you running" in om.SIMPLE_MODE_HELP
    assert om.DEFAULT_SIMPLE_MODE is True


# ── streamlit imports cleanly + no "Forward Runner" tab literal ──────────────

def test_streamlit_imports_cleanly_and_uses_branded_tabs():
    m = importlib.import_module("src.app.streamlit_main")
    assert m is not None
    src = (REPO / "src" / "app" / "streamlit_main.py").read_text(encoding="utf-8")
    ast.parse(src)
    # tabs come from om.tab_labels(); the old visible tab literal must be gone
    assert '"▶ Forward Runner"' not in src
    assert "om.tab_labels()" in src


def test_no_execution_surface_in_operator_mode():
    forbidden = ("submit_order", "place_order", "preview_order", "create_order",
                 "order_preview", "execute_trade", "broker.")
    src = (REPO / "src" / "app" / "operator_mode.py").read_text(encoding="utf-8")
    for tok in forbidden:
        assert tok not in src, f"operator_mode.py must not reference {tok!r}"
