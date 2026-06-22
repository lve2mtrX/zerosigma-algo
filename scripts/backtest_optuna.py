"""Research-only Optuna strategy search CLI."""

from __future__ import annotations

import argparse
import sys

from src.backtesting.optuna_optimizer import (
    OptunaConfig,
    optuna_latest_dir,
    optuna_run_dir,
    run_optuna,
    write_optuna_outputs,
)


def _configure_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_encoding()
    parser = argparse.ArgumentParser(
        description="Optuna robustness search over local historical replay (research only)."
    )
    parser.add_argument("--symbol", required=True, choices=["SPX"])
    parser.add_argument("--dte", required=True, type=int, choices=[0])
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--starting-balance", type=float, default=10000.0)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--trading-root", default=None)
    args = parser.parse_args(argv)
    config = OptunaConfig(
        symbol=args.symbol, dte=args.dte, trials=max(1, args.trials),
        timeout_seconds=max(1, args.timeout_seconds),
        starting_balance=args.starting_balance, contracts=max(1, args.contracts),
        run_label=args.run_label, seed=args.seed, trading_root=args.trading_root,
    )
    print("ZeroSigma Optuna research search")
    print(
        f"Evaluating up to {config.trials} deterministic trials "
        "in adaptive replay batches..."
    )
    try:
        result = run_optuna(config)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    run_id = str(result.run_config["optimizer_run_id"])
    write_optuna_outputs(result, [optuna_latest_dir(), optuna_run_dir(run_id)])
    print(f"Trials completed: {len(result.trials)}")
    if result.trials:
        best = result.trials[0]
        print(
            f"Best objective: {float(best['objective_value']):.2f}; "
            f"validation expectancy ${float(best.get('validation_expectancy_dollars') or 0):,.2f}; "
            f"holdout expectancy ${float(best.get('holdout_expectancy_dollars') or 0):,.2f}"
        )
    print(f"Output: {optuna_latest_dir()}")
    print("Research only. No profile writes, order preview, automatic promotion, or execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
