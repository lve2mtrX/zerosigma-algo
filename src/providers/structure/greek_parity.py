"""Reproducible Phase 11F audit facts from the Dashboard/API code paths."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GreekParityField:
    metric: str
    dashboard_behavior: str
    api_endpoint: str
    api_field: str
    api_shape: str
    algo_before_phase11f: str
    phase11f_status: str
    units: str
    notes: str = ""


PARITY_FIELDS: tuple[GreekParityField, ...] = (
    GreekParityField("raw_gex", "API chain/Exposure Lab", "/market/snapshot; /exposure/series", "total_raw_gex_1pct; c/p_raw_gex_1pct", "aggregate + per-strike", "aggregate alias only", "wired", "$Bn per 1% spot move"),
    GreekParityField("da_gex", "API chain/Exposure Lab", "/market/snapshot; /exposure/series", "total_da_gex_1pct; c/p_da_gex_1pct", "aggregate + per-strike", "aggregate only", "wired", "$Bn per 1% spot move"),
    GreekParityField("dex", "API chain/Exposure Lab", "/market/snapshot; /exposure/series", "total_dex_1pct; c/p_dex_1pct", "aggregate + per-strike", "read then discarded", "wired", "$Bn per 1% spot move"),
    GreekParityField("vex", "API chain/Exposure Lab", "/market/snapshot; /exposure/series", "total_vex_1vol; c/p_vex_1vol", "aggregate + per-strike", "aggregate only", "wired", "$Bn per +1 volatility point"),
    GreekParityField("cex", "API chain/Exposure Lab", "/market/snapshot; /exposure/series", "total_cex; c/p_cex", "aggregate + per-strike", "read then discarded", "wired", "$Bn per worker charm time unit"),
    GreekParityField("theta", "API chain chart", "/market/snapshot", "c_theta; p_theta", "per-strike raw", "ignored", "wired per-strike", "raw worker Greek", "No aggregate theta field exists today."),
    GreekParityField("vanna", "API chain chart; local fallback if absent", "/market/snapshot", "c_vanna; p_vanna", "per-strike raw", "ignored", "wired per-strike", "raw worker Greek", "Dashboard Black-Scholes backfill is client-side and is not copied."),
    GreekParityField("charm", "API chain chart; local fallback if absent", "/market/snapshot", "c_charm; p_charm", "per-strike raw", "ignored", "wired per-strike", "raw worker Greek", "Dashboard Black-Scholes backfill is client-side and is not copied."),
    GreekParityField("vomma", "API chain chart; local fallback if absent", "/market/snapshot", "c_vomma; p_vomma", "per-strike raw", "ignored", "wired per-strike", "raw worker Greek"),
    GreekParityField("speed", "API chain chart; local fallback if absent", "/market/snapshot", "c_speed; p_speed", "per-strike raw", "ignored", "wired per-strike", "raw worker Greek"),
    GreekParityField("zomma", "API chain chart; local fallback if absent", "/market/snapshot", "c_zomma; p_zomma", "per-strike raw", "ignored", "wired per-strike", "raw worker Greek"),
    GreekParityField("speed_exp", "Worker-baked chain column", "/market/snapshot", "c/p_speed_exp", "per-strike exposure", "ignored", "wired per-strike", "$Bn per squared 1% spot move"),
    GreekParityField("vomma_exp", "Worker-baked chain column", "/market/snapshot", "c/p_vomma_exp", "per-strike exposure", "ignored", "wired per-strike", "$Bn per squared 1% volatility move"),
    GreekParityField("zomma_exp", "Worker-baked chain column", "/market/snapshot", "c/p_zomma_exp", "per-strike exposure", "ignored", "wired per-strike", "$Bn per 1% spot x 1% volatility move"),
    GreekParityField("iv", "API chain and IV surface", "/market/snapshot", "c_iv; p_iv; straddle_iv_meta", "per-strike + metadata", "quote IV only", "wired diagnostics", "decimal implied volatility"),
    GreekParityField("vex_skew", "Worker-baked chain chart", "/market/snapshot", "c/p_vex_skew_1vol", "per-strike exposure", "ignored", "wired per-strike", "$Bn per +1 volatility point"),
    GreekParityField("volume", "API chain/Volume module", "/market/snapshot; /exposure/series", "c_volume; p_volume", "per-strike", "separate series only", "wired snapshot fallback", "contracts"),
    GreekParityField("open_interest", "API chain", "/market/snapshot", "c_oi; p_oi", "per-strike", "ignored by structure provider", "wired diagnostics", "contracts"),
    GreekParityField("maxvol", "Derived from API volume rows", "/market/snapshot", "max_call/put_vol_strike; c/p_volume", "aggregate strike + per-strike", "partial fallback", "wired with combined volume", "strike / contracts"),
    GreekParityField("gamma_structure", "API metrics", "/market/snapshot", "gamma.flip; cluster_primary; cluster_secondary", "aggregate levels", "wired", "wired", "strike"),
    GreekParityField("ddoi", "Separate history module", "/exposure/ddoi", "records[]", "history records", "placeholder only", "deferred", "provider-defined", "Available only when API Spaces storage and subscription are configured."),
    GreekParityField("volume_weighted_greeks", "Computed in Dashboard from raw Greek x volume", "none", "none", "client-derived", "unavailable", "deferred", "derived", "Not a baked API field."),
)


def write_greek_parity_audit(output_dir: Path | str) -> tuple[Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / "greek_api_parity_fields.csv"
    md_path = directory / "greek_api_parity_audit.md"

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(PARITY_FIELDS[0])))
        writer.writeheader()
        writer.writerows(asdict(field) for field in PARITY_FIELDS)

    available = [field.metric for field in PARITY_FIELDS if field.phase11f_status.startswith("wired")]
    deferred = [field.metric for field in PARITY_FIELDS if field.phase11f_status == "deferred"]
    lines = [
        "# Greek API Parity Audit",
        "",
        "## Finding",
        "",
        "The lower-tier Greeks are not categorically missing from ZeroSigma API data. "
        "The Dashboard worker writes them into canonical `chain_json.rows`, "
        "`/api/v1/market/snapshot` returns that chain, and the Dashboard reads it. "
        "The Dashboard also performs a client-side Black-Scholes backfill when raw "
        "vanna/charm/vomma/speed/zomma fields are absent; Phase 11F does not copy that "
        "fallback or invent missing values.",
        "",
        "## Endpoints",
        "",
        "- `/api/v1/market/snapshot`: public consolidated spot, metrics, and canonical chain.",
        "- `/api/v1/market/exposures`: aggregate worker metrics.",
        "- `/api/v1/market/chain`: canonical per-strike chain fields.",
        "- `/api/v1/exposure/series`: subscription series for raw_gex, da_gex, dex, vex, cex, and volume.",
        "- `/api/v1/exposure/ddoi`: subscription/Spaces-backed DDOI history.",
        "",
        "## Phase 11F wiring",
        "",
        f"Verified and wired: {', '.join(available)}.",
        f"Deferred because no equivalent baked field/contract is available: {', '.join(deferred)}.",
        "Aggregate theta and aggregate raw lower-tier Greeks are intentionally not synthesized.",
        "",
        "## Field table",
        "",
        "See `greek_api_parity_fields.csv` for the field-level source, shape, units, and disposition.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path
