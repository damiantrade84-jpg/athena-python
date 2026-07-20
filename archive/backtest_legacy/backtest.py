"""Backtest API — Engine A/B walk-forward loops live in ``backtest_runner``."""

from __future__ import annotations

from typing import Any

from backtest_runner import (
    backtest_pair as _runner_backtest_pair,
    backtest_pair_consensus as _runner_backtest_pair_consensus,
    backtest_pair_naked as _runner_backtest_pair_naked,
    run_full_backtest as _runner_run_full_backtest,
)


def backtest_pair(pair: dict[str, Any], style: str = "auto", **kwargs: Any) -> dict[str, Any]:
    return _runner_backtest_pair(pair, style=style, **kwargs)


def backtest_pair_naked(
    pair: dict[str, Any], style: str = "naked", **kwargs: Any
) -> dict[str, Any]:
    return _runner_backtest_pair_naked(pair, style=style, **kwargs)


def run_full_backtest(
    style: str = "auto",
    asset_class: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _runner_run_full_backtest(style=style, asset_class=asset_class, **kwargs)


def backtest_engine_c_pair(
    pair: dict[str, Any], style: str = "intraday", **kwargs: Any
) -> dict[str, Any]:
    """Consensus (Engine C) backtest via ``backtest_pair_consensus`` (same pipeline as monolith routes)."""

    return _runner_backtest_pair_consensus(pair, style=style, **kwargs)
