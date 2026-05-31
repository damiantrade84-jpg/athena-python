from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backtest_candle_cache as cache
import frozen_data


def _bars(start: datetime, count: int, base: float = 1.0):
    return [
        {
            "time": (start + timedelta(hours=i)).isoformat(),
            "open": base + i,
            "high": base + i + 0.1,
            "low": base + i - 0.1,
            "close": base + i,
            "vol": 100 + i,
        }
        for i in range(count)
    ]


def _pair():
    return {"display": "BTC/USDT", "symbol": "BTCUSDT", "source": "binance", "type": "crypto"}


def test_frozen_backtest_cache_read_never_calls_live_fetcher(monkeypatch, tmp_path):
    monkeypatch.setenv("ATHENA_FROZEN_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("BACKTEST_DATA_AS_OF", "2026-05-30")
    start = datetime(2026, 5, 30, 8, tzinfo=timezone.utc)
    frozen_data.write_frozen_candles(
        "2026-05-30",
        _pair(),
        "H1",
        "bybit_linear_kline",
        _bars(start, 3, base=50.0),
    )

    rows = cache.fetch_backtest_candles(
        _pair(),
        "H1",
        3,
        lambda _limit: (_ for _ in ()).throw(AssertionError("live fetch called")),
        provider="bybit_linear_kline",
        min_bars=3,
    )

    assert [r["close"] for r in rows] == [50.0, 51.0, 52.0]


def test_frozen_backtest_cache_missing_series_hard_errors_without_live_fetch(monkeypatch, tmp_path):
    monkeypatch.setenv("ATHENA_FROZEN_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("BACKTEST_DATA_AS_OF", "2026-05-30")
    called = False

    def fetcher(_limit):
        nonlocal called
        called = True
        return _bars(datetime(2026, 5, 30, tzinfo=timezone.utc), 3)

    with pytest.raises(frozen_data.FrozenDataError, match="missing frozen candle series"):
        cache.fetch_backtest_candles(
            _pair(), "H1", 3, fetcher, provider="bybit_linear_kline", min_bars=3
        )

    assert called is False


def test_frozen_candle_hash_tamper_hard_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("ATHENA_FROZEN_DATA_ROOT", str(tmp_path))
    start = datetime(2026, 5, 30, 8, tzinfo=timezone.utc)
    rec = frozen_data.write_frozen_candles(
        "2026-05-30",
        _pair(),
        "H1",
        "binance_futures",
        _bars(start, 3, base=1.0),
    )
    path = tmp_path / rec["path"]
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["close"] = 999.0
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    with pytest.raises(frozen_data.FrozenDataError, match="hash mismatch"):
        frozen_data.read_frozen_candles(
            "2026-05-30", _pair(), "H1", "binance_futures", limit=3
        )


def test_frozen_backtest_spread_floor_does_not_query_mt5(monkeypatch):
    import backtest_runner

    monkeypatch.setenv("BACKTEST_DATA_AS_OF", "2026-05-30")
    backtest_runner._BROKER_SPREAD_CACHE.clear()
    fake_mt5 = types.SimpleNamespace(
        mt5_get_symbol_info=lambda _display: pytest.fail("live MT5 symbol info called")
    )
    monkeypatch.setitem(sys.modules, "mt5_executor", fake_mt5)

    assert backtest_runner._bt_broker_spread_floor(
        {"display": "EUR/USD", "symbol": "EURUSD", "source": "mt5", "type": "forex"}
    ) == 0.0


def test_frozen_engine_a_crypto_level_atr_uses_signal_atr_without_live_bybit(monkeypatch):
    import backtest_runner

    monkeypatch.setenv("BACKTEST_DATA_AS_OF", "2026-05-30")
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_A_CRYPTO_LEVELS_FEED", "bybit")
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK",
        False,
    )
    fake_runtime = types.SimpleNamespace(
        bybit_atr_for_levels=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(backtest_runner, "_rt", lambda: fake_runtime)

    atr, feed = backtest_runner._engine_a_level_atr_for_bt(
        123.45,
        _pair(),
        "intraday",
        as_of="2026-05-30T00:00:00+00:00",
    )

    assert atr == 123.45
    assert feed == "signal_frozen"


def test_frozen_gold_dxy_macro_reads_manifest_without_yfinance(monkeypatch, tmp_path):
    monkeypatch.setenv("ATHENA_FROZEN_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("BACKTEST_DATA_AS_OF", "2026-05-30")
    dxy_pair = {"display": "DX-Y.NYB", "symbol": "DX-Y.NYB", "source": "yfinance", "type": "macro"}
    start = datetime(2026, 5, 30, 8, tzinfo=timezone.utc)
    frozen_data.write_frozen_candles(
        "2026-05-30",
        dxy_pair,
        "H4",
        "yfinance",
        _bars(start, 2, base=100.0),
    )

    import backtest_runner

    monkeypatch.setattr(backtest_runner, "_rt", lambda: pytest.fail("live yfinance called"))

    rows = backtest_runner._bt_load_dxy_h4_for_gold({"display": "XAU/USD"})

    assert [r["close"] for r in rows] == [100.0, 101.0]
