"""Smoke test for the read-only ZerσSigma API StructureProvider.

Usage:
    python -m scripts.smoke_zs_api                  # SPX, .env-driven config
    python -m scripts.smoke_zs_api --symbol SPY
    python -m scripts.smoke_zs_api --json           # machine-readable output

What it does:
    1. Loads `.env` + config/providers.yaml (via the existing AppConfig).
    2. Builds `ZeroSigmaApiStructureProvider` from those settings.
    3. Calls `get_snapshot(symbol)` exactly once and prints a SANITIZED
       summary — never tokens, passwords, service keys, headers, or any
       raw env values.

Exit codes:
    0 — snapshot returned (even with missing fields under public_only)
    0 — provider not configured (auth_mode=none) → printed as warning
    1 — provider IS configured but the live call failed (network, parse,
        unexpected error). Printed without a Python traceback.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ──────────────────────────────────────────────────────────────────────
# safe printers — these are the ONLY places we render the result
# ──────────────────────────────────────────────────────────────────────

# Allow-list of fields we render. Anything not in this list is NEVER
# touched by the printer, so a future careless field addition to
# `provider.status()` cannot accidentally leak.
_SAFE_STATUS_FIELDS = (
    "provider", "base_url", "auth_mode", "configured", "public_only",
    "last_status_code", "last_error", "last_missing_fields",
    "subscription_active",
    "exposure_series_enabled", "exposure_series_effective",
    "ddoi_enabled",
)

_SAFE_EXPOSURE_FIELDS = (
    "total_gex_bn", "total_vex_bn", "da_gex_signed",
    "maxvol", "gamma_regime", "gamma_flip",
    "put_ceiling_2k", "put_ceiling_5k",
    "call_floor_2k", "call_floor_5k",
    "ddoi_pin", "call_wall", "put_wall",
)


def _sanitize_status(status: dict) -> dict:
    return {k: status.get(k) for k in _SAFE_STATUS_FIELDS if k in status}


def _sanitize_snapshot(snap, status: dict) -> dict:
    e = snap.exposures
    return {
        "provider": status.get("provider"),
        "configured": status.get("configured"),
        "auth_mode": status.get("auth_mode"),
        "public_only": status.get("public_only"),
        "symbol": snap.symbol,
        "spot": snap.spot,
        "expiry": snap.expiry,
        "dte": snap.dte,
        "quote_ts": snap.quote_ts.isoformat() if snap.quote_ts else None,
        "exposures": {f: getattr(e, f, None) for f in _SAFE_EXPOSURE_FIELDS},
        "missing_fields": (snap.raw or {}).get("missing_fields") or [],
        "subscription_active": (snap.raw or {}).get("subscription_active"),
        "last_status_code": status.get("last_status_code"),
    }


def _render_text(payload: dict) -> str:
    """Plain-text summary. Mirrors the JSON shape but human-readable."""
    lines: list[str] = []
    lines.append(f"provider:     {payload.get('provider')}")
    lines.append(f"auth_mode:    {payload.get('auth_mode')}")
    lines.append(f"configured:   {payload.get('configured')}")
    lines.append(f"public_only:  {payload.get('public_only')}")
    lines.append(f"symbol:       {payload.get('symbol')}")
    lines.append(f"spot:         {payload.get('spot')}")
    lines.append(f"expiry:       {payload.get('expiry')} (DTE {payload.get('dte')})")
    lines.append(f"quote_ts:     {payload.get('quote_ts')}")
    lines.append(f"http_status:  {payload.get('last_status_code')}")
    lines.append("exposures:")
    for f, v in (payload.get("exposures") or {}).items():
        lines.append(f"  {f:<16} {v}")
    missing = payload.get("missing_fields") or []
    lines.append(f"missing_fields ({len(missing)}): {', '.join(missing) if missing else '—'}")
    lines.append(f"subscription_active: {payload.get('subscription_active')}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sanitized smoke test for ZeroSigmaApiStructureProvider"
    )
    parser.add_argument("--symbol", default=None,
                        help="symbol to fetch (default: ZS_PRIMARY_SYMBOL or SPX)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    args = parser.parse_args()

    from src.providers.structure.factory import build_structure_provider
    from src.providers.structure.zerosigma_api import ZeroSigmaApiStructureProvider
    from src.utils.config import load_config
    from src.utils.logging import get_logger

    log = get_logger("smoke")
    cfg = load_config(REPO_ROOT)
    symbol = (args.symbol or cfg.scanner.get("symbols", ["SPX"])[0]).upper()

    # Force the zerosigma_api provider (so the user can smoke-test even if
    # they left ZS_STRUCTURE_PROVIDER=stub). Factory falls back to stub on
    # construction failure, but instantiation of the real provider with no
    # creds succeeds — status() simply reports configured=False.
    provider, resolved = build_structure_provider(cfg, override="zerosigma_api")

    if not isinstance(provider, ZeroSigmaApiStructureProvider):
        # Factory degraded to stub (e.g. import error). Treat as warning.
        log.warning(
            "Factory could not instantiate ZeroSigmaApiStructureProvider; "
            "falling back to %s. Check config/providers.yaml entries.", resolved,
        )
        print("WARNING: zerosigma_api provider unavailable; "
              "falling back to stub. See logs above.", file=sys.stderr)
        return 0

    status = provider.status()
    safe_status = _sanitize_status(status)

    # If auth_mode=none → nothing to test. Print a clear notice and exit 0.
    if not status.get("configured"):
        msg = (
            f"ZS API provider is NOT configured (auth_mode={status.get('auth_mode')!r}). "
            f"Set ZS_API_BASE_URL + ZS_API_AUTH_MODE in .env to run a real smoke test."
        )
        if args.json:
            print(json.dumps({"warning": msg, "status": safe_status}, indent=2))
        else:
            print(f"WARNING: {msg}")
            print()
            print(_render_text({**safe_status, "symbol": symbol, "exposures": {}}))
        return 0

    # configured — try the snapshot once.
    try:
        snap = provider.get_snapshot(symbol)
    except Exception as exc:
        # Clean warning, no traceback. Never print exc args verbatim if they
        # might contain a URL with a token (httpx doesn't normally, but be
        # defensive — render the exception TYPE only).
        msg = (
            f"ZS API call failed: {type(exc).__name__}. "
            f"base_url={safe_status.get('base_url')!r}, "
            f"auth_mode={safe_status.get('auth_mode')!r}. "
            f"Check connectivity + credentials in .env."
        )
        if args.json:
            print(json.dumps({"error": msg, "status": safe_status}, indent=2),
                  file=sys.stderr)
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    payload = _sanitize_snapshot(snap, provider.status())
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(_render_text(payload))
        if payload.get("public_only") and "put_ceiling_2k" in (payload.get("missing_fields") or []):
            print()
            print("NOTE: public_only mode skips /exposure/series → "
                  "PUT_CEILING / CALL_FLOOR / MaxVol are intentionally None.")
            print("      Switch ZS_API_AUTH_MODE to bearer/login/service_token "
                  "and set ZS_API_ENABLE_EXPOSURE_SERIES=true to populate them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
