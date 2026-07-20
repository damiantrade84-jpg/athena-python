from __future__ import annotations

from athena_app.services.crypto_signal_feed import (
    fetch_crypto_signal_candles,
    resolve_crypto_signal_feed,
)


def _bars(source: str, count: int = 3) -> list[dict]:
    return [
        {
            "time": f"2026-01-0{i + 1}T00:00:00+00:00",
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "vol": 10.0 + i,
            "source_tag": source,
        }
        for i in range(count)
    ]


def test_crypto_signal_feed_defaults_to_bybit_without_touching_bybit_fetcher_shape():
    """B1: with no config, the default feed is bybit (matches config.yaml/config.py).
    Previously defaulted to binance — audit MED #11."""
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"}
    default_calls = []
    bybit_calls = []

    def default_fetch(pair_arg, tf_arg, limit_arg):
        default_calls.append((pair_arg, tf_arg, limit_arg))
        return _bars("binance", limit_arg)

    def bybit_fetch(symbol, tf, limit):
        bybit_calls.append((symbol, tf, limit))
        return _bars("bybit", limit)

    result = fetch_crypto_signal_candles(
        pair,
        "H4",
        3,
        engine="AB",
        config={},
        default_fetch=default_fetch,
        bybit_fetch=bybit_fetch,
    )

    assert [c["source_tag"] for c in result.candles] == ["bybit"] * 3
    assert default_calls == []
    assert bybit_calls == [("BTCUSDT", "H4", 3)]
    assert result.meta["signalFeed"] == "bybit"
    assert result.meta["actualPriceVolume"] is True
    assert result.meta["upstream"] == "bybit_linear_kline"


def test_crypto_signal_feed_uses_bybit_when_configured_and_records_source_truth():
    pair = {"display": "ETH/USDT", "symbol": "ETHUSDT", "type": "crypto", "source": "binance"}
    default_calls = []
    bybit_calls = []

    def default_fetch(*args):
        default_calls.append(args)
        return _bars("binance", 3)

    def bybit_fetch(symbol, tf, limit):
        bybit_calls.append((symbol, tf, limit))
        return _bars("bybit", limit)

    result = fetch_crypto_signal_candles(
        pair,
        "H1",
        3,
        engine="AB",
        config={"ENGINE_AB_CRYPTO_SIGNAL_FEED": "bybit"},
        default_fetch=default_fetch,
        bybit_fetch=bybit_fetch,
    )

    assert [c["source_tag"] for c in result.candles] == ["bybit"] * 3
    assert default_calls == []
    assert bybit_calls == [("ETHUSDT", "H1", 3)]
    assert result.meta["signalFeed"] == "bybit"
    assert result.meta["upstream"] == "bybit_linear_kline"
    assert result.meta["fallback"] is None


def test_crypto_signal_feed_fails_closed_when_bybit_missing_and_fallback_disabled():
    pair = {"display": "SOL/USDT", "symbol": "SOLUSDT", "type": "crypto", "source": "binance"}
    default_calls = []

    result = fetch_crypto_signal_candles(
        pair,
        "D1",
        3,
        engine="AB",
        config={"ENGINE_AB_CRYPTO_SIGNAL_FEED": "bybit"},
        default_fetch=lambda *args: default_calls.append(args) or _bars("binance", 3),
        bybit_fetch=lambda *_args, **_kwargs: None,
    )

    assert result.candles is None
    assert default_calls == []
    assert result.meta["signalFeed"] == "bybit"
    assert result.meta["error"] is True
    assert result.meta["detail"] == "bybit_signal_feed_unavailable"


def test_crypto_signal_feed_can_fallback_to_default_when_explicitly_enabled():
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"}

    result = fetch_crypto_signal_candles(
        pair,
        "H4",
        2,
        engine="AB",
        config={
            "ENGINE_AB_CRYPTO_SIGNAL_FEED": "bybit",
            "ENGINE_AB_CRYPTO_SIGNAL_FEED_FALLBACK": True,
        },
        default_fetch=lambda *_args: _bars("binance", 2),
        bybit_fetch=lambda *_args, **_kwargs: None,
    )

    assert [c["source_tag"] for c in result.candles] == ["binance"] * 2
    assert result.meta["signalFeed"] == "bybit"
    assert result.meta["fallback"] == "binance"
    assert result.meta["upstream"] == "binance_futures"


def test_crypto_signal_feed_uses_paginated_bybit_fetcher_for_large_backtest_requests():
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"}
    paginated_calls = []

    result = fetch_crypto_signal_candles(
        pair,
        "H1",
        1200,
        engine="AB",
        config={"ENGINE_AB_CRYPTO_SIGNAL_FEED": "bybit"},
        default_fetch=lambda *_args: _bars("binance", 1200),
        bybit_fetch=lambda *_args, **_kwargs: _bars("bybit_non_paginated", 1000),
        bybit_paginated_fetch=lambda symbol, tf, limit: paginated_calls.append((symbol, tf, limit))
        or _bars("bybit_paginated", 1200),
    )

    assert result.candles is not None
    assert len(result.candles) == 1200
    assert result.candles[-1]["source_tag"] == "bybit_paginated"
    assert paginated_calls == [("BTCUSDT", "H1", 1200)]
    assert result.meta["paginated"] is True
    assert result.meta["estimatedRequests"] == 2


def test_resolve_crypto_signal_feed_supports_engine_specific_override():
    config = {
        "ENGINE_AB_CRYPTO_SIGNAL_FEED": "binance",
        "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
    }

    assert resolve_crypto_signal_feed("A", config) == "bybit"
    assert resolve_crypto_signal_feed("B", config) == "binance"


def test_resolve_crypto_signal_feed_engine_a_defaults_to_bybit_in_repo_config():
    from config import CONFIG

    assert CONFIG.get("ENGINE_A_CRYPTO_SIGNAL_FEED") == "bybit"
    assert resolve_crypto_signal_feed("A", CONFIG) == "bybit"
    assert resolve_crypto_signal_feed("AB", CONFIG) == "bybit"


def test_bybit_klines_paginated_walks_back_from_earliest_open_time(monkeypatch):
    import data_feeds

    calls = []

    def fake_fetch(symbol, tf, limit, *, end_ms=None):
        calls.append((symbol, tf, limit, end_ms))
        if end_ms is None:
            return [
                {"open_time": 3000, "time": "t3", "close": 3.0, "vol": 30.0},
                {"open_time": 4000, "time": "t4", "close": 4.0, "vol": 40.0},
            ]
        if end_ms == 2999:
            return [
                {"open_time": 1000, "time": "t1", "close": 1.0, "vol": 10.0},
                {"open_time": 2000, "time": "t2", "close": 2.0, "vol": 20.0},
            ]
        return []

    monkeypatch.setattr(data_feeds, "_fetch_bybit_klines", fake_fetch)

    candles = data_feeds._fetch_bybit_klines_paginated("BTCUSDT", "H1", 4)

    assert [c["open_time"] for c in candles] == [1000, 2000, 3000, 4000]
    assert calls == [
        ("BTCUSDT", "H1", 4, None),
        ("BTCUSDT", "H1", 2, 2999),
    ]


def test_bybit_klines_paginated_parallel_pages_are_complete_and_ordered(monkeypatch):
    import data_feeds

    interval_ms = 3_600_000
    latest = 10_000 * interval_ms
    calls = []

    def fake_fetch(symbol, tf, limit, *, end_ms=None):
        calls.append((limit, end_ms))
        page_end = latest if end_ms is None else int(end_ms) // interval_ms * interval_ms
        return [
            {"open_time": page_end - interval_ms * offset, "time": "t", "close": 1.0}
            for offset in range(limit - 1, -1, -1)
        ]

    monkeypatch.setattr(data_feeds, "_fetch_bybit_klines", fake_fetch)

    candles = data_feeds._fetch_bybit_klines_paginated(
        "BTCUSDT",
        "H1",
        2_500,
        end_ms=latest,
        start_ms=latest - 2_499 * interval_ms,
        workers=4,
    )

    opens = [int(candle["open_time"]) for candle in candles]
    assert len(opens) == 2_500
    assert opens == sorted(opens)
    assert opens[0] == latest - 2_499 * interval_ms
    assert opens[-1] == latest
    assert len(calls) == 3


# test_backtest_crypto_signal_fetch_uses_configured_bybit_feed removed: it
# exercised the retired legacy backtester's _crypto_bt_signal_candles fetch
# path (now in archive/backtest_legacy/); the v3 rebuild reads its own parquet
# store and never routes through the live crypto signal feed selector.
