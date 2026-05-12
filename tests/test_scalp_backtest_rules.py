from datetime import datetime, timezone, timedelta
import types
import pytest

import backtest_runner
import indicators
import mt5_executor
import scalp_engine
import volume_profile


def _m15_candles(count, *, close=100.005, high=100.006, low=99.99, open_price=100.005, time_value="2026-03-26T14:00:00+00:00"):
    candles = []
    for _ in range(count):
        candles.append(
            {
                "time": time_value,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "vol": 1000.0,
            }
        )
    return candles


def _timed_candles(count, tf_minutes, *, start=None, close=100.005, high=100.006, low=99.99, open_price=100.005):
    start = start or datetime(2026, 3, 26, 0, 0, tzinfo=timezone.utc)
    return [
        {
            "time": (start + timedelta(minutes=i * tf_minutes)).isoformat(),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "vol": 1000.0,
        }
        for i in range(count)
    ]


def test_backtest_pair_scalp_uses_stable_m15_execution_proxy(monkeypatch):
    candles_m15 = _timed_candles(140, 15)
    candles_m5 = _timed_candles(420, 5)
    candles_m1 = _timed_candles(2100, 1)
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "SCALP_ENGINE",
        {
            **backtest_runner.CONFIG.get("SCALP_ENGINE", {}),
            "BT_ENABLED": True,
            "BT_SESSION_MODE": "all",
            "BT_SCRATCH_ENABLED": True,
            "BT_SCRATCH_BARS": 3,
            "BT_SCRATCH_MIN_R": 0.10,
            "BT_WALK_BARS": 6,
            "MIN_RR": 1.2,
            "BIAS_TIMEFRAME": "M15",
            "MIN_GRADE_AUTO_EXECUTE": "C",
            "VP_PROXIMITY_USE_ATR": False,
        },
    )
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")

    def _fetch(_symbol, tf, *_args, **_kwargs):
        return {"M15": candles_m15, "M5": candles_m5, "M1": candles_m1}[tf]

    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", _fetch)
    monkeypatch.setattr(backtest_runner, "calc_atr", lambda highs, lows, closes, period: [1.0] * len(closes))
    monkeypatch.setattr(
        indicators,
        "detect_absorption",
        lambda candles, vol_mult, max_move_atr, sma_period: [{"absorbed": False, "direction": "neutral"} for _ in candles],
    )
    monkeypatch.setattr(
        indicators,
        "calc_cvd",
        lambda candles, smooth_period=5: {"smoothed_delta": list(range(len(candles))), "cvd": list(range(len(candles)))},
    )
    monkeypatch.setattr(
        volume_profile,
        "compute_fixed_range_volume_profile",
        lambda candles, bins=64, value_area_pct=0.70: {
            "profile_valid": True,
            "poc": 101.0,
            "vah": 102.0,
            "val": 100.0,
            "session_high": 102.0,
            "session_low": 99.0,
        },
    )
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type, same_bar_both_hit, validation_mode: {
            "totalTrades": len(trades),
            "trades": trades,
            "wfSplit": {},
        },
    )
    monkeypatch.setattr(backtest_runner, "_rt", lambda: types.SimpleNamespace(AUDIT_DB=":memory:"))

    result = backtest_runner.backtest_pair_scalp({"display": "EUR/USD", "type": "forex"})

    assert result["execution_tf"] == "M15"
    assert result["backtest_model"] == "M15_STABLE_PROXY"
    trade = result["trades"][0]
    assert trade["execution_tf"] == "M15"
    assert trade["exit_reason"] == "SCRATCH_NO_FOLLOW_THROUGH"
    assert trade["exit_bar_index"] - trade["entry_bar_index"] == 3


def test_backtest_pair_scalp_crypto_tuple_fetch_drops_forming_bar(monkeypatch):
    candles = _timed_candles(100, 15)
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "SCALP_ENGINE",
        {
            **backtest_runner.CONFIG.get("SCALP_ENGINE", {}),
            "BT_ENABLED": True,
            "BT_SESSION_MODE": "all",
        },
    )
    monkeypatch.setattr(
        scalp_engine,
        "_scalp_fetch_candles",
        lambda pair, tf, limit: (candles, "binance_ws"),
    )

    result = backtest_runner.backtest_pair_scalp({"display": "BTC/USDT", "type": "crypto"})

    assert "Insufficient M15 data" in result["error"]
    assert "99 bars" in result["error"]


def test_backtest_pair_scalp_uses_asset_aware_cost_assumptions(monkeypatch):
    candles = _timed_candles(140, 15)
    calls = []
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "SCALP_ENGINE",
        {
            **backtest_runner.CONFIG.get("SCALP_ENGINE", {}),
            "BT_ENABLED": True,
            "BT_SESSION_MODE": "all",
            "BT_SCRATCH_ENABLED": True,
            "BT_SCRATCH_BARS": 3,
            "BT_SCRATCH_MIN_R": 0.10,
            "BT_WALK_BARS": 6,
            "BT_SLIPPAGE_TICKS": 0,
            "MIN_RR": 1.2,
            "BIAS_TIMEFRAME": "M15",
            "MIN_GRADE_AUTO_EXECUTE": "C",
            "VP_PROXIMITY_USE_ATR": False,
        },
    )
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "XAUUSD")
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: candles)
    monkeypatch.setattr(backtest_runner, "calc_atr", lambda highs, lows, closes, period: [1.0] * len(closes))
    monkeypatch.setattr(
        indicators,
        "detect_absorption",
        lambda candles, vol_mult, max_move_atr, sma_period: [{"absorbed": False, "direction": "neutral"} for _ in candles],
    )
    monkeypatch.setattr(
        indicators,
        "calc_cvd",
        lambda candles, smooth_period=5: {"smoothed_delta": list(range(len(candles))), "cvd": list(range(len(candles)))},
    )
    monkeypatch.setattr(
        volume_profile,
        "compute_fixed_range_volume_profile",
        lambda candles, bins=64, value_area_pct=0.70: {
            "profile_valid": True,
            "poc": 101.0,
            "vah": 102.0,
            "val": 100.0,
            "session_high": 102.0,
            "session_low": 99.0,
        },
    )

    def cost_assumptions(cfg, asset_type):
        calls.append(asset_type)
        return 0.002, 0.001

    monkeypatch.setattr(scalp_engine, "_scalp_cost_assumptions", cost_assumptions)
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type, same_bar_both_hit, validation_mode: {
            "totalTrades": len(trades),
            "trades": trades,
            "wfSplit": {},
        },
    )
    monkeypatch.setattr(backtest_runner, "_rt", lambda: types.SimpleNamespace(AUDIT_DB=":memory:"))

    result = backtest_runner.backtest_pair_scalp({"display": "XAU/USD", "type": "commodity"})

    assert calls
    assert set(calls) == {"commodity"}
    trade = result["trades"][0]
    risk_pct = abs(trade["entry"] - trade["sl"]) / trade["entry"]
    assert trade["fee_R"] == round(0.002 / risk_pct, 4)
    assert trade["slippage_R"] == round(0.001 / risk_pct, 4)


def test_backtest_pair_scalp_uses_h1_bias_context_when_configured(monkeypatch):
    candles_m15 = _timed_candles(840, 15)
    captured = []
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "SCALP_ENGINE",
        {
            **backtest_runner.CONFIG.get("SCALP_ENGINE", {}),
            "BT_ENABLED": True,
            "BT_SESSION_MODE": "all",
            "BT_SCRATCH_ENABLED": True,
            "BT_SCRATCH_BARS": 1,
            "BT_WALK_BARS": 2,
            "MIN_RR": 1.0,
            "BIAS_TIMEFRAME": "H1",
            "VP_PROXIMITY_USE_ATR": False,
        },
    )
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: candles_m15)
    monkeypatch.setattr(backtest_runner, "calc_atr", lambda highs, lows, closes, period: [1.0] * len(closes))
    monkeypatch.setattr(
        indicators,
        "detect_absorption",
        lambda candles, vol_mult, max_move_atr, sma_period: [{"absorbed": True, "direction": "bullish"} for _ in candles],
    )
    monkeypatch.setattr(
        indicators,
        "calc_cvd",
        lambda candles, smooth_period=5: {"smoothed_delta": list(range(len(candles))), "cvd": list(range(len(candles)))},
    )
    monkeypatch.setattr(
        volume_profile,
        "compute_fixed_range_volume_profile",
        lambda candles, bins=64, value_area_pct=0.70: {
            "profile_valid": True,
            "poc": 101.0,
            "vah": 102.0,
            "val": 100.0,
            "session_high": 102.0,
            "session_low": 99.0,
            "volume_source": "candle_volume",
        },
    )
    monkeypatch.setattr(scalp_engine, "infer_bias_from_ema_stack", lambda candles: captured.append(candles) or "LONG")
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type, same_bar_both_hit, validation_mode: {
            "totalTrades": len(trades),
            "trades": trades,
            "wfSplit": {},
        },
    )
    monkeypatch.setattr(backtest_runner, "_rt", lambda: types.SimpleNamespace(AUDIT_DB=":memory:"))

    result = backtest_runner.backtest_pair_scalp({"display": "EUR/USD", "type": "forex"})

    assert result["trades"]
    assert captured
    assert len(captured[0]) == 200
    assert captured[0][1]["_close_dt"] - captured[0][0]["_close_dt"] == timedelta(hours=1)


def test_backtest_pair_scalp_scratches_when_no_follow_through(monkeypatch):
    candles = _m15_candles(120)
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "SCALP_ENGINE",
        {
            **backtest_runner.CONFIG.get("SCALP_ENGINE", {}),
            "BT_ENABLED": True,
            "BT_SESSION_MODE": "new_york",
            "BT_NY_OPEN_SKIP_MINUTES": 30,
            "BT_SCRATCH_ENABLED": True,
            "BT_SCRATCH_BARS": 3,
            "BT_SCRATCH_MIN_R": 0.10,
            "BT_WALK_BARS": 6,
            "MIN_RR": 1.2,
            "BIAS_TIMEFRAME": "M15",
            "MIN_GRADE_AUTO_EXECUTE": "C",
            "VP_PROXIMITY_USE_ATR": False,
        },
    )
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: candles)
    monkeypatch.setattr(backtest_runner, "calc_atr", lambda highs, lows, closes, period: [1.0] * len(closes))
    monkeypatch.setattr(
        indicators,
        "detect_absorption",
        lambda candles, vol_mult, max_move_atr, sma_period: [{"absorbed": False, "direction": "neutral"} for _ in candles],
    )
    monkeypatch.setattr(
        indicators,
        "calc_cvd",
        lambda candles, smooth_period=5: {"smoothed_delta": list(range(len(candles)))},
    )
    monkeypatch.setattr(
        volume_profile,
        "compute_fixed_range_volume_profile",
        lambda candles, bins=64, value_area_pct=0.70: {
            "profile_valid": True,
            "poc": 101.0,
            "vah": 102.0,
            "val": 100.0,
        },
    )
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type, same_bar_both_hit, validation_mode: {
            "totalTrades": len(trades),
            "trades": trades,
            "wfSplit": {},
        },
    )
    monkeypatch.setattr(backtest_runner, "_rt", lambda: types.SimpleNamespace(AUDIT_DB=":memory:"))

    result = backtest_runner.backtest_pair_scalp({"display": "EUR/USD", "type": "forex"})

    assert result["trades"], "Expected at least one scalp trade in the synthetic fixture"
    assert all(t["exit_reason"] == "SCRATCH_NO_FOLLOW_THROUGH" for t in result["trades"])


@pytest.mark.parametrize("scratch_bars, expected_w", [(2, 2), (3, 3)])
def test_backtest_pair_scalp_scratch_clock_2_vs_3_bars(monkeypatch, scratch_bars, expected_w):
    candles = _m15_candles(120)
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "SCALP_ENGINE",
        {
            **backtest_runner.CONFIG.get("SCALP_ENGINE", {}),
            "BT_ENABLED": True,
            "BT_SESSION_MODE": "new_york",
            "BT_NY_OPEN_SKIP_MINUTES": 30,
            "BT_SCRATCH_ENABLED": True,
            "BT_SCRATCH_BARS": scratch_bars,
            "BT_SCRATCH_MIN_R": 0.10,
            "BT_WALK_BARS": 6,
            "MIN_RR": 1.2,
            "BIAS_TIMEFRAME": "M15",
            "MIN_GRADE_AUTO_EXECUTE": "C",
            "VP_PROXIMITY_USE_ATR": False,
        },
    )
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: candles)
    monkeypatch.setattr(backtest_runner, "calc_atr", lambda highs, lows, closes, period: [1.0] * len(closes))
    monkeypatch.setattr(
        indicators,
        "detect_absorption",
        lambda candles, vol_mult, max_move_atr, sma_period: [{"absorbed": False, "direction": "neutral"} for _ in candles],
    )
    monkeypatch.setattr(
        indicators,
        "calc_cvd",
        lambda candles, smooth_period=5: {"smoothed_delta": list(range(len(candles)))},
    )
    monkeypatch.setattr(
        volume_profile,
        "compute_fixed_range_volume_profile",
        lambda candles, bins=64, value_area_pct=0.70: {
            "profile_valid": True,
            "poc": 101.0,
            "vah": 102.0,
            "val": 100.0,
        },
    )
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type, same_bar_both_hit, validation_mode: {
            "totalTrades": len(trades),
            "trades": trades,
            "wfSplit": {},
        },
    )
    monkeypatch.setattr(backtest_runner, "_rt", lambda: types.SimpleNamespace(AUDIT_DB=":memory:"))

    result = backtest_runner.backtest_pair_scalp({"display": "EUR/USD", "type": "forex"})
    assert result["trades"], "Expected at least one scalp trade in the synthetic fixture"
    t0 = result["trades"][0]
    assert t0["exit_reason"] == "SCRATCH_NO_FOLLOW_THROUGH"
    assert (t0["exit_bar_index"] - t0["entry_bar_index"]) == expected_w


def test_backtest_pair_scalp_respects_ny_open_cooldown(monkeypatch):
    candles = _m15_candles(120, time_value="2026-03-26T13:40:00+00:00")  # 09:40 NY (EDT)
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "SCALP_ENGINE",
        {
            **backtest_runner.CONFIG.get("SCALP_ENGINE", {}),
            "BT_ENABLED": True,
            "BT_SESSION_MODE": "new_york",
            "BT_NY_OPEN_SKIP_MINUTES": 30,
            "BT_SCRATCH_ENABLED": True,
        },
    )
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: candles)
    monkeypatch.setattr(backtest_runner, "calc_atr", lambda highs, lows, closes, period: [1.0] * len(closes))
    monkeypatch.setattr(
        indicators,
        "detect_absorption",
        lambda candles, vol_mult, max_move_atr, sma_period: [{"absorbed": False, "direction": "neutral"} for _ in candles],
    )
    monkeypatch.setattr(
        indicators,
        "calc_cvd",
        lambda candles, smooth_period=5: {"smoothed_delta": list(range(len(candles)))},
    )
    monkeypatch.setattr(
        volume_profile,
        "compute_fixed_range_volume_profile",
        lambda candles, bins=64, value_area_pct=0.70: {
            "profile_valid": True,
            "poc": 101.0,
            "vah": 102.0,
            "val": 100.0,
        },
    )

    result = backtest_runner.backtest_pair_scalp({"display": "EUR/USD", "type": "forex"})

    assert "No scalp setups found" in result["error"]
