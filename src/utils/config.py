"""Config loading.

Loads `.env` then merges all YAML files under `config/` into a single
strongly-typed `AppConfig`. Performs `${ENV_VAR}` substitution inside YAML
values so secrets stay in `.env`.

Portability: all paths are resolved relative to a passed `repo_root` so the
project runs from any directory on any machine.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _substitute_env(value: Any) -> Any:
    """Recursively replace ``${VAR}`` in strings with env var values."""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), "")
        return ENV_REF.sub(repl, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _substitute_env(data)


@dataclass
class ProvidersConfig:
    structure_active: str
    quotes_active: str
    execution_active: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppConfig:
    repo_root: Path
    strategies: dict[str, Any]
    risk_profiles: dict[str, Any]
    active_risk_profile: str | None
    providers: ProvidersConfig
    scanner: dict[str, Any]

    @property
    def data_dir(self) -> Path:
        raw = os.environ.get("DATA_DIR", "./data")
        p = Path(raw)
        return p if p.is_absolute() else (self.repo_root / p).resolve()

    @property
    def output_dir(self) -> Path:
        raw = os.environ.get("OUTPUT_DIR", "./outputs")
        p = Path(raw)
        return p if p.is_absolute() else (self.repo_root / p).resolve()


def load_config(repo_root: Path) -> AppConfig:
    """Load .env and all config YAMLs from `repo_root`."""
    load_dotenv(repo_root / ".env", override=False)

    cfg_dir = repo_root / "config"
    strategies = _load_yaml(cfg_dir / "strategies.yaml").get("strategies", {})
    risk_raw = _load_yaml(cfg_dir / "risk_profiles.yaml")
    risk = risk_raw.get("profiles", {})
    # The cockpit boots into this profile; the UI may override per session.
    active_risk_profile = risk_raw.get("active_profile") or (next(iter(risk), None))
    providers_raw = _load_yaml(cfg_dir / "providers.yaml")
    scanner = _load_yaml(cfg_dir / "scanner.yaml").get("scanner", {})

    struct_section = providers_raw.get("structure") or {}
    structure_active = (
        struct_section.get("active")
        or struct_section.get("default_if_unset")
        or "stub"
    )
    quotes_section = providers_raw.get("quotes") or {}
    quotes_active = (
        quotes_section.get("active")
        or quotes_section.get("default_if_unset")
        or "null"
    )
    exec_section = providers_raw.get("execution") or {}
    execution_active = (
        exec_section.get("active")
        or exec_section.get("default_if_unset")
        or "disabled"
    )

    return AppConfig(
        repo_root=repo_root,
        strategies=strategies,
        risk_profiles=risk,
        active_risk_profile=active_risk_profile,
        providers=ProvidersConfig(
            structure_active=structure_active,
            quotes_active=quotes_active,
            execution_active=execution_active,
            raw=providers_raw,
        ),
        scanner=scanner,
    )
