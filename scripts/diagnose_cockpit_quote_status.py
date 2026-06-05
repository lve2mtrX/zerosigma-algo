"""Live Cockpit quote-status PARITY check — READ-ONLY.

Reproduces EXACTLY what the Streamlit Live Cockpit does to decide its quote
status — loads `.env` via `load_config`, builds the SAME structure + quote
providers (honoring QUOTE_PROVIDER / the Live data source), fetches the chain
with the SAME structure-anchored `QuoteRequest`, and classifies it with the SAME
`cockpit_quote_status` helper. This lets us verify the cockpit's status banner
without eyeballing the app.

No orders, no order preview, no execution, no secrets. (ZerσSigma supplies
structure/exposures; Tastytrade supplies the quote chain.)

Usage:
    python -m scripts.diagnose_cockpit_quote_status --symbol SPX --dte 0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _configure_cli_encoding() -> None:
    """Keep Unicode banners printable in default Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    _configure_cli_encoding()
    ap = argparse.ArgumentParser(
        description="Live Cockpit quote-status parity check (read-only, no orders).")
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--dte", type=int, default=0, help="informational (the cockpit uses the "
                                                       "structure's own expiry)")
    args = ap.parse_args(argv)

    import src.app.cockpit_helpers as ch
    from src.app import operator_mode as om
    from src.providers.quotes.factory import build_quote_provider
    from src.providers.quotes.tastytrade_provider import (
        TastytradeConfigurationError,
        validation_from_env,
    )
    from src.providers.structure.factory import build_structure_provider
    from src.providers.structure.stub import StubStructureProvider
    from src.strategies.registry import load_strategies
    from src.utils.config import load_config

    cfg = load_config(_REPO_ROOT)            # loads .env (QUOTE_PROVIDER, TASTY_*)
    symbol = (args.symbol or "SPX").strip().upper()
    strategies = load_strategies(cfg)

    print("ZerσSigma Algo — Live Cockpit quote-status parity (read-only, no orders, no secrets)")
    print(f"symbol={symbol}  target_dte={args.dte}")

    # ── same provider build as streamlit_main (override=None → configured default) ──
    structure_provider, resolved_structure_name = build_structure_provider(cfg, override=None)
    quote_provider_error: str | None = None
    try:
        quote_provider, resolved_quote_name = build_quote_provider(
            override=None, yaml_active=cfg.providers.quotes_active, fallback_on_misconfig=True)
    except TastytradeConfigurationError as exc:
        quote_provider_error = f"{type(exc).__name__}: {exc}"
        from src.providers.quotes.mock_provider import MockQuoteProvider
        quote_provider, resolved_quote_name = MockQuoteProvider(), "mock"

    try:
        structure = structure_provider.get_snapshot(symbol)
        structure_error: str | None = None
    except Exception as exc:                  # mirror the cockpit's stub fallback
        structure_error = f"{type(exc).__name__}: {exc}"
        structure_provider = StubStructureProvider()
        resolved_structure_name = "stub"
        structure = structure_provider.get_snapshot(symbol)

    quote_request = ch.build_quote_request(symbol, structure, strategies)
    chain = quote_provider.get_option_chain(symbol, expiry=structure.expiry, request=quote_request)
    quote_status = quote_provider.status()
    validation = validation_from_env()

    status = ch.cockpit_quote_status(
        symbol=symbol, resolved_quote_name=resolved_quote_name, chain=chain,
        quote_status=quote_status, quote_provider_error=quote_provider_error,
        structure_error=structure_error, max_spread_abs=validation.max_spread_abs,
        max_age_seconds=validation.max_age_seconds,
        requested_strikes=quote_request.required_strikes,
    )
    data_source = om.providers_to_data_source(resolved_structure_name, resolved_quote_name)
    d = status["details"]

    rows = [
        ("cockpit quote provider", resolved_quote_name),
        ("resolved data source", data_source),
        ("structure provider", resolved_structure_name),
        ("required strikes", list(quote_request.required_strikes)),
        ("chain returned", d["chain_returned"]),
        ("root / expiration", f"{d['root'] or '—'} @ {d['expiration'] or '—'}"),
        ("quote count", d["quote_count"]),
        ("strike range", f"{d['strike_min']} – {d['strike_max']}"),
        ("validation passed / failed", f"{d['validation_passed_count']} / {d['validation_failed_count']}"),
        ("top validation blocker", d["top_blocker"]),
        ("max_spread_abs / max_age_s", f"{d['max_spread_abs']} / {d['max_age_seconds']}"),
        ("observed worst spread", d["observed_failing_spread"]),
        ("missing strikes", d["missing_strikes"]),
        ("last_error", d["last_error"]),
    ]
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        print(f"  {k.ljust(width)} : {v}")

    print()
    print(f"  STATE              : {status['state']}")
    print(f"  UI status label    : {status['label']}")
    print(f"  Strategy eligible  : {status['eligible_hint']}")
    print(f"  Banner             : {status['banner'] or '(none — usable / sandbox)'}")
    print("\nNo secrets shown. No broker execution. No order preview.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
