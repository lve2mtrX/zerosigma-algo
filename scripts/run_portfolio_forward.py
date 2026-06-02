"""Phase 9B — multi-strategy local paper portfolio runner.

Runs the EXISTING scanner once per profile per tick (in-process,
`scripts.run_scanner.main` — no duplicated strategy logic), feeds each profile's
SELECTED signals into the local paper-trade lifecycle engine, re-prices open
spreads from each tick's fresh quotes, applies TP / SL / EOD exits, and records
portfolio-level ledgers.

LOCAL PAPER ACCOUNTING ONLY. It NEVER places orders, submits paper orders, calls
order preview, selects a broker account, or reconciles against a brokerage. Every
ledger stamps no_execution=True / execution_mode=local_paper_lifecycle_only.

  python -m scripts.run_portfolio_forward --profiles profile_a,profile_b
  python -m scripts.run_portfolio_forward --profiles-file config/portfolio_profiles.yaml
  python -m scripts.run_portfolio_forward --profiles A,B --interval-seconds 60
  python -m scripts.run_portfolio_forward --profiles A,B --max-ticks 5
  python -m scripts.run_portfolio_forward --profiles A,B --once
  python -m scripts.run_portfolio_forward --profiles A,B --market-hours-only
  python -m scripts.run_portfolio_forward --profiles A,B --output-dir outputs/portfolio_forward
"""

from __future__ import annotations

import argparse
import csv
import importlib
import logging
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

NO_EXECUTION = True
EXECUTION_MODE = "local_paper_lifecycle_only"

log = logging.getLogger("portfolio_forward")


# ── small helpers ────────────────────────────────────────────────────────────

def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except Exception:
        return None


def _is_rth(dt) -> bool:  # type: ignore[no-untyped-def]
    """Weekday, 09:30–16:00 ET (simple rule, no holiday calendar — mirrors Phase 7)."""
    if dt.weekday() >= 5:
        return False
    minutes = dt.hour * 60 + dt.minute
    return (9 * 60 + 30) <= minutes <= (16 * 60)


def _read_all_rows(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        return []
    try:
        with csv_path.open("r", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _selected_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if str(r.get("selected_trade", "")).lower() == "true"]


def _run_scanner_tick(profile_ref: str, scanner_out: Path) -> tuple[int, list[dict]]:
    """Run the scanner in-process pointed at scanner_out. Returns (rc, all_rows)."""
    run_scanner = importlib.import_module("scripts.run_scanner")
    prev_output_dir = os.environ.get("OUTPUT_DIR")
    os.environ["OUTPUT_DIR"] = str(scanner_out)
    try:
        rc = run_scanner.main(["--profile", profile_ref])
    finally:
        if prev_output_dir is None:
            os.environ.pop("OUTPUT_DIR", None)
        else:
            os.environ["OUTPUT_DIR"] = prev_output_dir
    rows = _read_all_rows(scanner_out / "latest" / "ranked_candidates.csv")
    return rc, rows


def _load_profiles(profiles_arg: str | None, profiles_file: str | None):
    """Resolve the requested profiles to (loaded_list, invalid_list, file_lifecycle).

    loaded_list items: dict(ref, profile_id, profile_hash, symbol, strategy_id,
    target_dte, daily_selector). file_lifecycle is the optional lifecycle override
    block from a --profiles-file."""
    from src.config.strategy_profiles import load_profile_file

    refs: list[str] = []
    file_lifecycle: dict = {}
    if profiles_file:
        import yaml
        path = Path(profiles_file)
        if not path.is_absolute():
            path = REPO_ROOT / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data = data or {}
        for item in data.get("profiles", []) or []:
            refs.append(str(item))
        file_lifecycle = data.get("lifecycle", {}) or {}
    if profiles_arg:
        refs.extend(r.strip() for r in profiles_arg.split(",") if r.strip())

    loaded: list[dict] = []
    invalid: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        res = load_profile_file(ref)
        if not res.ok or res.profile is None:
            invalid.append({"ref": ref, "errors": res.errors})
            continue
        p = res.profile
        loaded.append({
            "ref": ref, "profile_id": p.profile_id, "profile_hash": p.profile_hash(),
            "symbol": p.symbol, "strategy_id": p.strategy_id, "target_dte": p.target_dte,
            "daily_selector": p.daily_selector,
        })
    return loaded, invalid, file_lifecycle


def main(argv: list[str] | None = None) -> int:
    from src.paper import ledger, lifecycle
    from src.paper.models import PaperLifecycleConfig
    from src.utils.time import now_et

    parser = argparse.ArgumentParser(
        description="ZerσSigma multi-strategy paper portfolio runner (local accounting — no execution)",
    )
    parser.add_argument("--profiles", default=None,
                        help="comma-separated run-profile ids/paths")
    parser.add_argument("--profiles-file", dest="profiles_file", default=None,
                        help="YAML file with a 'profiles:' list (+ optional 'lifecycle:' block)")
    parser.add_argument("--interval-seconds", dest="interval_seconds", type=float, default=60.0,
                        help="seconds between ticks (default 60; 0 = no sleep)")
    parser.add_argument("--max-ticks", dest="max_ticks", type=int, default=None)
    parser.add_argument("--once", action="store_true", help="run exactly one tick")
    parser.add_argument("--market-hours-only", dest="market_hours_only", action="store_true",
                        help="skip scanning outside RTH (EOD exits still evaluated)")
    parser.add_argument("--output-dir", dest="output_dir", default="outputs/portfolio_forward")
    # lifecycle overrides (CLI > profiles-file lifecycle > env PAPER_* > default)
    parser.add_argument("--contracts", type=int, default=None)
    parser.add_argument("--take-profit-pct", dest="take_profit_pct", type=float, default=None)
    parser.add_argument("--stop-loss-pct", dest="stop_loss_pct", type=float, default=None)
    parser.add_argument("--eod-exit-time", dest="eod_exit_time", default=None)
    parser.add_argument("--no-exit-on-eod", dest="no_exit_on_eod", action="store_true")
    parser.add_argument("--max-open-total", dest="max_open_trades_total", type=int, default=None)
    parser.add_argument("--max-open-per-profile", dest="max_open_trades_per_profile",
                        type=int, default=None)
    parser.add_argument("--allow-multiple-open-per-profile",
                        dest="allow_multiple_open_per_profile", action="store_true")
    parser.add_argument("--allow-duplicate-strikes",
                        dest="allow_duplicate_strikes", action="store_true")
    args = parser.parse_args(argv)

    if not args.profiles and not args.profiles_file:
        parser.error("one of --profiles or --profiles-file is required")

    loaded, invalid, file_lifecycle = _load_profiles(args.profiles, args.profiles_file)
    if not loaded:
        sys.stderr.write("portfolio runner: no valid profiles to run\n")
        for inv in invalid:
            sys.stderr.write(f"  - {inv['ref']}: {inv['errors']}\n")
        return 2

    # build lifecycle config: env → file lifecycle → CLI overrides
    cli_over = {
        "contracts": args.contracts,
        "take_profit_pct": args.take_profit_pct,
        "stop_loss_pct": args.stop_loss_pct,
        "eod_exit_time": args.eod_exit_time,
        "exit_on_eod": False if args.no_exit_on_eod else None,
        "max_open_trades_total": args.max_open_trades_total,
        "max_open_trades_per_profile": args.max_open_trades_per_profile,
        "allow_multiple_open_per_profile": True if args.allow_multiple_open_per_profile else None,
        "allow_duplicate_strikes": True if args.allow_duplicate_strikes else None,
    }
    config = PaperLifecycleConfig.from_env()
    for k, v in {**file_lifecycle, **{k: v for k, v in cli_over.items() if v is not None}}.items():
        if hasattr(config, k):
            setattr(config, k, v)

    max_ticks = 1 if args.once else args.max_ticks
    started = now_et()
    portfolio_run_id = f"{started.strftime('%Y%m%d_%H%M%S')}_portfolio"
    out_root = Path(args.output_dir)
    if not out_root.is_absolute():
        out_root = REPO_ROOT / out_root
    run_dir = out_root / "runs" / portfolio_run_id
    latest_dir = out_root / "latest"
    paths = ledger.portfolio_paths(run_dir)

    manifest = {
        "portfolio_run_id": portfolio_run_id,
        "profiles": [p["profile_id"] for p in loaded],
        "profile_refs": [p["ref"] for p in loaded],
        "profile_hashes": {p["profile_id"]: p["profile_hash"] for p in loaded},
        "invalid_profiles": invalid,
        "started_at": started.isoformat(),
        "ended_at": None,
        "status": "running",
        "interval_seconds": args.interval_seconds,
        "max_ticks": max_ticks,
        "market_hours_only": bool(args.market_hours_only),
        "lifecycle_config": config.to_dict(),
        "no_execution": True,
        "execution_mode": EXECUTION_MODE,
        "git_commit": _git_commit(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }

    open_trades: list[lifecycle.PaperTrade] = []
    closed_trades: list[lifecycle.PaperTrade] = []
    dup_skipped = 0
    blocked = 0
    max_open_seen = 0
    tick_id = 0
    exit_code = 0

    def _persist(status_hb: str) -> None:
        ledger.write_open_trades(run_dir, latest_dir, open_trades)
        ledger.write_closed_trades(run_dir, latest_dir, closed_trades)
        summary = ledger.compute_summary(
            open_trades, closed_trades, max_open_trades_seen=max_open_seen,
            duplicate_skipped_count=dup_skipped, blocked_by_limits_count=blocked,
        )
        ledger.write_summary(run_dir, latest_dir, summary)
        ledger.write_heartbeat(run_dir, latest_dir, {
            "portfolio_run_id": portfolio_run_id, "status": status_hb, "tick_id": tick_id,
            "latest_tick_time": now_et().isoformat(),
            "open_trade_count": len(open_trades), "closed_trade_count": len(closed_trades),
            "total_pnl": summary["total_pnl"], "no_execution": True,
        })

    ledger.write_manifest(run_dir, latest_dir, manifest)
    _persist("running")

    try:
        while max_ticks is None or tick_id < max_ticks:
            tick_id += 1
            tick_started = now_et()
            now_iso = tick_started.isoformat()
            trade_date = tick_started.date().isoformat()
            scanning = not (args.market_hours_only and not _is_rth(tick_started))
            rows_by_profile: dict[str, list[dict]] = {}
            profile_decisions: list[dict] = []

            # ── scan each profile + open new paper trades from selected signals ──
            if scanning:
                for p in loaded:
                    scanner_out = paths["scanner"] / p["profile_id"]
                    try:
                        rc, rows = _run_scanner_tick(p["ref"], scanner_out)
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:  # one profile's failure must not kill the portfolio
                        log.error("portfolio tick %d profile %s errored: %s",
                                  tick_id, p["profile_id"], exc)
                        ledger.append_profile_tick(run_dir, {
                            "portfolio_run_id": portfolio_run_id, "tick_id": tick_id,
                            "profile_id": p["profile_id"], "status": "error",
                            "error": f"{type(exc).__name__}: {exc}", "no_execution": True,
                        })
                        profile_decisions.append({"profile_id": p["profile_id"], "status": "error"})
                        continue

                    rows_by_profile[p["profile_id"]] = rows
                    selected = _selected_rows(rows)
                    opened_this = 0
                    for row in selected:
                        side = row.get("side")
                        short_strike = lifecycle._f(row, "short_strike")
                        long_strike = lifecycle._f(row, "long_strike")
                        selected_expiry = row.get("selected_expiry") or row.get("expiry")
                        identity = lifecycle.make_trade_identity(
                            profile_hash=p["profile_hash"], symbol=p["symbol"],
                            selected_expiry=selected_expiry, side=side,
                            short_strike=short_strike, long_strike=long_strike,
                            target_dte=p["target_dte"], trade_date=trade_date,
                        )
                        ok, block_reason, is_dup = lifecycle.can_open(
                            identity=identity, side=side, short_strike=short_strike,
                            long_strike=long_strike, selected_expiry=selected_expiry,
                            profile_id=p["profile_id"], open_trades=open_trades, config=config,
                        )
                        if is_dup:
                            dup_skipped += 1
                            ledger.append_event(run_dir, ledger.make_event(
                                event_type="duplicate_skipped", timestamp=now_iso,
                                paper_trade_id=lifecycle._paper_trade_id(identity),
                                profile_id=p["profile_id"], reason="duplicate_open_identity",
                                trade=None))
                            continue
                        if not ok:
                            blocked += 1
                            ledger.append_event(run_dir, ledger.make_event(
                                event_type="blocked_by_limits", timestamp=now_iso,
                                paper_trade_id=lifecycle._paper_trade_id(identity),
                                profile_id=p["profile_id"], reason=block_reason, trade=None))
                            continue
                        trade = lifecycle.open_trade_from_signal(
                            row, run_id=portfolio_run_id, profile_id=p["profile_id"],
                            profile_hash=p["profile_hash"], strategy_id=p["strategy_id"],
                            symbol=p["symbol"], target_dte=p["target_dte"], config=config,
                            now_iso=now_iso, trade_date=trade_date)
                        open_trades.append(trade)
                        opened_this += 1
                        ledger.append_event(run_dir, ledger.make_event(
                            event_type="open", timestamp=now_iso,
                            paper_trade_id=trade.paper_trade_id, profile_id=p["profile_id"],
                            reason="selected_signal", trade=trade))

                    ledger.append_profile_tick(run_dir, {
                        "portfolio_run_id": portfolio_run_id, "tick_id": tick_id,
                        "profile_id": p["profile_id"], "status": "ok",
                        "scanner_return_code": rc, "candidate_rows": len(rows),
                        "selected_rows": len(selected), "opened": opened_this,
                        "no_execution": True,
                    })
                    profile_decisions.append({
                        "profile_id": p["profile_id"], "status": "ok",
                        "selected": len(selected), "opened": opened_this,
                    })

            max_open_seen = max(max_open_seen, len(open_trades))

            # ── re-price + exit-check every open trade (TP/SL/EOD) ──
            still_open: list[lifecycle.PaperTrade] = []
            for trade in open_trades:
                rows = rows_by_profile.get(trade.profile_id, [])
                reprice_row = lifecycle.find_repricing_row(trade, rows) if rows else None
                q = lifecycle.spread_quote_from_row(reprice_row) if reprice_row else None
                upd_reason = lifecycle.update_trade_mark(trade, q, now_iso)
                exit_reason, debit = lifecycle.evaluate_exit(trade, config, tick_started)
                if exit_reason:
                    lifecycle.close_trade(trade, exit_reason=exit_reason,
                                          exit_debit=debit, now_iso=now_iso)
                    closed_trades.append(trade)
                    ledger.append_event(run_dir, ledger.make_event(
                        event_type="close", timestamp=now_iso,
                        paper_trade_id=trade.paper_trade_id, profile_id=trade.profile_id,
                        reason=exit_reason, trade=trade))
                else:
                    ledger.append_event(run_dir, ledger.make_event(
                        event_type="update", timestamp=now_iso,
                        paper_trade_id=trade.paper_trade_id, profile_id=trade.profile_id,
                        reason=("quote_unavailable_no_exit" if upd_reason == "quote_unavailable"
                                else "update"),
                        trade=trade))
                    still_open.append(trade)
            open_trades = still_open

            # ── persist this tick ──
            summary = ledger.compute_summary(
                open_trades, closed_trades, max_open_trades_seen=max_open_seen,
                duplicate_skipped_count=dup_skipped, blocked_by_limits_count=blocked,
            )
            ledger.append_portfolio_tick(run_dir, {
                "portfolio_run_id": portfolio_run_id, "tick_id": tick_id,
                "tick_time": now_iso, "scanning": scanning,
                "profile_decisions": profile_decisions,
                "open_trade_count": len(open_trades),
                "closed_trade_count": len(closed_trades),
                "realized_pnl": summary["realized_pnl"],
                "unrealized_pnl": summary["unrealized_pnl"],
                "total_pnl": summary["total_pnl"],
                "duplicate_skipped_count": dup_skipped,
                "blocked_by_limits_count": blocked,
                "no_execution": True,
            })
            _persist("running")
            log.info("portfolio tick %d: open=%d closed=%d total_pnl=%s",
                     tick_id, len(open_trades), len(closed_trades), summary["total_pnl"])

            if (max_ticks is None or tick_id < max_ticks) and args.interval_seconds > 0:
                time.sleep(args.interval_seconds)

        if manifest["status"] == "running":
            manifest["status"] = "completed"
    except KeyboardInterrupt:
        manifest["status"] = "stopped"
        sys.stderr.write("\nportfolio runner: stopped (KeyboardInterrupt)\n")
        exit_code = 0
    finally:
        manifest["ended_at"] = now_et().isoformat()
        ledger.write_manifest(run_dir, latest_dir, manifest)
        _persist(manifest["status"])
        # local-only reconciliation (never touches a broker)
        ledger.reconcile_run(portfolio_run_id, root=out_root,
                             reconciliation_mode=config.position_reconciliation_mode)

    print(f"portfolio run {portfolio_run_id}: status={manifest['status']} "
          f"open={len(open_trades)} closed={len(closed_trades)}")
    print(f"  ledger: {run_dir}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
