"""Cascade (Engine C) research loop (priority 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from research.edgelab.engine_loops.base import EngineFinding, run_engine_command


def run_cascade_loop(
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
            engine="cascade",
            symbol=symbol,
            timeframe=timeframe,
            finding_type="data_blocked",
            title=f"Cascade skipped for {symbol} {timeframe}",
            skipped=True,
            skip_reason="data_freshness_blocked",
        )
    cmd = (config.get("engine_commands") or {}).get("cascade") or ""
    metrics, score, excerpt = run_engine_command(cmd, repo=repo, config=config, dry_run=dry_run, engine="cascade")
    return EngineFinding(
        engine="cascade",
        symbol=symbol,
        timeframe=timeframe,
        finding_type="research_eval",
        title=f"Cascade research eval {symbol} {timeframe}",
        details={"command_excerpt": excerpt, "priority": 2},
        metrics=metrics,
        score=score,
        command=cmd,
    )
