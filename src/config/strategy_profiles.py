"""Strategy run-profile model + storage + validation — Phase 6.

A *run profile* is a named, versioned bundle of scanner/selector settings so an
operator can run `python -m scripts.run_scanner --profile <id>` instead of a long
flag string. This is CONFIGURATION / PERSISTENCE ONLY — it never executes,
submits, previews, or places orders, and it does not change candidate generation,
quote-provider behavior, risk caps, or Phase 4.2/5 logic. It only supplies
*defaults* the scanner already understands (CLI flags still override — see
`scripts/run_scanner.py` precedence: CLI > profile > env > YAML/default).

Pure-ish module: stdlib + PyYAML only. Profiles live as YAML files under
`profiles/` (repo root). NO secrets belong in a profile — credentials stay in
`.env`; profiles reference providers by NAME only.

Validation returns clean error STRINGS (never raises for user-facing commands).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from src.selector.daily_selector import SELECTOR_MODES

# Repo root = three parents up from src/config/strategy_profiles.py
REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_SCHEMA_VERSION = 1

# Allowed enum values — mirror the scanner's CLI choices (kept local so this
# module stays dependency-light; SELECTOR_MODES is imported as the single
# source of truth for selector modes).
ALLOWED_STRUCTURE_PROVIDERS = ("stub", "zerosigma_api")
ALLOWED_QUOTE_PROVIDERS = ("mock", "null", "tastytrade")
ALLOWED_SELECTORS = tuple(SELECTOR_MODES)

# Fields excluded from the deterministic profile hash. created_at/updated_at are
# excluded so cosmetic re-saves don't churn the hash; profile_path is runtime
# provenance, not profile content. EVERYTHING ELSE (id, name, notes, version,
# and every config knob) is hashed so the hash uniquely identifies the exact
# profile content that produced a signal.
_HASH_EXCLUDE = frozenset({"created_at", "updated_at", "profile_path"})


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def default_profiles_dir() -> Path:
    return REPO_ROOT / "profiles"


@dataclass
class StrategyProfile:
    """Versioned strategy/run profile. Required fields first; optional
    strategy-specific params (with defaults) after. `profile_path` is runtime
    provenance, never written into the YAML body."""

    # ── required / core ──
    profile_id: str
    profile_name: str
    version: int
    enabled: bool
    strategy_id: str
    strategy_type: str
    symbol: str
    structure_provider: str
    quote_provider: str
    target_dte: int
    strict_target_dte: bool
    daily_selector: str
    max_trades_per_day: int
    allow_call_credit: bool
    allow_put_credit: bool
    require_selector_eligible_base: bool
    require_quote_validation: bool
    require_score_edge: bool
    min_selector_score: float | None
    min_selector_credit: float | None
    min_selector_distance_from_spot: float | None
    max_selector_distance_from_spot: float | None
    risk_profile: str
    notes: str
    created_at: str
    updated_at: str

    # ── optional strategy-specific params (loaded, not all wired yet) ──
    wing_threshold: float | None = None
    spread_width: float | None = None
    entry_window_start: str | None = None
    entry_window_end: str | None = None
    no_trade_score_threshold: float | None = None
    min_credit: float | None = None
    max_planned_stop_risk_dollars: float | None = None
    max_theoretical_loss_dollars: float | None = None

    # ── runtime provenance (NOT persisted in the YAML body) ──
    profile_path: str | None = field(default=None)

    # ── construction ──
    @classmethod
    def from_dict(cls, d: dict[str, Any], *, profile_path: str | None = None) -> StrategyProfile:
        """Build from a (already-validated) dict, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in known}
        kwargs["profile_path"] = profile_path
        return cls(**kwargs)

    def to_dict(self, *, include_path: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_path:
            d.pop("profile_path", None)
        return d

    def hashable_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.to_dict(include_path=False).items() if k not in _HASH_EXCLUDE}

    def profile_hash(self) -> str:
        """Deterministic 16-hex hash of the profile content, EXCLUDING
        created_at / updated_at / profile_path (see _HASH_EXCLUDE)."""
        blob = json.dumps(self.hashable_dict(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def summary_row(self) -> dict[str, Any]:
        """One-line summary for `--list`."""
        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "strategy_id": self.strategy_id,
            "target_dte": self.target_dte,
            "quote_provider": self.quote_provider,
            "daily_selector": self.daily_selector,
            "enabled": self.enabled,
        }


# ── validation ──────────────────────────────────────────────────────────────

REQUIRED_FIELDS: tuple[str, ...] = (
    "profile_id", "profile_name", "version", "enabled", "strategy_id",
    "strategy_type", "symbol", "structure_provider", "quote_provider",
    "target_dte", "strict_target_dte", "daily_selector", "max_trades_per_day",
    "allow_call_credit", "allow_put_credit", "require_selector_eligible_base",
    "require_quote_validation", "require_score_edge", "min_selector_score",
    "min_selector_credit", "min_selector_distance_from_spot",
    "max_selector_distance_from_spot", "risk_profile", "notes",
    "created_at", "updated_at",
)

_BOOL_FIELDS = (
    "enabled", "strict_target_dte", "allow_call_credit", "allow_put_credit",
    "require_selector_eligible_base", "require_quote_validation", "require_score_edge",
)
_INT_FIELDS = ("version", "target_dte", "max_trades_per_day")
_OPT_FLOAT_FIELDS = (
    "min_selector_score", "min_selector_credit", "min_selector_distance_from_spot",
    "max_selector_distance_from_spot", "wing_threshold", "spread_width",
    "no_trade_score_threshold", "min_credit", "max_planned_stop_risk_dollars",
    "max_theoretical_loss_dollars",
)
_STR_FIELDS = (
    "profile_id", "profile_name", "strategy_id", "strategy_type", "symbol",
    "risk_profile", "notes",
)


def validate_profile_dict(d: Any) -> list[str]:
    """Return a list of human-readable validation errors. Empty list == valid.
    Never raises."""
    errors: list[str] = []
    if not isinstance(d, dict):
        return [f"profile must be a mapping, got {type(d).__name__}"]

    for f in REQUIRED_FIELDS:
        if f not in d or d[f] is None:
            # the *_selector_* numeric fields are allowed to be null (no filter)
            if f in ("min_selector_score", "min_selector_credit",
                     "min_selector_distance_from_spot", "max_selector_distance_from_spot"):
                continue
            errors.append(f"missing required field: {f}")

    # types
    for f in _BOOL_FIELDS:
        if f in d and d[f] is not None and not isinstance(d[f], bool):
            errors.append(f"{f} must be a boolean, got {type(d[f]).__name__}")
    for f in _INT_FIELDS:
        if f in d and d[f] is not None and (isinstance(d[f], bool) or not isinstance(d[f], int)):
            errors.append(f"{f} must be an integer, got {type(d[f]).__name__}")
    for f in _STR_FIELDS:
        if f in d and d[f] is not None and not isinstance(d[f], str):
            errors.append(f"{f} must be a string, got {type(d[f]).__name__}")
    for f in _OPT_FLOAT_FIELDS:
        if f in d and d[f] is not None and (isinstance(d[f], bool) or not isinstance(d[f], (int, float))):
            errors.append(f"{f} must be a number or null, got {type(d[f]).__name__}")

    # enums
    if d.get("structure_provider") not in (None, *ALLOWED_STRUCTURE_PROVIDERS):
        errors.append(
            f"structure_provider must be one of {ALLOWED_STRUCTURE_PROVIDERS}, "
            f"got {d.get('structure_provider')!r}"
        )
    if d.get("quote_provider") not in (None, *ALLOWED_QUOTE_PROVIDERS):
        errors.append(
            f"quote_provider must be one of {ALLOWED_QUOTE_PROVIDERS}, "
            f"got {d.get('quote_provider')!r}"
        )
    if d.get("daily_selector") not in (None, *ALLOWED_SELECTORS):
        errors.append(
            f"daily_selector must be one of {ALLOWED_SELECTORS}, "
            f"got {d.get('daily_selector')!r}"
        )

    # ranges
    if isinstance(d.get("target_dte"), int) and not isinstance(d.get("target_dte"), bool) and d["target_dte"] < 0:
        errors.append("target_dte must be >= 0")
    if isinstance(d.get("max_trades_per_day"), int) and not isinstance(d.get("max_trades_per_day"), bool) and d["max_trades_per_day"] < 0:
        errors.append("max_trades_per_day must be >= 0")
    if isinstance(d.get("version"), int) and not isinstance(d.get("version"), bool) and d["version"] < 1:
        errors.append("version must be >= 1")

    # Safety: a profile must NOT smuggle execution intent or secrets.
    for forbidden in ("execution_mode", "tasty_refresh_token", "tasty_client_secret",
                      "password", "client_secret", "refresh_token"):
        if forbidden in d:
            errors.append(f"profile must not contain '{forbidden}' (no execution / no secrets)")

    return errors


@dataclass
class ProfileLoadResult:
    ok: bool
    profile: StrategyProfile | None
    errors: list[str]
    path: str | None
    raw: dict[str, Any] | None = None


def resolve_profile_path(id_or_path: str, profiles_dir: Path | None = None) -> Path:
    """Resolve a profile id OR path to a concrete file path.

    Treated as a PATH when it ends in .yaml/.yml, contains a separator, or
    already exists as a file; otherwise as an id → `<profiles_dir>/<id>.yaml`.
    """
    profiles_dir = profiles_dir or default_profiles_dir()
    s = str(id_or_path)
    looks_like_path = (
        s.endswith((".yaml", ".yml")) or "/" in s or "\\" in s or Path(s).is_file()
    )
    if looks_like_path:
        return Path(s)
    return profiles_dir / f"{s}.yaml"


def load_profile_file(id_or_path: str, profiles_dir: Path | None = None) -> ProfileLoadResult:
    """Load + validate a profile. Never raises — returns a result object."""
    path = resolve_profile_path(id_or_path, profiles_dir)
    if not path.is_file():
        return ProfileLoadResult(ok=False, profile=None,
                                 errors=[f"profile file not found: {path}"], path=str(path))
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        return ProfileLoadResult(ok=False, profile=None,
                                 errors=[f"YAML parse error: {exc}"], path=str(path))
    errors = validate_profile_dict(raw)
    if errors:
        return ProfileLoadResult(ok=False, profile=None, errors=errors, path=str(path), raw=raw)
    profile = StrategyProfile.from_dict(raw, profile_path=str(path))
    return ProfileLoadResult(ok=True, profile=profile, errors=[], path=str(path), raw=raw)


def list_profiles(profiles_dir: Path | None = None) -> list[ProfileLoadResult]:
    """Load every *.yaml under profiles_dir (sorted). Invalid files come back
    with ok=False so `--list` / `--validate-all` can report them."""
    profiles_dir = profiles_dir or default_profiles_dir()
    if not profiles_dir.is_dir():
        return []
    out: list[ProfileLoadResult] = []
    for p in sorted(profiles_dir.glob("*.yaml")):
        out.append(load_profile_file(str(p), profiles_dir))
    return out


def template_profile_dict(profile_id: str) -> dict[str, Any]:
    """A SAFE starter profile: mock quotes, disabled, no secrets, no execution."""
    now = _now_iso()
    return {
        "profile_id": profile_id,
        "profile_name": profile_id.replace("_", " ").title(),
        "version": PROFILE_SCHEMA_VERSION,
        "enabled": False,
        "strategy_id": "vertical_wing_v1",
        "strategy_type": "vertical_credit_spread",
        "symbol": "SPX",
        "structure_provider": "stub",
        "quote_provider": "mock",
        "target_dte": 0,
        "strict_target_dte": False,
        "daily_selector": "score_best_valid",
        "max_trades_per_day": 1,
        "allow_call_credit": True,
        "allow_put_credit": True,
        "require_selector_eligible_base": True,
        "require_quote_validation": True,
        "require_score_edge": False,
        "min_selector_score": None,
        "min_selector_credit": None,
        "min_selector_distance_from_spot": None,
        "max_selector_distance_from_spot": None,
        "risk_profile": "aggressive_paper_10k",
        "notes": "Starter template — edit me. Mock quotes, disabled, no execution.",
        "created_at": now,
        "updated_at": now,
        # optional strategy params (null = unused this phase)
        "wing_threshold": None,
        "spread_width": None,
        "entry_window_start": None,
        "entry_window_end": None,
        "no_trade_score_threshold": None,
        "min_credit": None,
        "max_planned_stop_risk_dollars": None,
        "max_theoretical_loss_dollars": None,
    }


def save_profile_dict(d: dict[str, Any], path: Path, *, force: bool = False) -> tuple[bool, str]:
    """Write a profile dict to YAML. Refuses to overwrite unless force.
    Returns (ok, message)."""
    path = Path(path)
    if path.exists() and not force:
        return False, f"refusing to overwrite existing file (use --force): {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {k: v for k, v in d.items() if k != "profile_path"}
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(body, fh, sort_keys=False, default_flow_style=False)
    return True, f"wrote {path}"
