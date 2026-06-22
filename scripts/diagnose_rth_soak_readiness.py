"""Sanitized readiness diagnostic for an RTH local-paper soak."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.alerts.router import AlertPreferences
from src.config.strategy_profiles import load_profile_file
from src.paper.models import PaperLifecycleConfig
from src.providers.quotes import tasty_diagnostics
from src.providers.structure.factory import build_structure_provider
from src.providers.structure.greek_probe import DEFAULT_PROBE_METRICS, probe_configured_provider
from src.regime.opex import classify_opex_context
from src.utils.config import load_config
from src.utils.time import now_et

REPO_ROOT = Path(__file__).resolve().parents[1]


def _configure_cli_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _output_writable(root: Path) -> tuple[bool, str | None]:
    destination = root / "latest"
    try:
        destination.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination, prefix=".readiness_", delete=True):
            pass
    except OSError as exc:
        return False, type(exc).__name__
    return True, None


def _structure_probe(symbol: str, provider_name: str) -> dict[str, Any]:
    cfg = load_config(REPO_ROOT)
    try:
        provider, resolved = build_structure_provider(cfg, override=provider_name)
        report = probe_configured_provider(
            provider, symbol=symbol, metrics=DEFAULT_PROBE_METRICS
        )
    except Exception as exc:
        return {
            "provider": provider_name,
            "resolved_provider": provider_name,
            "status": "unavailable",
            "configured": False,
            "available_metrics": [],
            "missing_metrics": list(DEFAULT_PROBE_METRICS),
            "error_type": type(exc).__name__,
        }
    return {**report, "resolved_provider": resolved}


def _quote_probe(symbol: str, dte: int, quote_provider_name: str) -> dict[str, Any]:
    if quote_provider_name != "tastytrade":
        return {
            "quote_provider": quote_provider_name,
            "configured": False,
            "blocker": "quote_provider_not_tastytrade",
            "final_status": "RTH soak requires the read-only Tasty quote provider.",
            "chain_returned": False,
            "validation_passed_count": 0,
        }
    report = tasty_diagnostics.diagnose_from_env(symbol=symbol, target_dte=dte)
    keep = (
        "configured", "auth_mode", "auth_attempted", "auth_success", "resolved_root",
        "resolved_expiration", "has_0dte_today", "chain_returned", "quote_count",
        "validation_passed_count", "validation_failed_count", "validation_blockers",
        "stale_count", "invalid_bid_ask_count", "missing_strikes", "blocker",
        "final_status", "last_error",
    )
    return {"quote_provider": quote_provider_name, **{key: report.get(key) for key in keep}}


def _profiles(profile_ids: list[str], dte: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile_id in profile_ids:
        result = load_profile_file(profile_id)
        profile = result.profile
        target_dte = getattr(profile, "target_dte", None)
        rows.append({
            "profile_id": profile_id,
            "valid": result.ok,
            "target_dte": target_dte,
            "dte_matches_request": target_dte == dte,
            "errors": list(result.errors),
        })
    return rows


def sample_fixture_readiness(profile_ids: list[str] | None = None) -> dict[str, Any]:
    profiles = profile_ids or ["morning_5k_call_tp75_control"]
    return {
        "generated_at": "2026-06-22T09:20:00-04:00",
        "symbol": "SPX",
        "dte": 0,
        "profiles": [
            {"profile_id": value, "valid": True, "target_dte": 0,
             "dte_matches_request": True, "errors": []}
            for value in profiles
        ],
        "structure": {
            "provider": "zerosigma_api", "configured": True, "reachable": True,
            "status": "ok",
        },
        "greeks": {
            "status": "Available", "available_fields": list(DEFAULT_PROBE_METRICS),
            "missing_fields": [], "source_endpoint": "/api/v1/market/snapshot",
        },
        "daily_path": {
            "ready_to_accumulate": True, "current_code": "R0_PROVISIONAL",
            "note": "The first live observation starts the chronological path.",
        },
        "context": {
            "ready": True, "code": "R6_POST_OPEX_GAMMA_RESET",
            "label": "Post-OpEx Gamma Reset", "expiration_context": "NORMAL",
            "days_to_opex": -3,
        },
        "quotes": {
            "provider": "tastytrade", "configured": True, "auth_mode": "oauth",
            "root": "SPXW", "expiration": "2026-06-22", "chain_returned": True,
            "quote_count": 10, "validation_passed_count": 10,
            "validation_failed_count": 0, "validation_blockers": {},
            "missing_strikes": [], "ready": True, "blocker": None,
        },
        "alerts": {
            "delivery_enabled": False, "cockpit_enabled": True,
            "pushover_enabled": False, "voice_enabled": False,
            "cooldown_seconds": 300,
        },
        "paper_lifecycle": {
            "enabled": True, "contracts": 1, "take_profit_pct": 0.5,
            "stop_loss_pct": 1.5, "exit_on_eod": True, "eod_exit_time": "15:55",
            "regime_exit_enabled": True, "execution_mode": "local_paper_lifecycle_only",
        },
        "outputs": {"writable": True, "error_type": None},
        "can_start": True,
        "status": "READY",
        "blockers": [],
        "warnings": ["External alert delivery is disabled; local journals remain active."],
        "sanitized": True,
        "contains_secret_values": False,
        "no_broker_order_sent": True,
        "no_order_preview": True,
    }


def collect_rth_soak_readiness(
    *,
    profile_ids: list[str],
    symbol: str,
    dte: int,
    structure_provider_name: str = "zerosigma_api",
    quote_provider_name: str = "tastytrade",
    output_root: Path | str = "outputs/readiness",
    now: datetime | None = None,
    structure_probe: Callable[[str, str], dict[str, Any]] = _structure_probe,
    quote_probe: Callable[[str, int, str], dict[str, Any]] = _quote_probe,
) -> dict[str, Any]:
    load_config(REPO_ROOT)
    observed_at = now or now_et()
    profile_rows = _profiles(profile_ids, dte)
    structure_raw = structure_probe(symbol, structure_provider_name)
    quote_raw = quote_probe(symbol, dte, quote_provider_name)
    available = list(structure_raw.get("available_metrics") or [])
    missing = list(structure_raw.get("missing_metrics") or [])
    greek_status = "Unavailable" if not available else "Degraded" if missing else "Available"
    opex = classify_opex_context(observed_at.date())
    alerts = AlertPreferences.from_env()
    lifecycle = PaperLifecycleConfig.from_env()
    root = Path(output_root)
    if not root.is_absolute():
        root = REPO_ROOT / root
    output_ok, output_error = _output_writable(root)

    structure_ok = (
        structure_raw.get("status") == "ok"
        and structure_raw.get("resolved_provider", structure_raw.get("provider"))
        == "zerosigma_api"
    )
    da_gex_ready = "da_gex" in available
    quote_ready = bool(
        quote_raw.get("configured")
        and quote_raw.get("chain_returned")
        and int(quote_raw.get("validation_passed_count") or 0) > 0
        and not quote_raw.get("blocker")
    )
    profiles_ready = bool(profile_rows) and all(
        row["valid"] and row["dte_matches_request"] for row in profile_rows
    )
    context_ready = opex.code != "R_UNKNOWN"
    blockers: list[str] = []
    if not profiles_ready:
        blockers.append("profile_invalid_or_dte_mismatch")
    if not structure_ok:
        blockers.append("zerosigma_structure_unavailable")
    if not da_gex_ready:
        blockers.append("da_gex_unavailable")
    if not context_ready:
        blockers.append("opex_context_unknown")
    if not quote_ready:
        blockers.append(str(quote_raw.get("blocker") or "tasty_quotes_not_ready"))
    if not lifecycle.enabled:
        blockers.append("paper_lifecycle_disabled")
    if not output_ok:
        blockers.append("output_path_not_writable")
    warnings: list[str] = []
    if missing and available:
        warnings.append("Some Greek fields are missing; diagnostics will be degraded.")
    if not alerts.delivery_enabled:
        warnings.append("External alert delivery is disabled; local journals remain active.")
    if _env_bool("ALERTS_PUSHOVER_ENABLED"):
        warnings.append("Pushover delivery is enabled for this environment.")
    if _env_bool("ALERTS_VOICE_ENABLED"):
        warnings.append("Voice queue delivery is enabled for this environment.")

    return {
        "generated_at": observed_at.isoformat(),
        "symbol": symbol,
        "dte": dte,
        "profiles": profile_rows,
        "structure": {
            "provider": structure_raw.get("resolved_provider")
            or structure_raw.get("provider"),
            "configured": bool(structure_raw.get("configured")),
            "reachable": structure_raw.get("status") == "ok",
            "status": structure_raw.get("status"),
            "error_type": structure_raw.get("error_type"),
        },
        "greeks": {
            "status": greek_status,
            "available_fields": available,
            "missing_fields": missing,
            "source_endpoint": (
                structure_raw.get("endpoints", {})
                .get("/api/v1/market/snapshot", {})
                .get("source")
            ),
        },
        "daily_path": {
            "ready_to_accumulate": da_gex_ready,
            "current_code": "R0_PROVISIONAL" if da_gex_ready else "R0_UNAVAILABLE",
            "note": (
                "The first live observation starts the chronological path."
                if da_gex_ready else "DA-GEX is unavailable; the daily path cannot start."
            ),
        },
        "context": {
            "ready": context_ready,
            "code": opex.code,
            "label": opex.label,
            "expiration_context": opex.expiration_context,
            "days_to_opex": opex.days_to_opex,
        },
        "quotes": {
            "provider": quote_raw.get("quote_provider"),
            "configured": bool(quote_raw.get("configured")),
            "auth_mode": quote_raw.get("auth_mode"),
            "root": quote_raw.get("resolved_root"),
            "expiration": quote_raw.get("resolved_expiration"),
            "chain_returned": bool(quote_raw.get("chain_returned")),
            "quote_count": quote_raw.get("quote_count", 0),
            "validation_passed_count": quote_raw.get("validation_passed_count", 0),
            "validation_failed_count": quote_raw.get("validation_failed_count", 0),
            "validation_blockers": quote_raw.get("validation_blockers") or {},
            "stale_count": quote_raw.get("stale_count", 0),
            "invalid_bid_ask_count": quote_raw.get("invalid_bid_ask_count", 0),
            "missing_strikes": quote_raw.get("missing_strikes") or [],
            "ready": quote_ready,
            "blocker": quote_raw.get("blocker"),
            "status_note": quote_raw.get("final_status"),
            "error_type": quote_raw.get("last_error"),
        },
        "alerts": {
            "delivery_enabled": alerts.delivery_enabled,
            "cockpit_enabled": _env_bool("ALERTS_COCKPIT_ENABLED", True),
            "pushover_enabled": _env_bool("ALERTS_PUSHOVER_ENABLED"),
            "voice_enabled": _env_bool("ALERTS_VOICE_ENABLED"),
            "cooldown_seconds": alerts.default_cooldown_seconds,
        },
        "paper_lifecycle": {
            **lifecycle.to_dict(),
            "execution_mode": "local_paper_lifecycle_only",
        },
        "outputs": {
            "root": str(root),
            "writable": output_ok,
            "error_type": output_error,
        },
        "can_start": not blockers,
        "status": "READY" if not blockers else "BLOCKED",
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": warnings,
        "sanitized": True,
        "contains_secret_values": False,
        "no_broker_order_sent": True,
        "no_order_preview": True,
    }


def _markdown(report: dict[str, Any]) -> str:
    profile_text = ", ".join(row["profile_id"] for row in report["profiles"])
    return "\n".join([
        "# RTH Soak Readiness",
        "",
        "SANITIZED READ-ONLY CHECK - NO BROKER ORDER SENT.",
        "",
        f"- Status: **{report['status']}**",
        f"- Symbol / DTE: `{report['symbol']}` / `{report['dte']}`",
        f"- Profiles: {profile_text}",
        f"- ZeroSigma structure: {report['structure']['status']}",
        f"- Greek data: {report['greeks']['status']}",
        f"- DA-GEX path: {report['daily_path']['current_code']}",
        f"- OpEx context: {report['context']['code']}",
        f"- Tasty quotes ready: {report['quotes']['ready']}",
        f"- Paper lifecycle enabled: {report['paper_lifecycle']['enabled']}",
        f"- Output path writable: {report['outputs']['writable']}",
        f"- Blockers: {', '.join(report['blockers']) or 'none'}",
        f"- Warnings: {', '.join(report['warnings']) or 'none'}",
        "",
    ])


def write_readiness_report(
    report: dict[str, Any], output_root: Path | str = "outputs/readiness"
) -> Path:
    root = Path(output_root)
    if not root.is_absolute():
        root = REPO_ROOT / root
    destination = root / "latest"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "rth_soak_readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (destination / "rth_soak_readiness.md").write_text(_markdown(report), encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    _configure_cli_encoding()
    parser = argparse.ArgumentParser(
        description="Sanitized RTH local-paper soak readiness diagnostic."
    )
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--profiles", default="")
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--dte", type=int, default=0)
    parser.add_argument("--structure-provider", default="zerosigma_api")
    parser.add_argument("--quote-provider", default="tastytrade")
    parser.add_argument("--output-dir", default="outputs/readiness")
    parser.add_argument("--fixture", choices=("sample",), default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    profile_ids = [*args.profile]
    profile_ids.extend(value.strip() for value in args.profiles.split(",") if value.strip())
    profile_ids = list(dict.fromkeys(profile_ids))
    if not profile_ids:
        parser.error("at least one --profile or --profiles value is required")
    report = (
        sample_fixture_readiness(profile_ids)
        if args.fixture == "sample"
        else collect_rth_soak_readiness(
            profile_ids=profile_ids,
            symbol=args.symbol.strip().upper(),
            dte=args.dte,
            structure_provider_name=args.structure_provider,
            quote_provider_name=args.quote_provider,
            output_root=args.output_dir,
        )
    )
    destination = write_readiness_report(report, args.output_dir)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print("RTH local-paper soak readiness (sanitized, read-only)")
        print(f"  status: {report['status']}")
        print(f"  profiles: {', '.join(row['profile_id'] for row in report['profiles'])}")
        print(f"  Greek data: {report['greeks']['status']}")
        print(f"  Tasty quotes ready: {report['quotes']['ready']}")
        print(f"  blockers: {', '.join(report['blockers']) or 'none'}")
        print(f"  report: {destination.resolve()}")
    print("No secrets shown. No broker execution. No order preview.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
