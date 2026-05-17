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


def test_crypto_signal_feed_defaults_to_binance_without_touching_default_fetcher_shape():
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"}
    calls = []

    def default_fetch(pair_arg, tf_arg, limit_arg):
        calls.append((pair_arg, tf_arg, limit_arg))
        return _bars("binance", limit_arg)

    result = fetch_crypto_signal_candles(
        pair,
        "H4",
        3,
        engine="AB",
        config={},
        default_fetch=default_fetch,
        bybit_fetch=lambda *_args, **_kwargs: _bars("bybit"),
    )

    assert [c["source_tag"] for c in result.candles] == ["binance"] * 3
    assert calls == [(pair, "H4", 3)]
    assert result.meta["signalFeed"] == "binance"
    assert result.meta["actualPriceVolume"] is True
    assert result.meta["upstream"] == "binance_futures"


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


def test_resolve_crypto_signal_feed_supports_engine_specific_override():
    config = {
        "ENGINE_AB_CRYPTO_SIGNAL_FEED": "binance",
        "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
    }

    assert resolve_crypto_signal_feed("A", config) == "bybit"
    assert resolve_crypto_signal_feed("B", config) == "binance"


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


def test_backtest_crypto_signal_fetch_uses_configured_bybit_feed(monkeypatch):
    import backtest_runner
    from types import SimpleNamespace

    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"}
    calls = {"cached": [], "binance": [], "bybit_paginated": []}

    runtime = SimpleNamespace(
        fetch_binance=lambda symbol, interval, limit: calls["binance"].append(
            (symbol, interval, limit)
        )
        or _bars("binance", limit),
        fetch_binance_paginated=lambda symbol, interval, limit: calls["binance"].append(
            (symbol, interval, limit)
        )
        or _bars("binance", limit),
        fetch_bybit_klines=lambda symbol, tf, limit: _bars("bybit", limit),
        fetch_bybit_klines_paginated=lambda symbol, tf, limit: calls["bybit_paginated"].append(
            (symbol, tf, limit)
        )
        or _bars("bybit_paginated", limit),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_AB_CRYPTO_SIGNAL_FEED", "bybit")
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_AB_CRYPTO_SIGNAL_FEED_FALLBACK", False)
    monkeypatch.setattr(
        backtest_runner,
        "_bt_cached_fetch",
        lambda pair_arg, tf_arg, limit_arg, fetcher, provider, min_bars=None: calls[
            "cached"
        ].append((tf_arg, limit_arg, provider, min_bars))
        or fetcher(limit_arg),
    )

    candles = backtest_runner._crypto_bt_signal_candles(
        pair,
        engine="A",
        tf="H1",
        limit=1200,
        min_bars=500,
    )

    assert candles is not None
    assert candles[-1]["source_tag"] == "bybit_paginated"
    assert calls["bybit_paginated"] == [("BTCUSDT", "H1", 1200)]
    assert calls["binance"] == []
    assert calls["cached"] == [("H1", 1200, "bybit_linear_kline", 500)]
