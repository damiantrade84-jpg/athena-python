"""Tests for timeframe normalization in candles_cache.fetch_candles()."""

import os
import sys
import threading
import time
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from candles_cache import (
    _candle_cache,
    _candle_cache_lock,
    _candle_fetch_inflight,
    _candle_fetch_meta,
    fetch_candles,
    get_candle_fetch_meta,
)


def _noop_fetch(*_args, **_kwargs):
    return None


class TestCandleCacheKeys:
    def setup_method(self):
        with _candle_cache_lock:
            _candle_cache.clear()
            _candle_fetch_meta.clear()
            _candle_fetch_inflight.clear()

    def teardown_method(self):
        with _candle_cache_lock:
            _candle_cache.clear()
            _candle_fetch_meta.clear()
            _candle_fetch_inflight.clear()

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
        assert fetch_mt5.call_count == 2

    def test_mt5_direct_fetch_never_writes_ttl_cache(self):
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

        assert ("EURUSD", "H1", 100) not in keys
        assert ("EURUSD", "h1", 100) not in keys

    def test_mt5_bypasses_existing_ttl_cache_entry(self):
        pair = {"symbol": "EURUSD", "display": "EUR/USD", "source": "mt5", "type": "forex"}
        stale = [{"time": "2026-03-27T13:00:00+00:00", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "vol": 900}]
        fresh = [{"time": "2026-03-27T14:00:00+00:00", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 1000}]
        fetch_mt5 = Mock(return_value=fresh)

        with _candle_cache_lock:
            _candle_cache[("EURUSD", "H1", 100)] = (stale, time.time() + 3600)

        candles = fetch_candles(
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
        meta = get_candle_fetch_meta(pair, "H1", 100)

        assert candles == fresh
        assert fetch_mt5.call_count == 1
        assert meta["upstream"] == "mt5"
        assert meta["cacheHit"] is False
        with _candle_cache_lock:
            assert ("EURUSD", "H1", 100) not in _candle_cache

    def test_forex_eodhd_source_bypasses_existing_ttl_cache_entry(self):
        pair = {"symbol": "EURUSD.FOREX", "display": "EUR/USD", "source": "eodhd", "type": "forex"}
        stale = [{"time": "2026-03-27T13:00:00+00:00", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "vol": 900}]
        fresh = [{"time": "2026-03-27T14:00:00+00:00", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 1000}]
        fetch_eodhd = Mock(return_value=fresh)

        with _candle_cache_lock:
            _candle_cache[("EURUSD.FOREX", "H1", 100)] = (stale, time.time() + 3600)

        candles = fetch_candles(
            pair,
            "H1",
            100,
            fetch_candles_live=_noop_fetch,
            fetch_binance=_noop_fetch,
            fetch_eodhd=fetch_eodhd,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=None,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )
        meta = get_candle_fetch_meta(pair, "H1", 100)

        assert candles == fresh
        assert fetch_eodhd.call_count == 1
        assert meta["cacheBypass"] is True
        assert meta["cacheHit"] is False
        with _candle_cache_lock:
            assert ("EURUSD.FOREX", "H1", 100) not in _candle_cache

    def test_non_forex_crypto_can_use_existing_ttl_cache_entry(self):
        pair = {"symbol": "AAPL.US", "display": "AAPL", "source": "eodhd", "type": "stock"}
        cached = [{"time": "2026-03-27T13:00:00+00:00", "open": 200.0, "high": 201.0, "low": 199.0, "close": 200.5, "vol": 900}]
        fetch_eodhd = Mock(return_value=[{"time": "2026-03-27T14:00:00+00:00", "open": 201.0, "high": 202.0, "low": 200.0, "close": 201.5, "vol": 1000}])

        with _candle_cache_lock:
            _candle_cache[("AAPL.US", "H1", 100)] = (cached, time.time() + 3600)
            _candle_fetch_meta[("AAPL.US", "H1", 100)] = {"upstream": "eodhd"}

        candles = fetch_candles(
            pair,
            "H1",
            100,
            fetch_candles_live=_noop_fetch,
            fetch_binance=_noop_fetch,
            fetch_eodhd=fetch_eodhd,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=None,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )
        meta = get_candle_fetch_meta(pair, "H1", 100)

        assert candles == cached
        assert fetch_eodhd.call_count == 0
        assert meta["resolution"] == "ttl_cache"
        assert meta["cacheHit"] is True

    def test_crypto_h4_merges_live_candlebuilder_bar_over_rest_fallback(self):
        pair = {
            "symbol": "BTCUSDT",
            "display": "BTC/USDT",
            "source": "binance",
            "type": "crypto",
        }
        current_h4 = int(time.time() // (4 * 3600)) * (4 * 3600)
        prev_h4 = current_h4 - (4 * 3600)
        older_h4 = current_h4 - (8 * 3600)
        live_h4 = [
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(prev_h4)),
                "open": 87000,
                "high": 87500,
                "low": 86800,
                "close": 87250,
                "vol": 100,
            },
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(current_h4)),
                "open": 87250,
                "high": 87950,
                "low": 87150,
                "close": 87880,
                "vol": 120,
            },
        ]
        rest_h4 = [
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(older_h4)),
                "open": 86500,
                "high": 87100,
                "low": 86300,
                "close": 87000,
                "vol": 90,
            },
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(prev_h4)),
                "open": 87000,
                "high": 87400,
                "low": 86750,
                "close": 87180,
                "vol": 95,
            },
        ]
        fetch_binance = Mock(return_value=rest_h4)

        candles = fetch_candles(
            pair,
            "H4",
            10,
            fetch_candles_live=Mock(return_value={"candles": live_h4}),
            fetch_binance=fetch_binance,
            fetch_eodhd=_noop_fetch,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=None,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )

        assert fetch_binance.call_count == 1
        assert [c["time"] for c in candles] == [
            time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(older_h4)),
            time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(prev_h4)),
            time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(current_h4)),
        ]
        assert candles[-2]["close"] == 87250
        assert candles[-1]["close"] == 87880

    def test_crypto_h4_large_limit_does_not_short_circuit_on_partial_live_history(self):
        pair = {
            "symbol": "BTCUSDT",
            "display": "BTC/USDT",
            "source": "binance",
            "type": "crypto",
        }
        live_h4 = []
        current_h4 = int(time.time() // (4 * 3600)) * (4 * 3600)
        base_ts = current_h4 - (56 * 4 * 3600)
        for idx in range(57):
            ts = base_ts + (idx * 4 * 3600)
            live_h4.append(
                {
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts)),
                    "open": 87000 + idx,
                    "high": 87100 + idx,
                    "low": 86900 + idx,
                    "close": 87050 + idx,
                    "vol": 100 + idx,
                }
            )
        rest_h4 = [
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base_ts - (12 * 3600))),
                "open": 86880,
                "high": 86950,
                "low": 86790,
                "close": 86920,
                "vol": 88,
            },
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base_ts - (8 * 3600))),
                "open": 86920,
                "high": 87010,
                "low": 86820,
                "close": 86980,
                "vol": 91,
            },
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base_ts - (4 * 3600))),
                "open": 86980,
                "high": 87040,
                "low": 86870,
                "close": 87010,
                "vol": 95,
            },
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base_ts)),
                "open": 87010,
                "high": 87110,
                "low": 86950,
                "close": 87030,
                "vol": 97,
            },
        ]
        fetch_binance = Mock(return_value=rest_h4)

        candles = fetch_candles(
            pair,
            "H4",
            1000,
            fetch_candles_live=Mock(return_value={"candles": live_h4}),
            fetch_binance=fetch_binance,
            fetch_eodhd=_noop_fetch,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=None,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )
        meta = get_candle_fetch_meta(pair, "H4", 1000)

        assert fetch_binance.call_count == 1
        assert len(candles) == 60
        assert candles[0]["time"] == time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base_ts - (12 * 3600)))
        assert candles[3]["time"] == time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base_ts))
        assert candles[3]["close"] == 87050
        assert meta["upstream"] == "binance_futures"
        assert meta["liveMerge"] is True

    def test_crypto_live_tf_ignores_cache_when_live_builder_is_stale(self):
        pair = {
            "symbol": "BTCUSDT",
            "display": "BTC/USDT",
            "source": "binance",
            "type": "crypto",
        }
        current_bucket = int(time.time() // 3600) * 3600
        stale_ts = current_bucket - 3600
        current_ts = current_bucket
        stale_live = [
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(stale_ts)),
                "open": 87000,
                "high": 87100,
                "low": 86900,
                "close": 87050,
                "vol": 100,
            }
        ]
        cached = [
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(stale_ts)),
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "vol": 1,
            }
        ]
        fresh_rest = [
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(current_ts)),
                "open": 87200,
                "high": 87300,
                "low": 87100,
                "close": 87250,
                "vol": 120,
            }
        ]
        fetch_binance = Mock(return_value=fresh_rest)

        with _candle_cache_lock:
            _candle_cache[("BTCUSDT", "H1", 100)] = (cached, time.time() + 3600)

        candles = fetch_candles(
            pair,
            "H1",
            100,
            fetch_candles_live=Mock(return_value={"candles": stale_live}),
            fetch_binance=fetch_binance,
            fetch_eodhd=_noop_fetch,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=None,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )
        meta = get_candle_fetch_meta(pair, "H1", 100)

        assert candles == fresh_rest
        assert fetch_binance.call_count == 1
        assert meta["upstream"] == "binance_futures"
        assert meta["liveStale"] is True
        assert meta["cacheBypass"] is True
        assert meta["cacheWriteSkipped"] is True
        with _candle_cache_lock:
            assert ("BTCUSDT", "H1", 100) not in _candle_cache

    def test_crypto_live_tf_bypasses_cache_even_when_live_builder_is_current(self):
        pair = {
            "symbol": "BTCUSDT",
            "display": "BTC/USDT",
            "source": "binance",
            "type": "crypto",
        }
        current_bucket = int(time.time() // 3600) * 3600
        current_time = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(current_bucket))
        cached = [{"time": current_time, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}]
        live = [{"time": current_time, "open": 2, "high": 2, "low": 2, "close": 2, "vol": 2}]
        fresh_rest = [{"time": current_time, "open": 3, "high": 3, "low": 3, "close": 3, "vol": 3}]
        fetch_binance = Mock(return_value=fresh_rest)

        with _candle_cache_lock:
            _candle_cache[("BTCUSDT", "H1", 100)] = (cached, time.time() + 3600)

        candles = fetch_candles(
            pair,
            "H1",
            100,
            fetch_candles_live=Mock(return_value={"candles": live}),
            fetch_binance=fetch_binance,
            fetch_eodhd=_noop_fetch,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=None,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
        )
        meta = get_candle_fetch_meta(pair, "H1", 100)

        assert fetch_binance.call_count == 1
        assert candles[-1]["close"] == 2
        assert meta["cacheBypass"] is True
        assert meta["cacheHit"] is False
        with _candle_cache_lock:
            assert ("BTCUSDT", "H1", 100) not in _candle_cache

    def test_binance_large_limit_uses_paginated_fetch(self):
        pair = {
            "symbol": "BTCUSDT",
            "display": "BTC/USDT",
            "source": "binance",
            "type": "crypto",
        }
        paginated = Mock(
            return_value=[
                {
                    "time": "2026-03-27T00:00:00+00:00",
                    "open": 87000,
                    "high": 87500,
                    "low": 86800,
                    "close": 87250,
                    "vol": 100,
                }
            ]
        )
        direct = Mock(return_value=None)

        candles = fetch_candles(
            pair,
            "M15",
            2000,
            fetch_candles_live=_noop_fetch,
            fetch_binance=direct,
            fetch_binance_paginated=paginated,
            fetch_eodhd=_noop_fetch,
            fetch_polygon=_noop_fetch,
            fetch_yfinance=_noop_fetch,
            fetch_mt5=None,
            yfinance_symbol_for_pair=lambda _pair: None,
            tf_b={"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d"},
        )
        meta = get_candle_fetch_meta(pair, "M15", 2000)

        assert candles[0]["close"] == 87250
        assert paginated.call_count == 1
        assert direct.call_count == 0
        assert meta["pagination"] is True

    def test_fetch_meta_tracks_mt5_direct_fetches(self):
        pair = {"symbol": "EURUSD", "display": "EUR/USD", "source": "mt5", "type": "forex"}
        candles = [{"time": "2026-03-27T14:00:00+00:00", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 1000}]
        fetch_mt5 = Mock(return_value=candles)

        fetch_candles(
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
        first_meta = get_candle_fetch_meta(pair, "H1", 100)

        fetch_candles(
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
        cached_meta = get_candle_fetch_meta(pair, "H1", 100)

        assert first_meta["upstream"] == "mt5"
        assert first_meta["cacheHit"] is False
        assert cached_meta["resolution"] == "rest"
        assert cached_meta["upstream"] == "mt5"
        assert cached_meta["cacheHit"] is False
        assert fetch_mt5.call_count == 2

    def test_single_flight_dedupes_parallel_fetches(self):
        pair = {"symbol": "AAPL.US", "display": "AAPL", "source": "eodhd", "type": "stock"}
        candles = [{"time": "2026-03-27T14:00:00+00:00", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 1000}]
        call_count = 0
        call_lock = threading.Lock()

        def _slow_eodhd(*_args, **_kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
            time.sleep(0.05)
            return candles

        results = []

        def _worker():
            results.append(
                fetch_candles(
                    pair,
                    "H1",
                    100,
                    fetch_candles_live=_noop_fetch,
                    fetch_binance=_noop_fetch,
                    fetch_eodhd=_slow_eodhd,
                    fetch_polygon=_noop_fetch,
                    fetch_yfinance=_noop_fetch,
                    fetch_mt5=None,
                    yfinance_symbol_for_pair=lambda _pair: None,
                    tf_b={"H1": "1h", "H4": "4h", "D1": "1d"},
                )
            )

        threads = [threading.Thread(target=_worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert len(results) == 2
        assert results[0] == candles
        assert results[1] == candles
        assert call_count == 1
