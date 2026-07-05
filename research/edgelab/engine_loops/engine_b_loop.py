"""Engine B research loop (priority 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from research.edgelab.engine_loops.base import EngineFinding, run_engine_command


def run_engine_b_loop(
    *,
    repo: Path,
    config: dict[str, Any],
    symbol: str,
    timeframe: str,
    dry_run: bool,
    can_run: bool,
) -> EngineFinding:
    if not can_run:
        return EngineFinding(
            engine="engine_b",
            symbol=symbol,
            timeframe=timeframe,
            finding_type="data_blocked",
            title=f"Engine B skipped for {symbol} {timeframe}",
            skipped=True,
            skip_reason="data_freshness_blocked",
        )
    cmd = (config.get("engine_commands") or {}).get("engine_b") or ""
    metrics, score, excerpt = run_engine_command(cmd, repo=repo, config=config, dry_run=dry_run, engine="engine_b")
    return EngineFinding(
        engine="engine_b",
        symbol=symbol,
        timeframe=timeframe,
        finding_type="research_eval",
        title=f"Engine B research eval {symbol} {timeframe}",
        details={"command_excerpt": excerpt, "priority": 1},
        metrics=metrics,
        score=score,
        command=cmd,
    )
