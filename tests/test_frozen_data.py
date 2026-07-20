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


# The three frozen-mode backtester integration tests were removed with the
# legacy backtester (archive/backtest_legacy/): _bt_broker_spread_floor,
# _engine_a_level_atr_for_bt, and _bt_load_dxy_h4_for_gold were retired
# legacy-runner helpers. frozen_data's own read/write/hash contract stays
# fully covered by the tests above.
