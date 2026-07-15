"""Live Bybit kline TTL cache: duplicate live fetches collapse to one REST call;
point-in-time fetches (end_ms) always hit the API; confirmed flags recomputed on hit."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import data_feeds
from data_feeds import _fetch_bybit_klines, clear_bybit_kline_cache


def _make_response(rows):
    class _Response:
        status_code = 200

        def json(self):
            return {"retCode": 0, "result": {"list": rows}}

    return _Response()


def _install_counting_get(monkeypatch, rows):
    calls = {"n": 0}

    def _fake_get(_url, params=None, timeout=None):  # noqa: ARG001
        calls["n"] += 1
        return _make_response(rows)

    monkeypatch.setattr("data_feeds.http_requests.get", _fake_get)
    return calls


def test_live_fetch_served_from_cache_within_ttl(monkeypatch):
    clear_bybit_kline_cache()
    old_open = str(1700000000000)  # long-closed bar → confirmed on hit too
    calls = _install_counting_get(
        monkeypatch, [[old_open, "1", "2", "0.5", "1.5", "100", "150"]]
    )

    first = _fetch_bybit_klines("BTCUSDT", "H4", 120)
    second = _fetch_bybit_klines("BTCUSDT", "H4", 120)

    assert calls["n"] == 1
    assert first and second
    assert second[0]["close"] == 1.5
    assert second[0]["confirmed"] is True
    # Cache hit returns private copies — caller mutation must not taint cache.
    second[0]["close"] = 999.0
    third = _fetch_bybit_klines("BTCUSDT", "H4", 120)
    assert third[0]["close"] == 1.5
    clear_bybit_kline_cache()


def test_point_in_time_fetch_never_cached(monkeypatch):
    clear_bybit_kline_cache()
    calls = _install_counting_get(
        monkeypatch, [["1700000000000", "1", "2", "0.5", "1.5", "100", "150"]]
    )

    _fetch_bybit_klines("BTCUSDT", "H4", 120, end_ms=1700003600000)
    _fetch_bybit_klines("BTCUSDT", "H4", 120, end_ms=1700003600000)

    assert calls["n"] == 2
    # A point-in-time fetch must not have populated the live cache either.
    _fetch_bybit_klines("BTCUSDT", "H4", 120)
    assert calls["n"] == 3
    clear_bybit_kline_cache()


def test_cache_keys_isolate_symbol_tf_and_limit(monkeypatch):
    clear_bybit_kline_cache()
    calls = _install_counting_get(
        monkeypatch, [["1700000000000", "1", "2", "0.5", "1.5", "100", "150"]]
    )

    _fetch_bybit_klines("BTCUSDT", "H4", 120)
    _fetch_bybit_klines("ETHUSDT", "H4", 120)  # other symbol → fetch
    _fetch_bybit_klines("BTCUSDT", "H1", 120)  # other tf → fetch
    _fetch_bybit_klines("BTCUSDT", "H4", 200)  # other limit → fetch
    _fetch_bybit_klines("BTC/USDT", "H4", 120)  # normalizes to same key → hit

    assert calls["n"] == 4
    clear_bybit_kline_cache()


def test_expired_entry_refetches(monkeypatch):
    clear_bybit_kline_cache()
    calls = _install_counting_get(
        monkeypatch, [["1700000000000", "1", "2", "0.5", "1.5", "100", "150"]]
    )

    _fetch_bybit_klines("BTCUSDT", "H4", 120)
    # Force expiry instead of sleeping through the real TTL.
    key = ("BTCUSDT", "H4", 120)
    candles, _expiry = data_feeds._bybit_kline_cache[key]
    data_feeds._bybit_kline_cache[key] = (candles, time.time() - 1.0)

    _fetch_bybit_klines("BTCUSDT", "H4", 120)
    assert calls["n"] == 2
    clear_bybit_kline_cache()


def test_rate_limit_retcode_retries_once_then_fails_closed(monkeypatch):
    class _Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    success = {
        "retCode": 0,
        "result": {
            "list": [["1700000000000", "1", "2", "0.5", "1.5", "100", "150"]]
        },
    }
    rate_limited = {"retCode": 10006, "retMsg": "Too many visits"}
    responses = iter([_Response(rate_limited), _Response(success)])
    sleeps = []
    monkeypatch.setattr(data_feeds.http_requests, "get", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(data_feeds.time, "sleep", lambda seconds: sleeps.append(seconds))

    clear_bybit_kline_cache()
    candles = _fetch_bybit_klines("BTCUSDT", "D1", 1000)
    assert candles and candles[0]["close"] == 1.5
    assert sleeps == [data_feeds._BYBIT_KLINE_RATE_LIMIT_BACKOFF_SEC]

    calls = {"n": 0}

    def _always_limited(*_args, **_kwargs):
        calls["n"] += 1
        return _Response(rate_limited)

    clear_bybit_kline_cache()
    sleeps.clear()
    monkeypatch.setattr(data_feeds.http_requests, "get", _always_limited)
    assert _fetch_bybit_klines("BTCUSDT", "D1", 1000) is None
    assert calls["n"] == 2
    assert sleeps == [data_feeds._BYBIT_KLINE_RATE_LIMIT_BACKOFF_SEC]
    clear_bybit_kline_cache()
