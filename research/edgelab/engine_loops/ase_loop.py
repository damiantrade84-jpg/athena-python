"""ASE research loop — only when command configured."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from research.edgelab.engine_loops.base import EngineFinding, run_engine_command


def run_ase_loop(
    *,
    repo: Path,
    config: dict[str, Any],
    symbol: str,
    timeframe: str,
    dry_run: bool,
    can_run: bool,
) -> EngineFinding:
    cmd = (config.get("engine_commands") or {}).get("ase") or ""
    if not cmd and not dry_run:
        return EngineFinding(
            engine="ase",
            symbol=symbol,
            timeframe=timeframe,
            finding_type="not_configured",
            title="ASE loop not configured",
            skipped=True,
            skip_reason="no_safe_command",
        )
    if not can_run:
        return EngineFinding(
            engine="ase",
            symbol=symbol,
            timeframe=timeframe,
            finding_type="data_blocked",
            title=f"ASE skipped for {symbol} {timeframe}",
            skipped=True,
            skip_reason="data_freshness_blocked",
        )
    metrics, score, excerpt = run_engine_command(cmd or "echo stub", repo=repo, config=config, dry_run=dry_run, engine="ase")
    return EngineFinding(
        engine="ase",
        symbol=symbol,
        timeframe=timeframe,
        finding_type="research_eval",
        title=f"ASE research eval {symbol} {timeframe}",
        details={"command_excerpt": excerpt},
        metrics=metrics,
        score=score,
        command=cmd,
    )
