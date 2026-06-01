"""Strategy registry — loads strategies from config/strategies.yaml.

`load_strategies(cfg)` returns a dict[strategy_id -> Strategy] containing only
strategies with `enabled: true`.
"""

from __future__ import annotations

import importlib
from typing import Any

from src.strategies.base import Strategy
from src.utils.config import AppConfig
from src.utils.logging import get_logger

log = get_logger("strategy.registry")


def load_strategies(cfg: AppConfig) -> dict[str, Strategy]:
    out: dict[str, Strategy] = {}
    for sid, entry in cfg.strategies.items():
        if not entry.get("enabled", False):
            log.info("Skipping disabled strategy %s", sid)
            continue
        module_path = entry["module"]
        cls_name = entry["class"]
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, cls_name)
            instance: Strategy = cls(
                strategy_id=sid,
                display_name=entry.get("display_name", sid),
                symbol=entry.get("symbol"),
                default_parameters=entry.get("default_parameters", {}),
            )
            out[sid] = instance
            log.info("Loaded strategy %s (%s)", sid, cls_name)
        except Exception as exc:
            log.error("Failed to load strategy %s: %s", sid, exc)
    return out


def get_default_params(cfg: AppConfig, strategy_id: str) -> dict[str, Any]:
    entry = cfg.strategies.get(strategy_id, {})
    return entry.get("default_parameters", {})
