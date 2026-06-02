"""Phase 9C — pure profile-builder helpers for the Streamlit Strategy Builder.

Wraps the Phase 6 strategy-profile system (src/config/strategy_profiles.py) with
form-friendly CRUD helpers. PURE / SELECTION-CONFIG ONLY — imports ONLY
strategy_profiles (never control / scanner / streamlit), so there is no circular
import and the helpers are unit-testable without a UI.

It NEVER executes, places, previews, or submits an order. Secrets + execution
keys are rejected by the existing ``validate_profile_dict`` (we surface, never
suppress, those errors)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config.strategy_profiles import (
    ALLOWED_QUOTE_PROVIDERS,
    ALLOWED_SELECTORS,
    ALLOWED_STRUCTURE_PROVIDERS,
    PROFILE_SCHEMA_VERSION,
    StrategyProfile,
    _now_iso,
    default_profiles_dir,
    list_profiles,
    load_profile_file,
    resolve_profile_path,
    save_profile_dict,
    template_profile_dict,
    validate_profile_dict,
)

# Field metadata drives the Streamlit form (label, kind, section, options).
# kind ∈ str | bool | int | float | optfloat | opttext | select
FIELD_SECTIONS: tuple[str, ...] = ("Identity", "Providers", "Selector", "Risk", "Strategy params")

PROFILE_FIELDS: list[dict[str, Any]] = [
    # Identity
    {"name": "profile_id", "label": "Profile id", "kind": "str", "section": "Identity"},
    {"name": "profile_name", "label": "Profile name", "kind": "str", "section": "Identity"},
    {"name": "enabled", "label": "Enabled", "kind": "bool", "section": "Identity"},
    {"name": "strategy_id", "label": "Strategy id", "kind": "str", "section": "Identity"},
    {"name": "strategy_type", "label": "Strategy type", "kind": "str", "section": "Identity"},
    {"name": "symbol", "label": "Symbol", "kind": "str", "section": "Identity"},
    {"name": "notes", "label": "Notes", "kind": "str", "section": "Identity"},
    # Providers
    {"name": "structure_provider", "label": "Structure provider", "kind": "select",
     "options": list(ALLOWED_STRUCTURE_PROVIDERS), "section": "Providers"},
    {"name": "quote_provider", "label": "Quote provider", "kind": "select",
     "options": list(ALLOWED_QUOTE_PROVIDERS), "section": "Providers"},
    # Selector
    {"name": "daily_selector", "label": "Daily selector", "kind": "select",
     "options": list(ALLOWED_SELECTORS), "section": "Selector"},
    {"name": "target_dte", "label": "Target DTE", "kind": "int", "section": "Selector"},
    {"name": "strict_target_dte", "label": "Strict target DTE", "kind": "bool", "section": "Selector"},
    {"name": "max_trades_per_day", "label": "Max trades/day", "kind": "int", "section": "Selector"},
    {"name": "allow_call_credit", "label": "Allow call credit", "kind": "bool", "section": "Selector"},
    {"name": "allow_put_credit", "label": "Allow put credit", "kind": "bool", "section": "Selector"},
    {"name": "require_selector_eligible_base", "label": "Require eligible base", "kind": "bool", "section": "Selector"},
    {"name": "require_quote_validation", "label": "Require quote validation", "kind": "bool", "section": "Selector"},
    {"name": "require_score_edge", "label": "Require score edge", "kind": "bool", "section": "Selector"},
    {"name": "min_selector_score", "label": "Min selector score", "kind": "optfloat", "section": "Selector"},
    {"name": "min_selector_credit", "label": "Min selector credit", "kind": "optfloat", "section": "Selector"},
    {"name": "min_selector_distance_from_spot", "label": "Min distance from spot", "kind": "optfloat", "section": "Selector"},
    {"name": "max_selector_distance_from_spot", "label": "Max distance from spot", "kind": "optfloat", "section": "Selector"},
    # Risk
    {"name": "risk_profile", "label": "Risk profile", "kind": "str", "section": "Risk"},
    # Strategy params (optional)
    {"name": "wing_threshold", "label": "Wing threshold", "kind": "optfloat", "section": "Strategy params"},
    {"name": "spread_width", "label": "Spread width", "kind": "optfloat", "section": "Strategy params"},
    {"name": "entry_window_start", "label": "Entry window start", "kind": "opttext", "section": "Strategy params"},
    {"name": "entry_window_end", "label": "Entry window end", "kind": "opttext", "section": "Strategy params"},
    {"name": "no_trade_score_threshold", "label": "No-trade score threshold", "kind": "optfloat", "section": "Strategy params"},
    {"name": "min_credit", "label": "Min credit", "kind": "optfloat", "section": "Strategy params"},
    {"name": "max_planned_stop_risk_dollars", "label": "Max planned stop risk $", "kind": "optfloat", "section": "Strategy params"},
    {"name": "max_theoretical_loss_dollars", "label": "Max theoretical loss $", "kind": "optfloat", "section": "Strategy params"},
]

_FIELDS_BY_NAME = {f["name"]: f for f in PROFILE_FIELDS}


def new_template_dict(profile_id: str) -> dict[str, Any]:
    """A safe starter profile (mock quotes, disabled, no secrets)."""
    return template_profile_dict(profile_id or "new_profile")


def load_dict_for_edit(id_or_path: str, profiles_dir: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Load a profile's raw dict for editing. Returns (dict_or_None, errors)."""
    res = load_profile_file(id_or_path, profiles_dir)
    if res.ok and res.profile is not None:
        return res.profile.to_dict(include_path=False), []
    return (res.raw if res.raw else None), res.errors


def clone_dict(source_id_or_path: str, new_id: str,
               profiles_dir: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Clone an existing profile under a new id. Keeps config, resets id/name +
    timestamps. Returns (dict_or_None, errors)."""
    res = load_profile_file(source_id_or_path, profiles_dir)
    if not res.ok or res.profile is None:
        return None, res.errors or [f"could not load source profile {source_id_or_path!r}"]
    d = res.profile.to_dict(include_path=False)
    d["profile_id"] = new_id or f"{d.get('profile_id', 'profile')}_copy"
    d["profile_name"] = f"{d.get('profile_name', d['profile_id'])} (copy)"
    now = _now_iso()
    d["created_at"] = now
    d["updated_at"] = now
    return d, []


def _coerce(kind: str, value: Any) -> Any:
    if kind == "bool":
        return bool(value)
    if kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if kind in ("float",):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if kind == "optfloat":
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if kind == "opttext":
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        return str(value)
    # str / select
    return "" if value is None else str(value)


def build_profile_dict(values: dict[str, Any], *, base: dict[str, Any] | None = None,
                       now_iso: str | None = None) -> dict[str, Any]:
    """Merge form ``values`` onto a ``base`` (template/clone/loaded) dict and
    return a complete, type-coerced profile dict. Pure: no I/O, never raises."""
    out: dict[str, Any] = dict(base or {})
    for name, field in _FIELDS_BY_NAME.items():
        if name in values:
            out[name] = _coerce(field["kind"], values[name])
    # housekeeping fields the form does not edit
    out["version"] = int(out.get("version") or PROFILE_SCHEMA_VERSION)
    stamp = now_iso or _now_iso()
    out.setdefault("created_at", stamp)
    out["updated_at"] = stamp
    out.pop("profile_path", None)
    return out


def validate_dict(d: dict[str, Any]) -> list[str]:
    """Validation errors (empty == valid). Secrets / execution keys rejected."""
    return validate_profile_dict(d)


def hash_for(d: dict[str, Any]) -> str | None:
    """Deterministic profile hash for a VALID dict, else None."""
    if validate_profile_dict(d):
        return None
    try:
        return StrategyProfile.from_dict(d).profile_hash()
    except Exception:
        return None


def save_profile(d: dict[str, Any], *, overwrite: bool = False,
                 profiles_dir: Path | None = None) -> tuple[bool, str, str | None]:
    """Validate then save to profiles/{profile_id}.yaml.

    Returns (ok, message, profile_hash_or_None). Refuses to overwrite an existing
    file unless ``overwrite=True``. Returns the deterministic hash on success."""
    errors = validate_profile_dict(d)
    if errors:
        return False, "validation failed: " + "; ".join(errors), None
    pid = str(d.get("profile_id") or "").strip()
    if not pid:
        return False, "profile_id is required", None
    path = (profiles_dir or default_profiles_dir()) / f"{pid}.yaml"
    if path.exists() and not overwrite:
        return False, (f"profile '{pid}' already exists — check 'overwrite existing "
                       "profile' to replace it"), None
    ok, msg = save_profile_dict(d, path, force=overwrite)
    if not ok:
        return False, msg, None
    return True, msg, StrategyProfile.from_dict(d).profile_hash()


def list_summaries(profiles_dir: Path | None = None) -> list[dict[str, Any]]:
    """One row per profiles/*.yaml: id/name/ok/errors/hash (+ summary fields)."""
    out: list[dict[str, Any]] = []
    for res in list_profiles(profiles_dir):
        if res.ok and res.profile is not None:
            row = res.profile.summary_row()
            row["ok"] = True
            row["errors"] = []
            row["profile_hash"] = res.profile.profile_hash()
        else:
            pid = Path(res.path).stem if res.path else "?"
            row = {"profile_id": pid, "ok": False, "errors": res.errors, "profile_hash": None}
        row["path"] = res.path
        out.append(row)
    return out


def resolve_profile_target(profile_id: str, profiles_dir: Path | None = None) -> Path:
    """Where ``save_profile`` would write this id (for display)."""
    return resolve_profile_path(profile_id, profiles_dir)
