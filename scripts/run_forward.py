"""Forward runner — Phase 7: start/stop local paper MONITORING.

Repeatedly runs the EXISTING scanner (`scripts.run_scanner.main`, in-process —
same code path, no duplicated strategy logic) from a saved Phase 6 run-profile,
and records each tick's decision/signals into a local ledger.

THIS IS MONITORING + A LOCAL LEDGER ONLY. It NEVER places orders, submits paper
orders, calls order preview, or executes anything. There is no broker call here.

  python -m scripts.run_forward --profile PROFILE_ID
  python -m scripts.run_forward --profile PROFILE_ID --interval-seconds 60
  python -m scripts.run_forward --profile PROFILE_ID --max-ticks 5
  python -m scripts.run_forward --profile PROFILE_ID --once
  python -m scripts.run_forward --profile PROFILE_ID --dry-run
  python -m scripts.run_forward --profile PROFILE_ID --market-hours-only
  python -m scripts.run_forward --profile PROFILE_ID --output-dir outputs/forward

Ledger (under --output-dir, default outputs/forward):
  runs/{run_id}/run_manifest.json   tick_log.jsonl   signal_log.jsonl
                selected_trades.csv  no_trade_log.jsonl  heartbeat.json
                scanner/            (the scanner's own per-run outputs)
  latest/  ← mirror of the most recent run's manifest + heartbeat
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Marker so audits can grep-prove this runner has no execution surface.
NO_EXECUTION = True
EXECUTION_MODE = "disabled_local_monitoring"

# Columns mirrored from ranked_candidates.csv into signal_log / selected_trades.
_SIGNAL_FIELDS = (
    "side", "selected_expiry", "target_dte", "short_strike", "long_strike",
    "credit", "score", "daily_selector_mode", "selector_reason", "selector_score",
    "quote_provider", "quote_chain_root", "quote_timestamp", "quote_age_seconds",
    "quote_quality_bucket", "planned_stop_risk_dollars",
    "planned_stop_risk_cap_dollars", "planned_stop_risk_pct",
    "theoretical_max_loss_dollars", "risk_rejection_type", "risk_rejection_reason",
)


# ── small helpers ────────────────────────────────────────────────────────────

def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def _is_rth(dt) -> bool:  # type: ignore[no-untyped-def]
    """Regular US trading hours: weekday, 09:30–16:00 ET. Simple rule (no holiday
    calendar this phase — Phase 7.x). `dt` must be ET-aware (from now_et())."""
    if dt.weekday() >= 5:                       # Sat/Sun
        return False
    minutes = dt.hour * 60 + dt.minute
    return (9 * 60 + 30) <= minutes <= (16 * 60)


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, default=str)


def _append_csv(path: Path, row: dict, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k) for k in fieldnames})


def _read_jsonl_tail(path: Path, since_line: int) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i < since_line or not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def _read_selected_rows(csv_path: Path) -> tuple[list[dict], int]:
    """Return (selected_rows, total_rows) from a ranked_candidates.csv."""
    if not csv_path.is_file():
        return [], 0
    with csv_path.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    selected = [r for r in rows if str(r.get("selected_trade", "")).lower() == "true"]
    return selected, len(rows)


def _signal_identity(row: dict, profile_hash, target_dte, trade_date: str) -> str:
    return "|".join(str(x) for x in (
        profile_hash, row.get("symbol") or row.get("underlying") or "",
        row.get("selected_expiry"), row.get("side"),
        row.get("short_strike"), row.get("long_strike"), target_dte, trade_date,
    ))


# ── one tick: invoke the scanner + capture its result ───────────────────────

def _run_scanner_tick(scanner_argv: list[str], scanner_out: Path) -> tuple[int, list[dict], list[dict]]:
    """Run the scanner in-process pointed at `scanner_out`. Returns
    (return_code, this_tick_decision_records, this_tick_selected_rows)."""
    import importlib
    run_scanner = importlib.import_module("scripts.run_scanner")

    latest_log = scanner_out / "latest" / "decision_log.jsonl"
    pre = _count_lines(latest_log)

    prev_output_dir = os.environ.get("OUTPUT_DIR")
    os.environ["OUTPUT_DIR"] = str(scanner_out)
    try:
        rc = run_scanner.main(scanner_argv)
    finally:
        if prev_output_dir is None:
            os.environ.pop("OUTPUT_DIR", None)
        else:
            os.environ["OUTPUT_DIR"] = prev_output_dir

    decisions = _read_jsonl_tail(latest_log, pre)
    selected, _ = _read_selected_rows(scanner_out / "latest" / "ranked_candidates.csv")
    return rc, decisions, selected


def main(argv: list[str] | None = None) -> int:
    from src.config.strategy_profiles import load_profile_file
    from src.utils.time import now_et

    parser = argparse.ArgumentParser(
        description="ZerσSigma forward runner — local paper MONITORING (no execution)",
    )
    parser.add_argument("--profile", required=True,
                        help="strategy run-profile id or path (Phase 6)")
    parser.add_argument("--interval-seconds", dest="interval_seconds", type=float, default=60.0,
                        help="seconds between ticks (default 60; 0 = no sleep)")
    parser.add_argument("--max-ticks", dest="max_ticks", type=int, default=None,
                        help="stop after N ticks (default: unlimited)")
    parser.add_argument("--once", action="store_true", help="run exactly one tick")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="validate profile + print planned config; do NOT scan")
    parser.add_argument("--market-hours-only", dest="market_hours_only", action="store_true",
                        help="skip scanning outside RTH (09:30–16:00 ET weekdays)")
    parser.add_argument("--output-dir", dest="output_dir", default="outputs/forward",
                        help="forward ledger root (default outputs/forward)")
    # Safe passthrough overrides (CLI > profile in the scanner). No execution flags.
    parser.add_argument("--quote-provider", dest="quote_provider", default=None,
                        choices=["mock", "null", "tastytrade"])
    parser.add_argument("--structure-provider", dest="structure_provider", default=None,
                        choices=["stub", "zerosigma_api"])
    args = parser.parse_args(argv)

    # ── load + validate the run-profile (Phase 6) ──
    res = load_profile_file(args.profile)
    if not res.ok or res.profile is None:
        sys.stderr.write(f"forward runner: profile {args.profile!r} is invalid / not found:\n")
        for e in res.errors:
            sys.stderr.write(f"  - {e}\n")
        return 2
    prof = res.profile

    max_ticks = 1 if args.once else args.max_ticks
    eff_quote = args.quote_provider or prof.quote_provider
    eff_structure = args.structure_provider or prof.structure_provider

    started = now_et()
    run_id = f"{started.strftime('%Y%m%d_%H%M%S')}_{prof.profile_id}"
    out_root = Path(args.output_dir)
    if not out_root.is_absolute():
        out_root = REPO_ROOT / out_root
    run_dir = out_root / "runs" / run_id
    latest_dir = out_root / "latest"
    scanner_out = run_dir / "scanner"

    manifest = {
        "run_id": run_id,
        "profile_id": prof.profile_id,
        "profile_name": prof.profile_name,
        "profile_hash": prof.profile_hash(),
        "profile_path": res.path,
        "started_at": started.isoformat(),
        "ended_at": None,
        "status": "dry_run" if args.dry_run else "running",
        "interval_seconds": args.interval_seconds,
        "max_ticks": max_ticks,
        "dry_run": bool(args.dry_run),
        "quote_provider": eff_quote,
        "structure_provider": eff_structure,
        "daily_selector": prof.daily_selector,
        "target_dte": prof.target_dte,
        "no_execution": True,
        "execution_mode": EXECUTION_MODE,
        "git_commit": _git_commit(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "market_hours_only": bool(args.market_hours_only),
    }

    def _persist_manifest() -> None:
        _write_json(run_dir / "run_manifest.json", manifest)
        _write_json(latest_dir / "run_manifest.json", manifest)
        # Phase 8 — pointer so the review tooling resolves "latest" robustly.
        _write_json(latest_dir / "latest_run_pointer.json", {
            "run_id": run_id, "run_path": str(run_dir),
            "status": manifest["status"], "updated_at": manifest.get("ended_at")
                                                          or manifest["started_at"],
        })

    def _persist_heartbeat(hb: dict) -> None:
        _write_json(run_dir / "heartbeat.json", hb)
        _write_json(latest_dir / "heartbeat.json", hb)

    # ── --dry-run: validate + print plan + write a dry-run manifest only ──
    if args.dry_run:
        _persist_manifest()
        _persist_heartbeat({
            "run_id": run_id, "status": "dry_run", "latest_tick_time": None,
            "latest_decision": None, "selected_trade": False, "dry_run": True,
        })
        print("=== FORWARD RUN (DRY-RUN — NO SCAN, NO EXECUTION) ===")
        for k in ("run_id", "profile_id", "profile_name", "profile_hash",
                  "interval_seconds", "max_ticks", "quote_provider",
                  "structure_provider", "daily_selector", "target_dte",
                  "market_hours_only", "execution_mode", "no_execution"):
            print(f"  {k}={manifest[k]!r}")
        print(f"  manifest={run_dir / 'run_manifest.json'}")
        print("---")
        return 0

    # ── live monitoring loop ──
    scanner_argv = ["--profile", args.profile]
    if args.quote_provider:
        scanner_argv += ["--quote-provider", args.quote_provider]
    if args.structure_provider:
        scanner_argv += ["--structure-provider", args.structure_provider]

    _persist_manifest()
    emitted: set[str] = set()
    exit_code = 0
    tick_id = 0
    last_decision = None
    last_selected = False
    import logging
    log = logging.getLogger("forward")

    try:
        while max_ticks is None or tick_id < max_ticks:
            tick_id += 1
            tick_started = now_et()
            tick: dict = {
                "run_id": run_id, "tick_id": tick_id,
                "tick_started_at": tick_started.isoformat(),
                "tick_finished_at": None, "status": None,
                "scanner_return_code": None, "scanner_decision": None,
                "pre_selector_decision": None, "post_selector_decision": None,
                "selected_trade": False, "selected_candidate_summary": None,
                "selector_no_trade_reason": None, "duplicate_selected_signal": False,
                "output_files": [], "error": None,
                "no_execution": True,
            }

            # market-hours guard
            if args.market_hours_only and not _is_rth(tick_started):
                tick["status"] = "skipped_market_closed"
                tick["tick_finished_at"] = now_et().isoformat()
                _append_jsonl(run_dir / "tick_log.jsonl", tick)
                last_decision = "skipped_market_closed"
                _persist_heartbeat({
                    "run_id": run_id, "status": "running", "tick_id": tick_id,
                    "latest_tick_time": tick["tick_finished_at"],
                    "latest_decision": last_decision, "selected_trade": False,
                })
                log.info("forward tick %d: skipped (market closed)", tick_id)
                if (max_ticks is None or tick_id < max_ticks) and args.interval_seconds > 0:
                    time.sleep(args.interval_seconds)
                continue

            # run the scanner (same code path as `python -m scripts.run_scanner`)
            try:
                rc, decisions, selected = _run_scanner_tick(scanner_argv, scanner_out)
            except KeyboardInterrupt:
                raise
            except Exception as exc:                       # never crash the loop on a tick bug
                tick["status"] = "error"
                tick["error"] = f"{type(exc).__name__}: {exc}"
                tick["tick_finished_at"] = now_et().isoformat()
                _append_jsonl(run_dir / "tick_log.jsonl", tick)
                manifest["status"] = "error"
                exit_code = 1
                log.error("forward tick %d errored: %s", tick_id, tick["error"])
                break

            tick["scanner_return_code"] = rc
            primary = decisions[-1] if decisions else None
            snap = (primary or {}).get("snapshot_summary", {}) if primary else {}
            tick["scanner_decision"] = (primary or {}).get("decision")
            tick["pre_selector_decision"] = snap.get("pre_selector_decision")
            tick["post_selector_decision"] = snap.get("post_selector_decision")
            tick["selector_no_trade_reason"] = snap.get("selector_no_trade_reason")
            tick["output_files"] = [
                str(scanner_out / "latest" / "ranked_candidates.csv"),
                str(scanner_out / "latest" / "decision_log.jsonl"),
            ]

            if rc != 0:
                tick["status"] = "error"
                tick["error"] = f"scanner returned {rc}"
                tick["tick_finished_at"] = now_et().isoformat()
                _append_jsonl(run_dir / "tick_log.jsonl", tick)
                manifest["status"] = "error"
                exit_code = 1
                log.error("forward tick %d: scanner rc=%d", tick_id, rc)
                break

            trade_date = tick_started.date().isoformat()
            new_signals = []
            for row in selected:
                ident = _signal_identity(row, manifest["profile_hash"], prof.target_dte, trade_date)
                if ident in emitted:
                    tick["duplicate_selected_signal"] = True
                    continue
                emitted.add(ident)
                new_signals.append(row)

            if selected:
                tick["selected_trade"] = True
                top = selected[0]
                tick["selected_candidate_summary"] = {
                    "side": top.get("side"), "short_strike": top.get("short_strike"),
                    "long_strike": top.get("long_strike"), "credit": top.get("credit"),
                    "score": top.get("score"), "selected_expiry": top.get("selected_expiry"),
                }
                tick["status"] = "ok"
                for row in new_signals:
                    sig = {
                        "run_id": run_id, "tick_id": tick_id,
                        "emitted_at": now_et().isoformat(),
                        "profile_id": manifest["profile_id"],
                        "profile_hash": manifest["profile_hash"],
                        "symbol": prof.symbol,
                        "resolved_root_symbol": row.get("quote_chain_root"),
                        "trade_date": trade_date,
                        **{f: row.get(f) for f in _SIGNAL_FIELDS},
                    }
                    _append_jsonl(run_dir / "signal_log.jsonl", sig)
                    _append_csv(run_dir / "selected_trades.csv", sig, list(sig.keys()))
            else:
                tick["status"] = "ok"
                _append_jsonl(run_dir / "no_trade_log.jsonl", {
                    "run_id": run_id, "tick_id": tick_id,
                    "logged_at": now_et().isoformat(),
                    "profile_id": manifest["profile_id"],
                    "profile_hash": manifest["profile_hash"],
                    "target_dte": prof.target_dte,
                    "daily_selector": prof.daily_selector,
                    "no_trade_reason": snap.get("selector_no_trade_reason")
                                       or tick["post_selector_decision"] or "no_selected_trade",
                    "selector_blockers": snap.get("selector_blockers"),
                })

            tick["tick_finished_at"] = now_et().isoformat()
            _append_jsonl(run_dir / "tick_log.jsonl", tick)

            last_decision = tick["post_selector_decision"] or tick["scanner_decision"]
            last_selected = tick["selected_trade"]
            _persist_heartbeat({
                "run_id": run_id, "status": "running", "tick_id": tick_id,
                "latest_tick_time": tick["tick_finished_at"],
                "latest_decision": last_decision, "selected_trade": last_selected,
            })
            log.info("forward tick %d: decision=%s selected_trade=%s dup=%s",
                     tick_id, last_decision, last_selected, tick["duplicate_selected_signal"])

            if (max_ticks is None or tick_id < max_ticks) and args.interval_seconds > 0:
                time.sleep(args.interval_seconds)

        if manifest["status"] == "running":
            manifest["status"] = "completed"
    except KeyboardInterrupt:
        manifest["status"] = "stopped"
        sys.stderr.write("\nforward runner: stopped (KeyboardInterrupt)\n")
        exit_code = 0
    finally:
        manifest["ended_at"] = now_et().isoformat()
        _persist_manifest()
        _persist_heartbeat({
            "run_id": run_id, "status": manifest["status"],
            "tick_id": tick_id,
            "latest_tick_time": manifest["ended_at"],
            "latest_decision": last_decision, "selected_trade": last_selected,
        })

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
