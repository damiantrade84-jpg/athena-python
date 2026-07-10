from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import engine_a_v3.backtest as backtest_module
from engine_a_v3.backtest import _cost_r, _simulate_exit, _summarize, run_v3_backtest
from engine_a_v3.contract import PriceZone


def _bars(*, timeframe: str, count: int = 100) -> list[dict]:
    step = {"D1": timedelta(days=1), "H4": timedelta(hours=4), "H1": timedelta(hours=1)}[
        timeframe
    ]
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "time": (start + index * step).isoformat(),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "vol": 1_000.0,
        }
        for index in range(count)
    ]


def _candles() -> dict[str, list[dict]]:
    return {timeframe: _bars(timeframe=timeframe) for timeframe in ("D1", "H4", "H1")}


def _pair(score_group: str, *, display: str, symbol: str, asset_type: str) -> dict:
    return {
        "display": display,
        "symbol": symbol,
        "type": asset_type,
        "score_group": score_group,
        "enabled": True,
    }


def _trade_signal(*, score_norm: float = 1.0) -> SimpleNamespace:
    signal = SimpleNamespace(
        decision="TRADE",
        qualified=True,
        engineATradeEnabled=True,
        entryZone=PriceZone(low=99.5, high=100.5),
        direction="LONG",
        sl=99.0,
        tp1=101.0,
        tp2=102.0,
        exitPolicy="SINGLE_TP1",
        decisionTime="2025-01-01T00:00:00+00:00",
        signalId="fixture-v3-trade",
        setupId="fixture_setup",
        rejectionReasons=(),
        confluenceScore=2.5,
        maxScore=3.0,
        scoreNorm=score_norm,
    )
    signal.to_dict = lambda: {
        "decision": signal.decision,
        "qualified": signal.qualified,
        "engineATradeEnabled": signal.engineATradeEnabled,
        "scoreNorm": signal.scoreNorm,
    }
    return signal


def _costs() -> dict[str, float]:
    return {
        "spread_bps": 0.0,
        "commission_bps": 0.0,
        "slippage_bps": 0.0,
        "swap_bps_per_day": 0.0,
    }


@pytest.mark.parametrize(
    ("pair", "expected_timeframe"),
    [
        (_pair("forex_majors", display="EUR/USD", symbol="EURUSD", asset_type="forex"), "H1"),
        (_pair("forex_exotics", display="USD/MXN", symbol="USDMXN", asset_type="forex"), "H4"),
        (_pair("crypto_other", display="AAVE/USDT", symbol="AAVEUSDT", asset_type="crypto"), "H4"),
    ],
)
def test_v3_backtest_uses_resolved_entry_timeframe(monkeypatch, pair, expected_timeframe):
    monkeypatch.setattr(backtest_module, "evaluate_engine_a_v3", lambda *args, **kwargs: _trade_signal())

    result = run_v3_backtest(
        pair,
        _candles(),
        horizon="intraday",
        collect_funnel=True,
        **_costs(),
    )

    assert result["entryTimeframe"] == expected_timeframe
    assert result["funnel"]["primaryTf"] == expected_timeframe


def test_v3_backtest_discloses_unreplayed_live_inputs(monkeypatch):
    pair = _pair("forex_majors", display="EUR/USD", symbol="EURUSD", asset_type="forex")
    monkeypatch.setattr(backtest_module, "evaluate_engine_a_v3", lambda *args, **kwargs: _trade_signal())

    result = run_v3_backtest(pair, _candles(), horizon="intraday", **_costs())

    assert result["comparability"]["liveComparable"] is False
    assert result["comparability"]["promotionEligible"] is False
    assert "live_context" in result["comparability"]["unreplayedInputs"]
    assert "live_scan_gates" in result["comparability"]["unreplayedInputs"]


def test_v3_backtest_excludes_confidence_demoted_raw_trade(monkeypatch):
    from config import CONFIG

    pair = _pair("forex_majors", display="EUR/USD", symbol="EURUSD", asset_type="forex")
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", True)
    monkeypatch.setattr(
        backtest_module,
        "evaluate_engine_a_v3",
        lambda *args, **kwargs: _trade_signal(score_norm=0.0),
    )

    result = run_v3_backtest(
        pair,
        _candles(),
        horizon="intraday",
        collect_funnel=True,
        **_costs(),
    )

    assert result["funnel"]["rawQualified"] > 0
    assert result["funnel"]["scanEligible"] == 0
    assert result["funnel"]["tradesTaken"] == 0


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


def test_split_exit_tp1_then_sl_scales_with_tp1_distance():
    # TP1 at 1.5R: half banked at +1.5R, runner half stopped at -1R -> +0.25R.
    bars = [
        {"high": 101.7, "low": 100.0, "close": 101.5},
        {"high": 101.6, "low": 98.9, "close": 99.0},
    ]
    outcome = _simulate_exit(
        bars, direction="LONG", entry=100, sl=99, tp1=101.5, tp2=103,
        exit_policy="SPLIT_50_50",
    )
    assert outcome.outcome == "TP1_THEN_SL"
    assert outcome.result_r == pytest.approx(0.25)


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
