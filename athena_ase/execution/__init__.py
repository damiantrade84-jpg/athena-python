"""ASE standalone demo execution bridge (no Engine A/B/C/D)."""

from athena_ase.execution.bridge import (
    ASEExecutionDeps,
    ase_signal_to_execution_dict,
    enforce_time_stops,
    execute_trade_signal,
)

__all__ = [
    "ASEExecutionDeps",
    "ase_signal_to_execution_dict",
    "enforce_time_stops",
    "execute_trade_signal",
]
