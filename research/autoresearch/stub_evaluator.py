#!/usr/bin/env python
"""Stub research evaluator for safe AutoResearch end-to-end testing.

Reads research/autoresearch/stub/stub_config.yaml scenario and prints JSON metrics
to stdout. Does not import live execution modules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUB_CONFIG = ROOT / "research" / "autoresearch" / "stub" / "stub_config.yaml"

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
}


def _read_scenario_from_config(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    text = config_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("evaluator_scenario:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def resolve_scenario(cli_scenario: str, config_path: Path) -> str:
    if cli_scenario == "evaluate":
        from_config = _read_scenario_from_config(config_path)
        if from_config:
            return from_config
        return "evaluate"
    return cli_scenario


def main() -> None:
    ap = argparse.ArgumentParser(description="AutoResearch stub evaluator (research-only)")
    ap.add_argument("--scenario", default="baseline", help="baseline|evaluate|candidate_*")
    ap.add_argument("--config", default="", help="Optional stub_config.yaml path")
    args = ap.parse_args()

    config_path = Path(args.config) if args.config else DEFAULT_STUB_CONFIG
    scenario = resolve_scenario(args.scenario, config_path)
    metrics = SCENARIOS.get(scenario)
    if metrics is None:
        print(json.dumps({"error": f"unknown scenario: {scenario}"}), file=sys.stderr)
        sys.exit(1)

    payload = {"metrics": metrics, "scenario": scenario, "research_only": True}
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
