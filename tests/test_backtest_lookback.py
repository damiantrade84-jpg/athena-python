from __future__ import annotations

import math

import backtest_runner as br


def test_backtest_candle_limits_365_days():
    limits = br._backtest_candle_limits(365)
    assert limits["days"] == 365
    assert limits["d1"] == 385
    assert limits["h4"] == 2240
    assert limits["h1"] == 8810


def test_backtest_candle_limits_floor_at_120_days():
    limits = br._backtest_candle_limits(50)
    assert limits["days"] == 120
    assert limits["d1"] == 140
    assert limits["h4"] == 770
    assert limits["h1"] == 2930


def test_backtest_candle_limits_ceiling_at_730_days():
    limits = br._backtest_candle_limits(1000)
    assert limits["days"] == 730
    assert limits["d1"] == 750
    assert limits["h4"] == 4430
    assert limits["h1"] == 17570


def test_backtest_candle_limits_reads_config_default(monkeypatch):
    monkeypatch.setitem(br.CONFIG, "BACKTEST_LOOKBACK_DAYS", 180)
    limits = br._backtest_candle_limits()
    assert limits["days"] == 180
    assert limits["d1"] == 200
    assert limits["h4"] == math.ceil(180 * 6) + 50
    assert limits["h1"] == math.ceil(180 * 24) + 50


def test_backtest_candle_limits_cash_session_raises_d1_to_span_h4():
    # Single stocks / equity-bond ETFs print ~2-3 H4 bars per cash-session day, so
    # the H4 cap spans ~h4/2 trading days. The D1 cap must cover at least that span
    # (+60-bar warmup) or the v3 evaluator drops old H4 bars as d1_candles_missing.
    for asset_type in ("stock", "etf", "etf_bond", "STOCK"):
        limits = br._backtest_candle_limits(365, asset_type=asset_type)
        assert limits["d1"] == 1180  # max(385, ceil(2240/2)+60)
        assert limits["d1"] >= limits["h4"] // 2  # D1 spans the H4 window
        assert limits["h4"] == 2240  # intraday caps unchanged
        assert limits["h1"] == 8810


def test_backtest_candle_limits_24h_markets_unchanged():
    # 24h markets (forex/crypto/index/commodity) already align; D1 cap untouched.
    for asset_type in ("forex", "crypto", "index", "commodity", None):
        limits = br._backtest_candle_limits(365, asset_type=asset_type)
        assert limits["d1"] == 385
