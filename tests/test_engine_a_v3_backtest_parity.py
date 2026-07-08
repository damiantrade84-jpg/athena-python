from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine_a_v3.backtest import _cost_r, _simulate_exit, _summarize


def test_financing_cost_uses_elapsed_time_not_horizon_bar_assumptions():
    entered = datetime(2026, 1, 1, tzinfo=timezone.utc)
    exited = entered + timedelta(hours=48)
    cost = _cost_r(
        100.0, 99.0, spread_bps=0, commission_bps=0, slippage_bps=0,
        swap_bps_per_day=10, entry_time=entered, exit_time=exited,
    )
    assert cost == pytest.approx(0.2)


def test_split_exit_keeps_original_stop_and_realizes_zero_when_runner_stops():
    bars = [
        {"high": 101.2, "low": 100.0, "close": 101.0},
        {"high": 101.1, "low": 98.9, "close": 99.0},
    ]
    outcome = _simulate_exit(
        bars, direction="LONG", entry=100, sl=99, tp1=101, tp2=102,
        exit_policy="SPLIT_50_50",
    )
    assert outcome.outcome == "TP1_THEN_SL"
    assert outcome.result_r == pytest.approx(0.0)
    assert outcome.exit_offset == 1


def test_split_exit_returns_one_point_five_r_and_adverse_same_bar_is_sl():
    winner = _simulate_exit(
        [{"high": 101.2, "low": 99.5, "close": 101}, {"high": 102.1, "low": 100.5, "close": 102}],
        direction="LONG", entry=100, sl=99, tp1=101, tp2=102,
        exit_policy="SPLIT_50_50",
    )
    assert winner.result_r == pytest.approx(1.5)

    adverse = _simulate_exit(
        [{"high": 102.1, "low": 98.9, "close": 101}],
        direction="LONG", entry=100, sl=99, tp1=101, tp2=102,
        exit_policy="SPLIT_50_50",
    )
    assert adverse.outcome == "SL"
    assert adverse.result_r == -1.0
    assert adverse.same_bar is True


def test_engine_a_summary_reports_direction_breakdown_from_all_trades():
    summary = _summarize(
        {"display": "USD/JPY", "symbol": "USDJPY", "type": "forex"},
        "intraday",
        [
            {"direction": "LONG", "resultR": 1.0},
            {"direction": "SHORT", "resultR": -0.5},
            {"direction": "SHORT", "resultR": 0.25},
        ],
        same_bar=0,
    )

    assert summary["directionBreakdown"] == {
        "LONG": 1,
        "SHORT": 2,
        "UNKNOWN": 0,
    }
