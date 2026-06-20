from __future__ import annotations

import argparse
import json
from pathlib import Path

from .preflight import build_preflight_report

DEFAULT_GATE = Path(
    "logs/commodity_data_audit/forensics/consolidated_v4/"
    "commodity_authoritative_gate_table_v3.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Commodity Trend V1 fail-closed preflight")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--gate", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--output-dir", type=Path, default=Path("logs/commodity_trend_v1/preflight"))
    args = parser.parse_args()

    root = args.repo_root.resolve()
    gate = args.gate if args.gate.is_absolute() else root / args.gate
    report = build_preflight_report(root, gate)
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{report['run_hash']}.json"
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "run_hash": report["run_hash"], "output": str(output_path)}))
    return 0 if report["verdict"] == "READY_FOR_STRATEGY_RESEARCH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
