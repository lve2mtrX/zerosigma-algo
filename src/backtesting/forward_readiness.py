"""Forward-paper readiness reports for Phase 10I.

These reports recommend a tiny local paper-test candidate set. They do not
approve production trading and do not create any broker/order path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config.strategy_profiles import StrategyProfile, load_profile_file

BENCHMARK_CANDIDATES = (
    (
        "morning_5k_call_tp75_control",
        "Primary benchmark: positive call-only control from the comparison research.",
    ),
    (
        "morning_2k_call_no_tp_control",
        "Secondary benchmark: positive call-only control with a different threshold/exit shape.",
    ),
)


def _profile_card(profile: StrategyProfile, why: str) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "why_included": why,
        "why_not_production_approved": (
            "Forward paper is observation only. Positive historical control results "
            "are benchmarks, not production approval."
        ),
        "entry_window": (
            f"{profile.entry_window_start or '—'} to {profile.entry_window_end or '—'} ET"
        ),
        "target_time": profile.target_time,
        "tp": (
            "none" if profile.take_profit_pct in (None, 0)
            else f"{profile.take_profit_pct:.0%} credit capture"
        ),
        "sl": (
            "none" if profile.stop_loss_pct in (None, 0)
            else f"{profile.stop_loss_pct:.0%} credit stop"
        ),
        "dte": profile.target_dte,
        "account_sizing_suggestion": "$10,000 / 1 contract to start; compare with $2,500 / 1.",
        "what_to_watch_live": [
            "quote state stays Quotes: Available during RTH",
            "required strikes are present",
            "actual fill/exit behavior versus historical replay",
            "drawdown after one loss and after clustered losses",
        ],
        "what_would_invalidate_it": [
            "stale, wide, missing-strike, auth, or no-chain quote state at entry",
            "loss behavior materially worse than backtest/stress expectations",
            "profile/source mismatch in Run Strategy readiness",
            "manual review finds a strategy-math or data-quality defect",
        ],
        "no_broker_execution": True,
        "no_order_preview": True,
    }


def build_forward_readiness(
    *,
    stress_recommendation: dict[str, Any] | None = None,
    stress_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    for profile_id, why in BENCHMARK_CANDIDATES:
        loaded = load_profile_file(profile_id)
        if loaded.ok and loaded.profile is not None:
            profiles.append(_profile_card(loaded.profile, why))
    if (
        stress_recommendation
        and stress_recommendation.get("freeze_eligible")
        and isinstance(stress_profile, dict)
    ):
        try:
            candidate = StrategyProfile.from_dict(stress_profile)
            profiles.append(_profile_card(
                candidate,
                "Optimized near-miss candidate cleared Phase 10I stress criteria.",
            ))
        except TypeError:
            pass
    return {
        "title": "Forward Paper Candidate Set",
        "status": "local paper readiness only",
        "production_approved": False,
        "broker_execution_available": False,
        "order_preview_available": False,
        "candidate_count": len(profiles),
        "profiles": profiles[:2],
        "operator_note": (
            "Use Run Strategy readiness before each local paper test. Start Paper Test "
            "requires local paper mode, structure data, required strikes, and usable "
            "Live quotes or Sandbox mode."
        ),
    }


def forward_readiness_base() -> Path:
    return Path("outputs") / "forward_readiness"


def forward_readiness_latest_dir() -> Path:
    path = forward_readiness_base() / "latest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Forward Paper Candidate Set",
        "",
        "Research-only local paper readiness. No broker execution. No order preview.",
        "",
        (
            "Positive control result does not mean production approval; it means the "
            "control is the current benchmark."
        ),
        "",
    ]
    for item in report.get("profiles", []):
        lines.extend([
            f"## {item['profile_name']}",
            "",
            f"- Profile: `{item['profile_id']}`",
            f"- Why included: {item['why_included']}",
            f"- Not production approved: {item['why_not_production_approved']}",
            f"- Entry window: {item['entry_window']}",
            f"- TP / SL / DTE: {item['tp']} / {item['sl']} / {item['dte']}DTE",
            f"- Account sizing: {item['account_sizing_suggestion']}",
            "- Watch live: " + "; ".join(item["what_to_watch_live"]),
            "- Invalidate if: " + "; ".join(item["what_would_invalidate_it"]),
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def write_forward_readiness(report: dict[str, Any], directory: Path | None = None) -> Path:
    out = directory or forward_readiness_latest_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "forward_paper_candidates.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (out / "forward_paper_candidates.md").write_text(
        _markdown(report), encoding="utf-8"
    )
    return out
