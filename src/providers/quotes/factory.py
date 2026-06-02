"""Factory for QuoteProvider instantiation — Phase 4.

Parallel to `src/providers/structure/factory.py`. Resolves the active
quote-provider name from CLI override → `QUOTE_PROVIDER` env →
`config/providers.yaml`. Always returns a working provider:

  - Tasty configured + selected → `TastytradeQuoteProvider`
  - Tasty selected but config incomplete + `fallback_on_misconfig=False`
    (default) → raise `TastytradeConfigurationError` so the scanner
    fails LOUDLY rather than silently falling back to mock.
  - Anything else (or unknown) → `MockQuoteProvider`
  - Explicitly "null" → `NullQuoteProvider`

The default is `mock` — the same Phase 1.5 behavior. `tastytrade` is
opt-in via CLI flag or `QUOTE_PROVIDER=tastytrade` in `.env`.
"""

from __future__ import annotations

import os
from typing import Any

from src.providers.quotes.mock_provider import MockQuoteProvider
from src.providers.quotes.null_provider import NullQuoteProvider
from src.providers.quotes.tastytrade_provider import (
    TastytradeConfigurationError,
    TastytradeQuoteProvider,
)
from src.utils.logging import get_logger

log = get_logger("provider.quotes_factory")


# Names accepted by the CLI flag + env var. Anything else → mock.
VALID_QUOTE_PROVIDER_NAMES = ("mock", "null", "tastytrade")


def resolve_quote_provider_name(
    override: str | None = None,
    *,
    yaml_active: str | None = None,
) -> str:
    """Pick the active provider name.

    Precedence: CLI override > `QUOTE_PROVIDER` env > YAML > "mock".
    """
    if override:
        return override.strip().lower()
    env = os.environ.get("QUOTE_PROVIDER")
    if env:
        return env.strip().lower()
    if yaml_active:
        return yaml_active.strip().lower()
    return "mock"


def build_quote_provider(
    *,
    override: str | None = None,
    yaml_active: str | None = None,
    fallback_on_misconfig: bool = False,
    strict_tasty: bool = True,
) -> tuple[Any, str]:
    """Instantiate the active QuoteProvider.

    Args:
        override:               CLI override (e.g. from `--quote-provider`)
        yaml_active:            `cfg.providers.quotes_active` value
        fallback_on_misconfig:  if True, an unconfigured Tasty selection
                                falls back to mock with a WARNING. Default
                                False — the spec says "do not fall back
                                silently to mock unless the existing
                                architecture explicitly supports fallback
                                and logs it clearly."
        strict_tasty:           passed through to TastytradeQuoteProvider —
                                raise immediately on missing config.

    Returns:
        (instance, resolved_name)

    Raises:
        TastytradeConfigurationError: when tastytrade is selected, config
            is incomplete, and `fallback_on_misconfig=False`.
    """
    name = resolve_quote_provider_name(override, yaml_active=yaml_active)

    if name == "tastytrade":
        try:
            provider = TastytradeQuoteProvider.from_env(strict=strict_tasty)
            return provider, "tastytrade"
        except TastytradeConfigurationError:
            if fallback_on_misconfig:
                log.warning(
                    "TastytradeQuoteProvider config incomplete — "
                    "falling back to MockQuoteProvider per fallback_on_misconfig=True"
                )
                return MockQuoteProvider(), "mock"
            raise

    if name == "null":
        return NullQuoteProvider(), "null"

    if name not in ("mock", *VALID_QUOTE_PROVIDER_NAMES):
        log.warning("Unknown quote provider %r; falling back to mock.", name)
    # Default = mock — Phase 1.5 behavior, unchanged.
    return MockQuoteProvider(), "mock"
