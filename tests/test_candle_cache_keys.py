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

    def test_crypto_h4_merges_live_candlebuilder_bar_over_rest_fallback(self):
        pair = {
            "symbol": "BTCUSDT",
            "display": "BTC/USDT",
            "source": "binance",
            "type": "crypto",
        }
        live_h4 = [
            {
                "time": "2026-03-27T00:00:00+00:00",
                "open": 87000,
                "high": 87500,
                "low": 86800,
                "close": 87250,
                "vol": 100,
            },
            {
                "time": "2026-03-27T04:00:00+00:00",
                "open": 87250,
                "high": 87950,
                "low": 87150,
                "close": 87880,
                "vol": 120,
            },
        ]
        rest_h4 = [
            {
                "time": "2026-03-26T20:00:00+00:00",
                "open": 86500,
                "high": 87100,
                "low": 86300,
                "close": 87000,
                "vol": 90,
            },
            {
                "time": "2026-03-27T00:00:00+00:00",
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
            "2026-03-26T20:00:00+00:00",
            "2026-03-27T00:00:00+00:00",
            "2026-03-27T04:00:00+00:00",
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
        base_ts = 1743148800  # 2025-03-28 08:00:00 UTC
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
                "time": "2025-03-27T20:00:00+00:00",
                "open": 86880,
                "high": 86950,
                "low": 86790,
                "close": 86920,
                "vol": 88,
            },
            {
                "time": "2025-03-28T00:00:00+00:00",
                "open": 86920,
                "high": 87010,
                "low": 86820,
                "close": 86980,
                "vol": 91,
            },
            {
                "time": "2025-03-28T04:00:00+00:00",
                "open": 86980,
                "high": 87040,
                "low": 86870,
                "close": 87010,
                "vol": 95,
            },
            {
                "time": "2025-03-28T08:00:00+00:00",
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
        assert candles[0]["time"] == "2025-03-27T20:00:00+00:00"
        assert candles[3]["time"] == "2025-03-28T08:00:00+00:00"
        assert candles[3]["close"] == 87050
        assert meta["upstream"] == "binance_futures"
        assert meta["liveMerge"] is True

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

    def test_fetch_meta_tracks_upstream_and_cache_hits(self):
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
        assert cached_meta["resolution"] == "ttl_cache"
        assert cached_meta["cacheHit"] is True
        assert cached_meta["cacheUpstream"] == "mt5"

    def test_single_flight_dedupes_parallel_fetches(self):
        pair = {"symbol": "EURUSD", "display": "EUR/USD", "source": "mt5", "type": "forex"}
        candles = [{"time": "2026-03-27T14:00:00+00:00", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 1000}]
        call_count = 0
        call_lock = threading.Lock()

        def _slow_mt5(*_args, **_kwargs):
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
                    fetch_eodhd=_noop_fetch,
                    fetch_polygon=_noop_fetch,
                    fetch_yfinance=_noop_fetch,
                    fetch_mt5=_slow_mt5,
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
