"""Tests for timeframe normalization in candles_cache.fetch_candles()."""

import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from candles_cache import _candle_cache, _candle_cache_lock, fetch_candles


def _noop_fetch(*_args, **_kwargs):
    return None


class TestCandleCacheKeys:
    def setup_method(self):
        with _candle_cache_lock:
            _candle_cache.clear()

    def teardown_method(self):
        with _candle_cache_lock:
            _candle_cache.clear()

    def test_lowercase_h1_hits_same_cache_entry_as_uppercase_h1(self):
        pair = {"symbol": "EURUSD", "display": "EUR/USD", "source": "mt5", "type": "forex"}
        candles = [{"time": "2026-03-27T14:00:00+00:00", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 1000}]
        fetch_mt5 = Mock(return_value=candles)

        lower = fetch_candles(
            pair,
            "h1",
            100,
            fetch_candles_live=_noop_fetch,
            fetch_binance=_noop_fetch,
            fetch_eodhd=_noop_fetch,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=fetch_mt5,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )
        upper = fetch_candles(
            pair,
            "H1",
            100,
            fetch_candles_live=_noop_fetch,
            fetch_binance=_noop_fetch,
            fetch_eodhd=_noop_fetch,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=fetch_mt5,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )

        assert lower == candles
        assert upper == candles
        assert fetch_mt5.call_count == 1

    def test_ttl_cache_never_receives_lowercase_timeframe_key(self):
        pair = {"symbol": "EURUSD", "display": "EUR/USD", "source": "mt5", "type": "forex"}
        candles = [{"time": "2026-03-27T14:00:00+00:00", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 1000}]
        fetch_mt5 = Mock(return_value=candles)

        fetch_candles(
            pair,
            "h1",
            100,
            fetch_candles_live=_noop_fetch,
            fetch_binance=_noop_fetch,
            fetch_eodhd=_noop_fetch,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=fetch_mt5,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )

        with _candle_cache_lock:
            keys = list(_candle_cache.keys())

        assert ("EURUSD", "H1", 100) in keys
        assert ("EURUSD", "h1", 100) not in keys
