"""Pytest session setup — keep the suite hermetic against the developer's `.env`.

The test suite must NOT depend on the local `.env` (which may select the live
Tastytrade quote provider with real OAuth credentials) and must NEVER hit the
network. ``src.utils.config.load_config`` — called by ``run_scanner.main`` and by
``streamlit_main`` at import — loads `.env` via ``load_dotenv(override=False)``.
Because ``override=False`` skips any var already present in ``os.environ``,
PRE-SETTING the provider-selection + Tasty vars HERE (at conftest import, before
any test module imports or ``load_config`` runs) makes these safe test values win
over whatever is in `.env`.

This is test infrastructure only — it does not touch strategy, selector, risk, or
backtest logic. Tests that exercise the Tastytrade path inject their own config +
``httpx.MockTransport`` and are unaffected.
"""

from __future__ import annotations

import os

# Force the offline mock quote provider for the whole session, regardless of
# what `.env` (or the shell) sets. Scanner tests assume the mock chain.
os.environ["QUOTE_PROVIDER"] = "mock"

# Neutralize Tasty credentials so config_from_env() reports "not configured" and
# nothing can select / authenticate the live provider during tests. Present-but-
# empty values are treated as unset by the loaders AND block load_dotenv's
# override=False from re-populating them from `.env`.
for _var in (
    "TASTY_CLIENT_ID",
    "TASTY_CLIENT_SECRET",
    "TASTY_REFRESH_TOKEN",
    "TASTY_USERNAME",
    "TASTY_PASSWORD",
    "TASTY_BASE_URL",
):
    os.environ[_var] = ""
os.environ["TASTY_ENV"] = "certification"

# Phase 11E notification adapters are opt-in. Force test-session delivery off
# so a developer shell cannot enable Pushover or voice for unrelated tests.
os.environ["ALERTS_ENABLED"] = "false"
os.environ["ALERTS_PUSHOVER_ENABLED"] = "false"
os.environ["ALERTS_VOICE_ENABLED"] = "false"
os.environ["PUSHOVER_USER_KEY"] = ""
os.environ["PUSHOVER_API_TOKEN"] = ""
