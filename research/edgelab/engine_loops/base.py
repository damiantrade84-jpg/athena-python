"""Engine research loop base types."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research.edgelab.scoring import compute_score, parse_command_output


@dataclass
class EngineFinding:
    engine: str
    symbol: str
    timeframe: str
    finding_type: str
    title: str
    details: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    command: str = ""
    skipped: bool = False
    skip_reason: str = ""


def run_engine_command(
    command: str,
    *,
    repo: Path,
    config: dict[str, Any],
    dry_run: bool,
    engine: str,
) -> tuple[dict[str, float], float, str]:
    if dry_run or not command.strip():
        stub = {
            "expR": 0.13,
            "profit_factor": 1.4,
            "sqn": 1.2,
            "max_drawdown_R": 1.1,
            "trade_count": 45,
            "oos_score": 0.10,
            "stability_score": 0.04,
            "symbol_concentration": 0.30,
        }
        return stub, compute_score(stub, config), "[dry-run]"

    proc = subprocess.run(command, shell=True, cwd=str(repo), capture_output=True, text=True)
    metrics = parse_command_output(
        proc.stdout,
        proc.stderr,
        metrics_file=str(config.get("metrics_output_path") or "") or None,
    )
    if not metrics:
        return {}, 0.0, proc.stderr or proc.stdout or "no metrics"
    return metrics, compute_score(metrics, config), proc.stdout[:500]
