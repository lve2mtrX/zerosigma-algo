"""Sanitized Run Strategy live-readiness diagnostic.

Exercises the same structure-anchored quote request and cockpit quote-state
classification used by Streamlit, then applies the shared paper-test readiness
gate. Read-only: no broker, order preview, execution, or secret output.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _configure_cli_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def collect_live_readiness(
    *,
    symbol: str,
    profile_id: str,
    dte: int,
    structure_provider_name: str = "zerosigma_api",
    quote_provider_name: str = "tastytrade",
) -> dict[str, Any]:
    """Collect sanitized provider/readiness facts for CLI and tests."""
    import src.app.cockpit_helpers as ch
    from src.app import control_ui
    from src.app import operator_mode as om
    from src.config.strategy_profiles import load_profile_file
    from src.providers.quotes.factory import build_quote_provider
    from src.providers.quotes.tastytrade_provider import (
        TastytradeConfigurationError,
        validation_from_env,
    )
    from src.providers.structure.factory import build_structure_provider
    from src.strategies.registry import load_strategies
    from src.utils.config import load_config

    cfg = load_config(_REPO_ROOT)
    strategies = load_strategies(cfg)
    profile_result = load_profile_file(profile_id)
    profile = profile_result.profile
    structure_provider, resolved_structure_name = build_structure_provider(
        cfg, override=structure_provider_name
    )
    structure = None
    structure_error = None
    try:
        structure = structure_provider.get_snapshot(symbol)
    except Exception as exc:
        structure_error = f"{type(exc).__name__}: {exc}"

    quote_provider = None
    quote_provider_error = None
    resolved_quote_name = quote_provider_name
    try:
        quote_provider, resolved_quote_name = build_quote_provider(
            override=quote_provider_name,
            yaml_active=cfg.providers.quotes_active,
            fallback_on_misconfig=False,
        )
    except TastytradeConfigurationError as exc:
        quote_provider_error = f"{type(exc).__name__}: {exc}"

    quote_request = ch.build_quote_request(symbol, structure, strategies)
    chain = None
    quote_status = None
    if quote_provider is not None and structure is not None:
        chain = quote_provider.get_option_chain(
            symbol, expiry=structure.expiry, request=quote_request
        )
        quote_status = quote_provider.status()
    validation = validation_from_env()
    status = ch.cockpit_quote_status(
        symbol=symbol,
        resolved_quote_name=resolved_quote_name,
        chain=chain,
        quote_status=quote_status,
        quote_provider_error=quote_provider_error,
        structure_error=structure_error,
        max_spread_abs=validation.max_spread_abs,
        max_age_seconds=validation.max_age_seconds,
        requested_strikes=quote_request.required_strikes,
        dte=dte,
    )
    runner_can_start, runner_reason = control_ui.can_start(control_ui.get_status())
    sandbox = om.is_sandbox(resolved_structure_name, resolved_quote_name)
    readiness = om.paper_test_readiness(
        runner_can_start=runner_can_start,
        runner_reason=runner_reason,
        selected_profile_valid=profile_result.ok,
        local_paper_mode=True,
        structure_available=structure is not None and structure_error is None,
        required_strikes=quote_request.required_strikes,
        quote_state=status["state"],
        top_blocker=status["details"].get("top_blocker"),
        sandbox=sandbox,
        profile_dte=getattr(profile, "target_dte", None),
        quote_chain_dte=om.quote_chain_dte(status["details"].get("expiration"), datetime.now()),
    )
    exposures = getattr(structure, "exposures", None)
    corridor = ch.wing_corridor_status(
        getattr(structure, "spot", None),
        getattr(exposures, "call_floor_10k", None),
        getattr(exposures, "put_ceiling_10k", None),
    )
    return {
        "symbol": symbol,
        "target_dte": dte,
        "profile_id": profile_id,
        "profile_valid": profile_result.ok,
        "profile_errors": list(profile_result.errors),
        "profile_dte": getattr(profile, "target_dte", None),
        "zs_configured": ch.zs_configured(),
        "structure_provider": resolved_structure_name,
        "structure_available": structure is not None and structure_error is None,
        "structure_error": structure_error,
        "spot": getattr(structure, "spot", None),
        "corridor_10k_valid": corridor["corridor_valid"],
        "corridor_10k_reason": corridor["reason"],
        "required_strikes": list(quote_request.required_strikes),
        "quote_provider": resolved_quote_name,
        "tasty_configured": ch.tasty_configured(),
        "tasty_auth_mode": status["details"].get("auth_mode"),
        "quote_state": status["state"],
        "quote_label": readiness["quote_label"],
        "quote_root": status["details"].get("root"),
        "quote_expiration": status["details"].get("expiration"),
        "quote_chain_dte": om.quote_chain_dte(status["details"].get("expiration"), datetime.now()),
        "chain_returned": status["details"].get("chain_returned"),
        "quote_count": status["details"].get("quote_count"),
        "missing_strikes": status["details"].get("missing_strikes"),
        "top_blocker": status["details"].get("top_blocker"),
        "start_paper_test_enabled": readiness["can_start"],
        "start_reason": readiness["reason"],
        "preview_only": readiness["preview_only"],
        "no_broker": True,
        "no_order_preview": True,
        "no_execution": True,
    }


def main(argv: list[str] | None = None) -> int:
    _configure_cli_encoding()
    parser = argparse.ArgumentParser(
        description="Sanitized local-paper live-readiness diagnostic."
    )
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--dte", type=int, default=0)
    parser.add_argument("--structure-provider", default="zerosigma_api")
    parser.add_argument("--quote-provider", default="tastytrade")
    parser.add_argument(
        "--output-root", default="outputs", help="root for sanitized latest-readiness snapshot"
    )
    args = parser.parse_args(argv)
    result = collect_live_readiness(
        symbol=args.symbol.strip().upper(),
        profile_id=args.profile,
        dte=int(args.dte),
        structure_provider_name=args.structure_provider,
        quote_provider_name=args.quote_provider,
    )
    from src.app.readiness_snapshot import write_readiness_snapshot

    snapshot_path = write_readiness_snapshot(result, output_root=args.output_root)
    rows = [
        ("ZS configured", result["zs_configured"]),
        ("ZS structure available", result["structure_available"]),
        ("structure provider", result["structure_provider"]),
        ("spot", result["spot"]),
        ("10K corridor", f"{result['corridor_10k_valid']} ({result['corridor_10k_reason']})"),
        ("selected profile", result["profile_id"]),
        ("profile valid / DTE", f"{result['profile_valid']} / {result['profile_dte']}"),
        ("required quote strikes", result["required_strikes"]),
        ("Tasty provider", result["quote_provider"]),
        (
            "Tasty configured / auth",
            f"{result['tasty_configured']} / {result['tasty_auth_mode'] or 'unconfirmed'}",
        ),
        (
            "Tasty root / expiry / DTE",
            f"{result['quote_root']} / {result['quote_expiration']} / {result['quote_chain_dte']}",
        ),
        ("chain returned / quotes", f"{result['chain_returned']} / {result['quote_count']}"),
        ("quote state", f"{result['quote_state']} (Quotes: {result['quote_label']})"),
        ("missing strikes", result["missing_strikes"]),
        ("quote validation blocker", result["top_blocker"]),
        ("Start Paper Test enabled", result["start_paper_test_enabled"]),
        ("Start reason", result["start_reason"]),
    ]
    print("ZerσSigma Algo — live paper readiness (read-only, no secrets)")
    print(f"symbol={result['symbol']}  target_dte={result['target_dte']}")
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")
    print("\nNo secrets shown. No broker execution. No order preview.")
    print(f"Sanitized latest snapshot: {snapshot_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
