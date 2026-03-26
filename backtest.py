"""Backtest API — Engine A/B walk-forward loops live in ``backtest_runner``."""

from __future__ import annotations

from typing import Any

from backtest_runner import (
    backtest_pair as _runner_backtest_pair,
    backtest_pair_naked as _runner_backtest_pair_naked,
    run_full_backtest as _runner_run_full_backtest,
)
from research_metrics import enrich_backtest_summary


def backtest_pair(pair: dict[str, Any], style: str = "auto") -> dict[str, Any]:
    return _runner_backtest_pair(pair, style=style)


def backtest_pair_naked(pair: dict[str, Any], style: str = "naked") -> dict[str, Any]:
    return _runner_backtest_pair_naked(pair, style=style)


def run_full_backtest(style: str = "auto", asset_class: str | None = None) -> dict[str, Any]:
    return _runner_run_full_backtest(style=style, asset_class=asset_class)


def backtest_engine_c_pair(pair: dict[str, Any], style: str = "intraday") -> dict[str, Any]:
    """Engine C backtest if the monolith exposes it."""
    from athena_legacy import load as _load_legacy

    fn = getattr(_load_legacy(), "backtest_pair_engine_c", None)
    if not callable(fn):
        raise NotImplementedError("Engine C backtest function is not available")
    result = fn(pair, style=style)
    return enrich_backtest_summary(result)
