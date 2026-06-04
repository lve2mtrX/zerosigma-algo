"""Tastytrade quote-path diagnostic CLI — READ-ONLY.

Walks config → auth/session → root → expiry/DTE → chain → quote validation and
prints exactly which stage broke. Reuses the existing read-only `TastyProbeClient`
+ `QuoteValidation`. No orders, no order preview, no secrets.

Usage:
    python -m scripts.diagnose_tasty_quotes --symbol SPX --dte 0
    python -m scripts.diagnose_tasty_quotes --symbol SPX --dte 0 --spot-hint 7585 --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.providers.quotes import tasty_diagnostics as diag

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Load the repo `.env` so TASTY_* (OAuth) + QUOTE_PROVIDER are visible.

    The diagnostic reads ``os.environ``; nothing else loads `.env` on the CLI
    path. ``override=False`` keeps any var already exported in the shell. Never
    prints or returns any value.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO_ROOT / ".env", override=False)
    except Exception:           # dotenv missing / unreadable .env → best-effort
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only Tastytrade quote-path diagnostic (no orders, no secrets).")
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--dte", type=int, default=0, help="target DTE (0 = today's 0DTE)")
    ap.add_argument("--spot-hint", dest="spot_hint", type=float, default=None,
                    help="optional ATM center for the probe ladder (e.g. the Zσ structure spot)")
    ap.add_argument("--json", action="store_true", help="print the full sanitized JSON dict")
    args = ap.parse_args(argv)

    _load_env()
    symbol = (args.symbol or "SPX").strip().upper()
    print("ZerσSigma Algo — Tasty quote diagnostics (read-only, no orders, no secrets)")
    print(f"symbol={symbol}  target_dte={args.dte}")

    result = diag.diagnose_from_env(
        symbol=symbol, target_dte=int(args.dte), spot_hint=args.spot_hint,
    )

    width = max(len(label) for label, _ in diag.summary_rows(result))
    for label, value in diag.summary_rows(result):
        print(f"  {label.ljust(width)} : {value}")

    if args.json:
        print("\n--- full sanitized diagnostic ---")
        print(json.dumps(result, indent=2, default=str))

    print("\nNo secrets shown. No broker execution. No order preview.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
