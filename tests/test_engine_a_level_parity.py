"""Regression tests for Engine A level ATR parity paths."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest_runner
from config import CONFIG
from data_feeds import _fetch_bybit_klines


def test_engine_a_bt_bybit_atr_uses_backtest_bar_time(monkeypatch):
    captured = {}

    def _bybit_atr_for_levels(pair, style, as_of=None):
        captured["pair"] = pair
        captured["style"] = style
        captured["as_of"] = as_of
        return 12.5

    monkeypatch.setattr(
        backtest_runner,
        "_rt",
        lambda: SimpleNamespace(bybit_atr_for_levels=_bybit_atr_for_levels),
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LEVELS_FEED", "bybit")
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)

    atr, source = backtest_runner._engine_a_level_atr_for_bt(
        3.0,
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
        "intraday",
        as_of="2025-01-02T04:00:00+00:00",
    )

    assert atr == 12.5
    assert source == "bybit"
    assert captured["as_of"] == "2025-01-02T04:00:00+00:00"


def test_engine_a_bt_binance_source_uses_signal_atr_first(monkeypatch):
    def _bybit_atr_for_levels(pair, style, as_of=None):
        return 12.5

    monkeypatch.setattr(
        backtest_runner,
        "_rt",
        lambda: SimpleNamespace(bybit_atr_for_levels=_bybit_atr_for_levels),
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LEVELS_FEED", "bybit")
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_BT_LEVEL_ATR_USE_SIGNAL_FEED", True)

    pair = {
        "display": "BTC/USDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "source": "binance",
    }
    atr, source = backtest_runner._engine_a_level_atr_for_bt(
        99.0, pair, "intraday", as_of="2025-01-02T04:00:00+00:00",
    )
    assert atr == 99.0
    assert source == "signal"


def test_engine_a_bt_binance_signal_missing_fails_closed(monkeypatch):
    monkeypatch.setattr(
        backtest_runner,
        "_rt",
        lambda: SimpleNamespace(bybit_atr_for_levels=lambda *_a, **_k: 5.0),
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LEVELS_FEED", "bybit")
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_BT_LEVEL_ATR_USE_SIGNAL_FEED", True)

    pair = {
        "display": "BTC/USDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "source": "binance",
    }
    atr, source = backtest_runner._engine_a_level_atr_for_bt(
        None, pair, "intraday", as_of="2025-01-02T04:00:00+00:00",
    )
    assert atr is None
    assert source == "signal_unavailable"


def test_engine_a_bt_fails_closed_when_bybit_atr_missing(monkeypatch):
    monkeypatch.setattr(
        backtest_runner,
        "_rt",
        lambda: SimpleNamespace(bybit_atr_for_levels=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LEVELS_FEED", "bybit")
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)

    atr, source = backtest_runner._engine_a_level_atr_for_bt(
        3.0,
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
        "intraday",
        as_of="2025-01-02T04:00:00+00:00",
    )

    assert atr is None
    assert source == "bybit_unavailable"


def test_fetch_bybit_klines_accepts_point_in_time_end(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200

        def json(self):
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        ["1700000000000", "1", "2", "0.5", "1.5", "100", "150"],
                    ]
                },
            }

    def _fake_get(_url, params=None, timeout=None):
        captured["params"] = dict(params or {})
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("data_feeds.http_requests.get", _fake_get)

    candles = _fetch_bybit_klines("BTCUSDT", "H4", 120, end_ms=1700003600000)

    assert captured["params"]["end"] == "1700003600000"
    assert captured["params"]["category"] == "linear"
    assert candles and candles[0]["close"] == 1.5
