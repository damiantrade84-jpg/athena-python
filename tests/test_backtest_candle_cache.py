from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import backtest_candle_cache as cache


def _bars(start: datetime, count: int, step_hours: int, base: float = 1.0):
    out = []
    for i in range(count):
        px = base + i * 0.01
        out.append(
            {
                "time": (start + timedelta(hours=i * step_hours)).isoformat(),
                "open": px,
                "high": px + 0.1,
                "low": px - 0.1,
                "close": px,
                "vol": 100 + i,
            }
        )
    return out


def _use_workspace_db(monkeypatch):
    root = Path(__file__).resolve().parent / ".tmp" / "bt_candle_cache"
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / f"{uuid4().hex}.db"
    monkeypatch.setenv("ATHENA_BACKTEST_CANDLE_CACHE_DB_PATH", str(db_path))
    return db_path


def test_backtest_candle_cache_reuses_fresh_series(monkeypatch):
    _use_workspace_db(monkeypatch)
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "source": "binance", "type": "crypto"}
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    calls = []

    def fetcher(limit):
        calls.append(limit)
        return _bars(now - timedelta(hours=4), 5, 1)

    first = cache.fetch_backtest_candles(
        pair, "H1", 5, fetcher, provider="binance_futures", min_bars=5
    )
    second = cache.fetch_backtest_candles(
        pair, "H1", 5, lambda _limit: (_ for _ in ()).throw(AssertionError("cache miss")),
        provider="binance_futures",
        min_bars=5,
    )

    assert calls == [5]
    assert [c["close"] for c in second] == [c["close"] for c in first]


def test_backtest_candle_cache_separates_crypto_signal_providers(monkeypatch):
    _use_workspace_db(monkeypatch)
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "source": "binance", "type": "crypto"}
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    cache.fetch_backtest_candles(
        pair,
        "H1",
        5,
        lambda _limit: _bars(now - timedelta(hours=4), 5, 1, base=1.0),
        provider="binance_futures",
        min_bars=5,
    )

    bybit_calls = []

    def bybit_fetch(limit):
        bybit_calls.append(limit)
        return _bars(now - timedelta(hours=4), 5, 1, base=50.0)

    bybit = cache.fetch_backtest_candles(
        pair,
        "H1",
        5,
        bybit_fetch,
        provider="bybit_linear_kline",
        min_bars=5,
    )

    assert bybit_calls == [5]
    assert bybit[0]["close"] >= 50.0

    binance_again = cache.fetch_backtest_candles(
        pair,
        "H1",
        5,
        lambda _limit: (_ for _ in ()).throw(AssertionError("binance cache miss")),
        provider="binance_futures",
        min_bars=5,
    )

    binance_count, binance_newest, _ = cache._cache_state(pair, "H1", "binance_futures")
    bybit_count, bybit_newest, _ = cache._cache_state(pair, "H1", "bybit_linear_kline")

    assert [c["close"] for c in binance_again] == [1.0, 1.01, 1.02, 1.03, 1.04]
    assert binance_count == 5
    assert bybit_count == 5
    assert binance_newest == bybit_newest


def test_backtest_candle_cache_topups_stale_series(monkeypatch):
    _use_workspace_db(monkeypatch)
    pair = {"display": "ETH/USDT", "symbol": "ETHUSDT", "source": "binance", "type": "crypto"}
    old_start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=12)

    cache.fetch_backtest_candles(
        pair,
        "H1",
        200,
        lambda _limit: _bars(old_start, 200, 1),
        provider="binance_futures",
        min_bars=100,
    )

    topup_limits = []

    def topup(limit):
        topup_limits.append(limit)
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=limit)
        return _bars(start, limit, 1, base=10.0)

    out = cache.fetch_backtest_candles(
        pair, "H1", 200, topup, provider="binance_futures", min_bars=100
    )

    assert topup_limits
    assert 100 <= topup_limits[0] < 200
    assert out[-1]["close"] >= 10.0


def test_eodhd_intraday_cache_is_scoped_away_from_mt5_primary(monkeypatch):
    _use_workspace_db(monkeypatch)
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "source": "mt5", "type": "forex"}
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    cache.fetch_backtest_candles(
        pair,
        "H1",
        3,
        lambda _limit: _bars(now - timedelta(hours=2), 3, 1, base=1.0),
        provider="mt5",
        min_bars=3,
    )

    cache.fetch_backtest_eodhd_intraday(
        pair,
        7,
        lambda _days: (
            _bars(now - timedelta(hours=8), 3, 4, base=2.0),
            _bars(now - timedelta(hours=2), 3, 1, base=2.0),
        ),
        h4_limit=3,
        h1_limit=3,
    )

    primary = cache.fetch_backtest_candles(
        pair,
        "H1",
        3,
        lambda _limit: (_ for _ in ()).throw(AssertionError("primary cache miss")),
        provider="mt5",
        min_bars=3,
    )

    assert primary[-1]["close"] < 2.0
