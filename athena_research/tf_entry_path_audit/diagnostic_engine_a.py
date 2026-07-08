"""Engine A diagnostic backtest with decoupled signal/entry/exit timeframes."""

from __future__ import annotations

import bisect
from typing import Any

from athena_research.tf_entry_path_audit.matrix import TfMatrixCell


def _horizon_for_signal_tf(signal_tf: str) -> str:
    if signal_tf == "H1":
        return "intraday"
    return "swing"


def _baseline_engine_a(cell: TfMatrixCell, pair: dict) -> dict[str, Any]:
    """Run standard harness when signal/entry/exit align with production mapping."""
    import backtest_runner

    style = cell.horizon or _horizon_for_signal_tf(cell.signal_tf)
    return backtest_runner.backtest_pair(pair, style=style)


def run_engine_a_diagnostic(cell: TfMatrixCell, pair: dict, *, candles: dict | None = None) -> dict[str, Any]:
    """Run Engine A backtest for a matrix cell."""
    aligned = cell.signal_tf == cell.entry_tf == cell.exit_tf
    if aligned and cell.signal_tf in ("H1", "H4"):
        result = _baseline_engine_a(cell, pair)
        result["_diagnostic_mode"] = "baseline_harness"
        result["_matrix_cell"] = cell.key
        return result

    if cell.entry_tf == "M15" or cell.execution_tf == "M15":
        return {
            "error": "M15 candles not fetched by Engine A harness",
            "blocked": "BLOCKED_DATA",
            "totalTrades": 0,
            "trades": [],
            "_matrix_cell": cell.key,
        }

    from athena_research.tf_entry_path_audit.diagnostic_v3 import run_diagnostic_v3_backtest

    return run_diagnostic_v3_backtest(
        pair,
        signal_tf=cell.signal_tf,
        entry_tf=cell.entry_tf,
        exit_tf=cell.exit_tf,
        execution_tf=cell.execution_tf,
        horizon=cell.horizon or _horizon_for_signal_tf(cell.signal_tf),
        candles=candles,
    )
