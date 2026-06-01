"""Factory for StructureProvider instantiation.

Resolves the active provider name → provider instance, pulling YAML
`implementations` blocks and substituting env-var values that the
`utils.config` loader already plumbed through.

Default-safe: unknown / missing → `StubStructureProvider`.
"""

from __future__ import annotations

import importlib
from typing import Any

from src.providers.structure.stub import StubStructureProvider
from src.utils.config import AppConfig
from src.utils.logging import get_logger

log = get_logger("provider.factory")


def build_structure_provider(
    cfg: AppConfig,
    override: str | None = None,
) -> tuple[Any, str]:
    """Instantiate the active StructureProvider.

    Returns (instance, resolved_name). On any failure, falls back to the
    stub and logs the reason — the cockpit must keep launching.
    """
    raw = cfg.providers.raw or {}
    section = (raw.get("structure") or {})
    name = (override or cfg.providers.structure_active or "stub").strip()
    impls = section.get("implementations") or {}

    if name not in impls:
        if name and name != "stub":
            log.warning("Unknown structure provider %r; falling back to stub.", name)
        return StubStructureProvider(), "stub"

    entry = impls[name]
    module_path = entry.get("module")
    cls_name    = entry.get("class")
    params      = entry.get("params") or {}

    if not module_path or not cls_name:
        log.warning("Structure provider %r missing module/class; falling back to stub.", name)
        return StubStructureProvider(), "stub"

    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name)
        instance = cls(**params)
        return instance, name
    except Exception as exc:
        # Never log secrets — params may contain ${ZS_API_TOKEN} substituted values.
        log.error("Failed to load structure provider %r (%s.%s): %s — using stub.",
                  name, module_path, cls_name, type(exc).__name__)
        return StubStructureProvider(), "stub"
