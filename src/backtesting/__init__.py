"""Local historical backtesting for the ZerσSigma Algo (Phase 10A + 10B).

Maps saved raw daily snapshot CSVs (SPX / SPY / QQQ, from the TOS logger) into the
SAME StructureSnapshot / OptionChainSnapshot the live path uses, then replays the
existing strategy / selector across history and SIMULATES the TP/SL/EOD exit —
all WITHOUT a strategy fork.

Read-only: no network, no broker calls, no order preview, no execution. Phase 10A
is the loader/mapper scaffold; Phase 10B adds the replay runner + lifecycle sim +
reports (``replay_runner`` / ``lifecycle_sim`` / ``reports`` / ``replay_providers``
/ ``profile_runtime``).
"""

from src.backtesting import (
    attribution,
    comparison,
    lifecycle_sim,
    mappers,
    profile_runtime,
    raw_snapshot_loader,
    replay_providers,
    replay_runner,
    reports,
    schemas,
)

__all__ = [
    "attribution",
    "comparison",
    "lifecycle_sim",
    "mappers",
    "profile_runtime",
    "raw_snapshot_loader",
    "replay_providers",
    "replay_runner",
    "reports",
    "schemas",
]
