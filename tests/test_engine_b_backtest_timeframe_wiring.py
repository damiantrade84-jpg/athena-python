"""Focused Engine B backtest timeframe-wiring regression checks."""

import pytest

import backtest_runner


def test_engine_b_monitoring_uses_execution_series_and_h4_equivalent_hold() -> None:
    m15 = [{"time": "2026-07-15T10:00:00+00:00"}]
    h4 = [{"time": "2026-07-15T08:00:00+00:00"}]

    monitor_tf, monitor_candles, hold_multiplier = (
        backtest_runner._engine_b_monitoring_context(
            {"M15": m15, "H4": h4}, "M15"
        )
    )

    assert monitor_tf == "M15"
    assert monitor_candles is m15
    assert hold_multiplier == 16


def test_engine_b_monitoring_fails_closed_when_execution_series_is_missing() -> None:
    with pytest.raises(ValueError, match="M15"):
        backtest_runner._engine_b_monitoring_context({"H4": [{}]}, "M15")
