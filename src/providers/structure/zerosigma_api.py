"""ZerσSigma API StructureProvider — Phase 2 implementation (stubbed).

Talks to the public ZerσSigma API:
  GET /api/v1/market/snapshot?symbol=...
  GET /api/v1/exposure/series?symbol=...&metric=...&mode=net

Auth: Bearer JWT (login or admin service token).

Phase 1: this is a placeholder that raises NotImplementedError so the
cockpit can be wired but cannot accidentally hit the live API.
"""

from __future__ import annotations

from src.providers.structure.types import StructureSnapshot


class ZeroSigmaApiStructureProvider:
    name = "zerosigma_api"

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        admin_key: str | None = None,
        symbol: str = "SPX",
        refresh_seconds: int = 60,
        timeout_seconds: int = 10,
        max_retries: int = 3,
        **_: object,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.admin_key = admin_key
        self.symbol = symbol
        self.refresh_seconds = int(refresh_seconds or 60)
        self.timeout_seconds = int(timeout_seconds)
        self.max_retries = int(max_retries)
        self._last_refresh: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Phase 2 implementation outline (do not enable yet)
    # ------------------------------------------------------------------
    #
    # def _ensure_token(self) -> str:
    #     if self.token:
    #         return self.token
    #     if self.admin_key:
    #         # POST /api/v1/auth/service-token  with admin key in body
    #         ...
    #     raise RuntimeError("No ZS API auth configured")
    #
    # def get_snapshot(self, symbol: str) -> StructureSnapshot:
    #     with httpx.Client(timeout=self.timeout_seconds) as c:
    #         headers = {"Authorization": f"Bearer {self._ensure_token()}"}
    #         r = c.get(f"{self.base_url}/api/v1/market/snapshot",
    #                   params={"symbol": symbol}, headers=headers)
    #         r.raise_for_status()
    #         payload = r.json()
    #     # ... parse chain CSV, exposures, build StructureSnapshot ...
    #     return snapshot

    def get_snapshot(self, symbol: str) -> StructureSnapshot:
        raise NotImplementedError(
            "ZeroSigmaApiStructureProvider is stubbed in Phase 1. "
            "Wire it in Phase 2 — see docs/reference_notes.md."
        )

    def is_fresh(self, symbol: str, max_age_seconds: int) -> bool:
        return False

    def last_refresh_ts(self, symbol: str) -> float | None:
        return self._last_refresh.get(symbol)
