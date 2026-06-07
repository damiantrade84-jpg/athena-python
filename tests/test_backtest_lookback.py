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
