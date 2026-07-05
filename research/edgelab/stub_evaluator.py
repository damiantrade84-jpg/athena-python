#!/usr/bin/env python
"""EdgeLab stub evaluator for safe dry-run and pipeline testing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUB = ROOT / "research" / "edgelab" / "stub" / "stub_config.yaml"

SCENARIOS: dict[str, dict[str, float]] = {
    "baseline": {
        "expR": 0.12,
        "profit_factor": 1.35,
        "sqn": 1.1,
        "max_drawdown_R": 1.2,
        "trade_count": 45,
        "oos_score": 0.10,
        "stability_score": 0.04,
        "symbol_concentration": 0.30,
        "cost_sensitivity_penalty": 0.05,
    },
    "evaluate": {
        "expR": 0.12,
        "profit_factor": 1.35,
        "sqn": 1.1,
        "max_drawdown_R": 1.2,
        "trade_count": 45,
        "oos_score": 0.10,
        "stability_score": 0.04,
        "symbol_concentration": 0.30,
        "cost_sensitivity_penalty": 0.05,
    },
    "candidate_good": {
        "expR": 0.20,
        "profit_factor": 1.80,
        "sqn": 2.0,
        "max_drawdown_R": 1.0,
        "trade_count": 60,
        "oos_score": 0.15,
        "stability_score": 0.08,
        "symbol_concentration": 0.25,
        "cost_sensitivity_penalty": 0.02,
    },
    "candidate_bad_trades": {
        "expR": 0.25,
        "profit_factor": 2.0,
        "sqn": 2.5,
        "max_drawdown_R": 0.8,
        "trade_count": 10,
        "oos_score": 0.20,
        "stability_score": 0.10,
        "symbol_concentration": 0.20,
    },
    "candidate_bad_dd": {
        "expR": 0.22,
        "profit_factor": 1.9,
        "sqn": 2.2,
        "max_drawdown_R": 4.5,
        "trade_count": 55,
        "oos_score": 0.14,
        "stability_score": 0.07,
        "symbol_concentration": 0.28,
    },
    "candidate_bad_oos": {
        "expR": 0.21,
        "profit_factor": 1.85,
        "sqn": 2.1,
        "max_drawdown_R": 1.1,
        "trade_count": 52,
        "oos_score": 0.02,
        "stability_score": 0.06,
        "symbol_concentration": 0.27,
    },
    "engine_b_stub": {
        "expR": 0.14,
        "profit_factor": 1.42,
        "sqn": 1.3,
        "max_drawdown_R": 1.1,
        "trade_count": 48,
        "oos_score": 0.11,
        "stability_score": 0.05,
        "symbol_concentration": 0.28,
    },
    "cascade_stub": {
        "expR": 0.13,
        "profit_factor": 1.38,
        "sqn": 1.2,
        "max_drawdown_R": 1.15,
        "trade_count": 42,
        "oos_score": 0.10,
        "stability_score": 0.04,
        "symbol_concentration": 0.32,
    },
    "engine_a_stub": {
        "expR": 0.11,
        "profit_factor": 1.30,
        "sqn": 1.0,
        "max_drawdown_R": 1.25,
        "trade_count": 40,
        "oos_score": 0.09,
        "stability_score": 0.03,
        "symbol_concentration": 0.35,
    },
}


def _read_scenario(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    for line in config_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("evaluator_scenario:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="EdgeLab stub evaluator (research-only)")
    ap.add_argument("--scenario", default="baseline")
    ap.add_argument("--config", default="")
    args = ap.parse_args()

    config_path = Path(args.config) if args.config else DEFAULT_STUB
    scenario = args.scenario
    if scenario == "evaluate":
        from_cfg = _read_scenario(config_path)
        if from_cfg:
            scenario = from_cfg

    metrics = SCENARIOS.get(scenario)
    if metrics is None:
        print(json.dumps({"error": f"unknown scenario: {scenario}"}), file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"metrics": metrics, "scenario": scenario, "research_only": True}))


if __name__ == "__main__":
    main()
