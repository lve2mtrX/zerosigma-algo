"""Probe configured ZeroSigma Greek fields without printing payloads or secrets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.providers.structure.factory import build_structure_provider
from src.providers.structure.greek_parity import write_greek_parity_audit
from src.providers.structure.greek_probe import DEFAULT_PROBE_METRICS, probe_configured_provider
from src.utils.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sanitized ZeroSigma Greek API field probe")
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--metrics", default=",".join(DEFAULT_PROBE_METRICS))
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--write-latest", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(REPO_ROOT)
    provider, resolved = build_structure_provider(cfg, override="zerosigma_api")
    metrics = [metric.strip() for metric in args.metrics.split(",") if metric.strip()]
    report = probe_configured_provider(provider, symbol=args.symbol, metrics=metrics)
    report["resolved_provider"] = resolved

    output_path: Path | None = Path(args.output) if args.output else None
    if args.write_latest and output_path is None:
        output_path = cfg.output_dir / "research" / "latest" / (
            f"greek_api_probe_{args.symbol.upper()}.json"
        )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_greek_parity_audit(output_path.parent)

    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"Greek API probe {report['symbol']}: status={report.get('status')} "
            f"available={len(report['available_metrics'])} missing={len(report['missing_metrics'])}"
        )
        if output_path is not None:
            print(f"  sanitized report: {output_path.resolve()}")
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
