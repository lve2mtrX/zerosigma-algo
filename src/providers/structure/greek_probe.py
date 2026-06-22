"""Sanitized field-shape probe over the configured ZeroSigma structure provider."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.providers.structure.zerosigma_api import GREEK_API_UNITS

DEFAULT_PROBE_METRICS = (
    "raw_gex", "da_gex", "dex", "vex", "cex", "theta", "charm", "vanna",
    "vomma", "speed", "zomma", "volume", "oi", "iv", "iv_skew", "vex_skew",
    "speed_exp", "vomma_exp", "zomma_exp",
)


def _count(values: Iterable[Any], *, nonzero: bool = False) -> int:
    count = 0
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if nonzero and number == 0:
            continue
        count += 1
    return count


def probe_configured_provider(
    provider: Any,
    *,
    symbol: str,
    metrics: Iterable[str] = DEFAULT_PROBE_METRICS,
) -> dict[str, Any]:
    requested = tuple(dict.fromkeys(str(metric).strip().lower() for metric in metrics if metric))
    status = provider.status() if hasattr(provider, "status") else {}
    report: dict[str, Any] = {
        "symbol": symbol.upper(),
        "provider": getattr(provider, "name", type(provider).__name__),
        "configured": bool(status.get("configured")),
        "auth_mode": status.get("auth_mode"),
        "requested_metrics": list(requested),
        "endpoints": {},
        "metrics": {},
        "available_metrics": [],
        "missing_metrics": [],
        "sanitized": True,
        "contains_raw_payload_values": False,
    }
    try:
        snapshot = provider.get_snapshot(symbol)
    except Exception as exc:
        report["status"] = "unavailable"
        report["error_type"] = type(exc).__name__
        report["endpoints"]["/api/v1/market/snapshot"] = {
            "status": "unavailable",
            "error_type": type(exc).__name__,
        }
        return report

    exposures = snapshot.exposures
    available = set(exposures.greek_api_available_fields)
    unavailable = exposures.greek_api_unavailable_reasons
    report["status"] = "ok"
    report["endpoints"]["/api/v1/market/snapshot"] = {
        "status": "ok",
        "source": exposures.greek_api_source_endpoint,
        "chain_metric_count": len(exposures.per_strike_greek_series),
    }
    for metric in requested:
        series = exposures.per_strike_greek_series.get(metric)
        aggregate_alias = {
            "raw_gex": "total_raw_gex_1pct",
            "da_gex": "total_da_gex_1pct",
            "dex": "total_dex_1pct",
            "vex": "total_vex_1vol",
            "cex": "total_cex",
        }.get(metric)
        is_available = (
            metric in available
            or bool(aggregate_alias and aggregate_alias in available)
            or (metric == "iv_skew" and bool(exposures.greek_api_iv_metadata))
        )
        shape: dict[str, Any] = {"kind": "unavailable", "strike_count": 0}
        if series:
            calls, puts = series.get("calls", ()), series.get("puts", ())
            shape = {
                "kind": "per_strike_split",
                "strike_count": len(series.get("strikes", ())),
                "call_non_null_count": _count(calls),
                "put_non_null_count": _count(puts),
                "call_nonzero_count": _count(calls, nonzero=True),
                "put_nonzero_count": _count(puts, nonzero=True),
            }
        elif metric == "iv_skew" and exposures.greek_api_iv_metadata:
            shape = {
                "kind": "metadata",
                "strike_count": 0,
                "field_count": len(exposures.greek_api_iv_metadata),
                "fields": sorted(exposures.greek_api_iv_metadata),
            }
        field_report = {
            "available": is_available,
            "source_endpoint": exposures.greek_api_source_endpoint,
            "unit": GREEK_API_UNITS.get(metric, "provider-defined"),
            "shape": shape,
            "aggregate_field": aggregate_alias,
            "unavailable_reason": None if is_available else (
                unavailable.get(metric)
                or unavailable.get(aggregate_alias or "")
                or "not_present_in_configured_api_response"
            ),
        }
        report["metrics"][metric] = field_report
        target = "available_metrics" if is_available else "missing_metrics"
        report[target].append(metric)
    return report
