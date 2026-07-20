from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import engine_a_v3.backtest as backtest_module
from engine_a_v3.backtest import _cost_r, _simulate_exit, _summarize, run_v3_backtest
from engine_a_v3.contract import PriceZone
from engine_a_v3.evaluator import BacktestCandleValidationIndex
from engine_a_v3.indicator_adapter import BacktestIndicatorSnapshotCache, indicator_snapshot


def _bars(*, timeframe: str, count: int = 100) -> list[dict]:
    step = {
        "D1": timedelta(days=1),
        "H4": timedelta(hours=4),
        "H1": timedelta(hours=1),
        "M30": timedelta(minutes=30),
        "M15": timedelta(minutes=15),
    }[timeframe]
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


def test_vectorized_snapshot_cache_matches_prefix_indicator_path_exactly():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(420):
        center = 100.0 + math.sin(index / 7.0)
        rows.append(
            {
                "time": (start + timedelta(hours=index)).isoformat(),
                "open": center,
                "high": center + 1.2,
                "low": center - 1.2,
                "close": center + (0.2 * math.cos(index / 3.0)),
                "vol": 1_000.0 + index,
            }
        )
    periods = {
        "ema_trend": 21,
        "ema_momentum": 50,
        "ema_long": 200,
        "rsi": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr": 14,
        "adx": 14,
    }
    cache = BacktestIndicatorSnapshotCache({"H1": rows})

    for length in (80, 120, 220, 319, 420):
        expected = indicator_snapshot(rows[:length], periods, "forex")
        actual = cache.snapshot_at("H1", length, periods, "forex")
        assert actual == expected


def test_setup_series_cache_matches_prefix_atr_ema_path_exactly():
    from engine_a_v3.setups import SetupSeriesCache, _atr, _ema_last

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(420):
        center = 100.0 + math.sin(index / 7.0)
        rows.append(
            {
                "time": (start + timedelta(hours=index)).isoformat(),
                "open": center,
                "high": center + 1.2,
                "low": center - 1.2,
                "close": center + (0.2 * math.cos(index / 3.0)),
                "vol": 1_000.0 + index,
            }
        )
    cache = SetupSeriesCache({"H1": rows})

    for length in (2, 15, 80, 120, 220, 319, 420):
        prefix = rows[:length]
        closes = [float(c["close"]) for c in prefix]
        for period in (8, 14, 21, 50):
            assert _atr(prefix, period, series_cache=cache, timeframe="H1") == _atr(
                prefix, period
            )
            assert _ema_last(
                closes, period, series_cache=cache, timeframe="H1", candles=prefix
            ) == _ema_last(closes, period)
            arr = cache.ema_series_view("H1", length, period)
            assert arr is not None
            from engine_a_v3.setups import _ema

            assert arr[length - 1] == _ema(closes, period)[-1]
            calc_arr = cache.calc_ema_series_view("H1", length, period)
            from indicators import calc_ema

            expected_calc = calc_ema(closes, period)
            assert calc_arr is not None
            assert calc_arr[length - 1] == expected_calc[length - 1]


def test_setup_series_cache_matches_rejects_cross_timeframe_substitution():
    from engine_a_v3.setups import SetupSeriesCache

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def _series(step_hours: int, count: int) -> list[dict]:
        return [
            {
                "time": (start + timedelta(hours=step_hours * i)).isoformat(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + i * 0.01,
                "vol": 1.0,
            }
            for i in range(count)
        ]

    h1 = _series(1, 200)
    h4 = _series(4, 120)
    cache = SetupSeriesCache({"H1": h1, "H4": h4})

    # True prefixes (and copies of prefixes) are accepted.
    assert cache.matches("H1", h1[:50])
    assert cache.matches("H1", [dict(r) for r in h1[:50]])
    # Another timeframe's rows under the H1 name are rejected -- this is the
    # frame-resolution fallback (`candles.get(tf) or candles.get("H4")`).
    assert not cache.matches("H1", h4[:50])
    assert not cache.matches("H1", [])
    assert not cache.matches("H1", h1[:50] + h4[:1])


def test_backtest_validation_index_preserves_point_in_time_invalid_boundary():
    rows = _bars(timeframe="H1", count=120)
    rows[90] = {**rows[90], "high": 98.0}
    index = BacktestCandleValidationIndex({"H1": rows})

    assert index.failure_for("H1", rows[:90]) == (True, None)
    assert index.failure_for("H1", rows[:91]) == (True, "ohlc_invalid")


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
        (_pair("forex_exotics", display="USD/MXN", symbol="USDMXN", asset_type="forex"), "H1"),
        (_pair("crypto_other", display="AAVE/USDT", symbol="AAVEUSDT", asset_type="crypto"), "H1"),
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
    assert "timeframe_speed_adaptation" in result["comparability"]["unreplayedInputs"]


def test_v3_backtest_applies_policy_setup_and_trigger_timeframes(monkeypatch):
    pair = _pair("forex_majors", display="EUR/USD", symbol="EURUSD", asset_type="forex")
    candles = {
        timeframe: _bars(timeframe=timeframe)
        for timeframe in ("D1", "H4", "H1", "M30", "M15")
    }
    policy = {
        "regime": "D1",
        "bias": "H4",
        "structure": "H1",
        "setup": "M30",
        "trigger": "M15",
        "execution": "M5",
    }
    captured: list[dict] = []

    def _recording_eval(*args, **kwargs):
        captured.append(kwargs)
        return _trade_signal()

    monkeypatch.setattr(backtest_module, "evaluate_engine_a_v3", _recording_eval)
    result = run_v3_backtest(
        pair,
        candles,
        horizon="intraday",
        collect_funnel=True,
        policy_timeframes=policy,
        **_costs(),
    )

    assert result["entryTimeframe"] == "M30"
    assert result["funnel"]["primaryTf"] == "M30"
    assert result["policyTimeframesApplied"] == policy
    assert captured
    assert all(call.get("policy_timeframes") == policy for call in captured)


def test_scan_speed_state_is_used_for_engine_a_scoring_policy():
    root = Path(__file__).resolve().parents[1]
    athena_src = (root / "athena.py").read_text(encoding="utf-8")
    scanner_src = (root / "scanner.py").read_text(encoding="utf-8")

    assert "timeframe_speed_state=None" in athena_src
    assert "speed_state=timeframe_speed_state" in athena_src
    assert '"timeframe_speed_state": _speed_state' in scanner_src


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
