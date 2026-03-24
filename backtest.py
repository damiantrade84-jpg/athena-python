"""Backtest API — Engine A/B walk-forward loops live in ``backtest_runner``."""

from __future__ import annotations

from typing import Any

from backtest_runner import backtest_pair, backtest_pair_naked, run_full_backtest


def backtest_engine_c_pair(pair: dict[str, Any], style: str = "intraday") -> dict[str, Any]:
    """Engine C backtest if the monolith exposes it."""
    from athena_legacy import load as _load_legacy

    fn = getattr(_load_legacy(), "backtest_pair_engine_c", None)
    if not callable(fn):
        raise NotImplementedError("Engine C backtest function is not available")
    return fn(pair, style=style)
