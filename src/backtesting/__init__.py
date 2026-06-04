"""Phase 10A — local historical backtesting scaffold for the ZerσSigma Algo.

Maps saved raw daily snapshot CSVs (SPX / SPY / QQQ, from the TOS logger) into the
SAME StructureSnapshot / OptionChainSnapshot the live + replay path uses, so the
existing strategy / selector / paper lifecycle can replay history WITHOUT a fork.

Read-only data mapping: no network, no broker calls, no order/execution. This is a
LOADER + MAPPER scaffold — the full TP/SL lifecycle runner is a later phase.
"""

from src.backtesting import mappers, raw_snapshot_loader, schemas

__all__ = ["mappers", "raw_snapshot_loader", "schemas"]
