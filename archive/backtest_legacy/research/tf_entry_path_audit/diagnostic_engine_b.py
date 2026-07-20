"""Engine B diagnostic backtest wrapper."""

from __future__ import annotations

from typing import Any

from athena_research.tf_entry_path_audit.matrix import TfMatrixCell, cell_requires_m15, resolve_style_for_cell
from athena_research.tf_entry_path_audit.patches import engine_b_tf_patch, score_group_aware_indicators


def run_engine_b_diagnostic(cell: TfMatrixCell, pair: dict) -> dict[str, Any]:
    """Run Engine B backtest for a matrix cell with research patches only."""
    if cell_requires_m15(cell):
        return {
            "error": "M15 candles not fetched by Engine B harness",
            "blocked": "BLOCKED_DATA",
            "totalTrades": 0,
            "trades": [],
            "_matrix_cell": cell.key,
        }

    import backtest_runner

    style = resolve_style_for_cell(cell)
    with engine_b_tf_patch(signal_tf=cell.signal_tf, entry_tf=cell.entry_tf), score_group_aware_indicators():
        result = backtest_runner.backtest_pair_naked(pair, style=style)

    result["_diagnostic_mode"] = "patched_harness"
    result["_matrix_cell"] = cell.key
    result["_signal_tf"] = cell.signal_tf
    result["_entry_tf"] = cell.entry_tf
    result["_exit_tf"] = cell.exit_tf
    result["_execution_tf"] = cell.execution_tf
    return result
