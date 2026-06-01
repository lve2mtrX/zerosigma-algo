"""Tastytrade capability probe — read-only smoke test.

Usage:
    python -m scripts.probe_tastytrade --help

    # NO credentials required for --help; --help works offline.

    # Auth check only (POSTs /sessions, prints sanitized auth_success)
    python -m scripts.probe_tastytrade --auth-only

    # List accounts (redacted to last-4)
    python -m scripts.probe_tastytrade --accounts

    # Option chain summary (counts + small strike sample, no raw payload)
    python -m scripts.probe_tastytrade --chain --symbol SPX

    # Quotes for specific strikes (probe synthesizes OCC symbols today,
    # then GET /market-data/by-type)
    python -m scripts.probe_tastytrade --quotes --symbol SPX \\
        --expiry 2026-06-30 --strikes 5800,5810,5815,5820 --right C

    # Full capability matrix
    python -m scripts.probe_tastytrade --capabilities --symbol SPX

The probe NEVER:
    * POSTs to /orders or /complex-orders (live submit paths)
    * Submits anything (order, dry-run, or otherwise) by default
    * Prints passwords, session tokens, remember-tokens, auth headers,
      or full account numbers

Default output is text. Add `--json` for a machine-readable payload.

Exit codes:
    0  configured + probe ran successfully
    0  unconfigured (clear warning printed, no traceback)
    1  configured but a hard failure (network / unexpected) occurred
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _expiry_to_occ_date(expiry: str) -> str:
    """Convert YYYY-MM-DD → YYMMDD (the OCC 21-char date segment)."""
    parts = expiry.split("-")
    if len(parts) != 3 or len(parts[0]) != 4:
        raise ValueError(f"expiry must be YYYY-MM-DD, got {expiry!r}")
    return f"{parts[0][2:]}{parts[1]}{parts[2]}"


def _build_occ_symbol(symbol: str, expiry: str, strike: float, right: str) -> str:
    """Build an OCC 21-char option symbol.

    Format (per OCC 2010):  ROOT(6, space-padded) YYMMDD R STRIKE(8, 0.001 units)
    Example: SPXW  260620C00050000  →  "SPXW  260620C00050000"
    """
    root = symbol.upper().ljust(6, " ")
    yymmdd = _expiry_to_occ_date(expiry)
    r = right.upper()
    if r not in ("C", "P"):
        raise ValueError(f"right must be C or P, got {right!r}")
    # OCC strike is integer milli-dollars (8 digits), padded with zeros.
    strike_int = round(float(strike) * 1000)
    if strike_int < 0 or strike_int > 99999999:
        raise ValueError(f"strike {strike} outside OCC encodable range")
    return f"{root}{yymmdd}{r}{strike_int:08d}"


def _print(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        # Plain-text rendering — readable for terminal use.
        for k, v in payload.items():
            if isinstance(v, (dict, list)):
                print(f"{k}: {json.dumps(v, default=str)}")
            else:
                print(f"{k}: {v}")


# ──────────────────────────────────────────────────────────────────────
# subcommands
# ──────────────────────────────────────────────────────────────────────

def cmd_auth_only(probe, as_json: bool) -> int:                  # type: ignore[no-untyped-def]
    out = probe.login()
    out["mode"] = "auth-only"
    _print(out, as_json)
    return 0


def cmd_accounts(probe, as_json: bool) -> int:                   # type: ignore[no-untyped-def]
    auth = probe.login()
    if not auth.get("auth_success"):
        _print({"mode": "accounts", **auth}, as_json)
        return 0
    out = probe.list_accounts()
    out["mode"] = "accounts"
    _print(out, as_json)
    return 0 if out.get("ok") else 1


def cmd_chain(probe, symbol: str, as_json: bool) -> int:         # type: ignore[no-untyped-def]
    auth = probe.login()
    if not auth.get("auth_success"):
        _print({"mode": "chain", **auth}, as_json)
        return 0
    out = probe.get_option_chain_summary(symbol)
    out["mode"] = "chain"
    _print(out, as_json)
    return 0 if out.get("ok") else 1


def cmd_quotes(
    probe,                                                       # type: ignore[no-untyped-def]
    *,
    symbol: str,
    expiry: str,
    strikes: list[float],
    right: str,
    as_json: bool,
) -> int:
    auth = probe.login()
    if not auth.get("auth_success"):
        _print({"mode": "quotes", **auth}, as_json)
        return 0
    occ_syms = [_build_occ_symbol(symbol, expiry, k, right) for k in strikes]
    out = probe.get_option_quotes(occ_syms)
    out["mode"] = "quotes"
    out["requested_symbols"] = occ_syms
    _print(out, as_json)
    return 0 if out.get("ok") else 1


def cmd_capabilities(probe, symbol: str, as_json: bool) -> int:  # type: ignore[no-untyped-def]
    caps = probe.capabilities_summary(symbol)
    caps["mode"] = "capabilities"
    _print(caps, as_json)
    return 0


def cmd_status_only(probe, as_json: bool) -> int:                # type: ignore[no-untyped-def]
    # No HTTP call — just print sanitized config view.
    _print({**probe.status().sanitize(), "mode": "status"}, as_json)
    return 0


def cmd_config(probe, as_json: bool) -> int:                     # type: ignore[no-untyped-def]
    """Sanitized config dump — no HTTP call.

    Prints which credential blocks are present (without values), the
    resolved env / base_url / scopes, and most importantly the safety
    gate state. Useful for confirming `.env` is wired correctly BEFORE
    attempting any network call.
    """
    summary = probe.config_summary()
    summary["mode"] = "config"
    _print(summary, as_json)
    return 0


# ──────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_tastytrade",
        description="Sanitized read-only Tastytrade capability probe (Phase 3).",
        epilog=(
            "Safety: this script NEVER posts orders, NEVER opens the DXLink "
            "WebSocket, and NEVER prints tokens / passwords / account numbers."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--config",      action="store_true",
                      help="Print the sanitized probe config — auth_mode, scopes, "
                           "trade_scope_present, safety-gate state, missing fields. "
                           "NO HTTP call. Safe to run anytime.")
    mode.add_argument("--auth-only",   action="store_true",
                      help="Login only (OAuth refresh OR /sessions per env). "
                           "Sanitized output — no token printed.")
    mode.add_argument("--accounts",    action="store_true",
                      help="Login + GET /customers/me/accounts (redacted ids)")
    mode.add_argument("--chain",       action="store_true",
                      help="Login + GET /option-chains/{symbol}/nested (summary only)")
    mode.add_argument("--quotes",      action="store_true",
                      help="Login + GET /market-data/by-type for specified strikes")
    mode.add_argument("--capabilities", action="store_true",
                      help="Run all read-only sub-probes and report a capability matrix")

    parser.add_argument("--symbol",  default="SPX", help="Underlying symbol (default: SPX)")
    parser.add_argument("--expiry",  default=None,  help="YYYY-MM-DD (for --quotes)")
    parser.add_argument("--strikes", default="",    help="Comma-separated strikes (for --quotes)")
    parser.add_argument("--right",   default="C", choices=("C", "P"),
                        help="Option right for --quotes (default: C)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args(argv)

    from src.providers.quotes.tasty_probe import TastyProbeClient, config_from_env
    from src.utils.config import load_config

    # Load .env via the existing config machinery (idempotent).
    load_config(REPO_ROOT)
    cfg = config_from_env()
    probe = TastyProbeClient(cfg)

    # `--config` is the safe diagnostic — it makes ZERO HTTP calls and
    # works without credentials. Run it BEFORE the unconfigured short-
    # circuit so the user can always inspect their .env wiring.
    if args.config:
        return cmd_config(probe, args.json)

    # Unconfigured short-circuit — exit 0 with a clean warning.
    if not cfg.is_configured():
        warn = (
            "TASTY_USERNAME / TASTY_PASSWORD not set. Add them to .env "
            "(see .env.example > 'Phase 3: Tastytrade capability probe'). "
            "Probe will not contact the network."
        )
        if any([args.auth_only, args.accounts, args.chain, args.quotes, args.capabilities]):
            _print({"warning": warn, "status": probe.status().sanitize()}, args.json)
        else:
            _print({"warning": warn, "status": probe.status().sanitize(), "mode": "status"},
                   args.json)
        return 0

    if args.auth_only:
        return cmd_auth_only(probe, args.json)
    if args.accounts:
        return cmd_accounts(probe, args.json)
    if args.chain:
        return cmd_chain(probe, args.symbol, args.json)
    if args.quotes:
        if not args.expiry or not args.strikes:
            print("ERROR: --quotes requires --expiry YYYY-MM-DD and "
                  "--strikes K1,K2,...", file=sys.stderr)
            return 2
        try:
            strike_list = [float(s.strip()) for s in args.strikes.split(",") if s.strip()]
        except ValueError as exc:
            print(f"ERROR: bad --strikes value ({exc})", file=sys.stderr)
            return 2
        return cmd_quotes(
            probe, symbol=args.symbol, expiry=args.expiry,
            strikes=strike_list, right=args.right, as_json=args.json,
        )
    if args.capabilities:
        return cmd_capabilities(probe, args.symbol, args.json)

    # No mode picked — print sanitized status. Useful CI sanity check.
    return cmd_status_only(probe, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
