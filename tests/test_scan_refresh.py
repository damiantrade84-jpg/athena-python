from __future__ import annotations

from datetime import datetime, timezone

from athena_app.services.scan_backtest_service import handle_scan_request
from candles_cache import _candle_cache, _candle_cache_lock, fetch_candles


def _current_h1_candle() -> list[dict]:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        {
            "time": now.isoformat(),
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "vol": 100.0,
        }
    ]


def test_forced_candle_refresh_bypasses_existing_ttl_entry():
    pair = {"display": "TEST", "symbol": "TEST", "type": "stock", "source": "eodhd"}
    calls = {"eodhd": 0}

    def fetch_eodhd(*_args):
        calls["eodhd"] += 1
        return _current_h1_candle()

    with _candle_cache_lock:
        _candle_cache.clear()
    common = {
        "fetch_candles_live": lambda *_args: None,
        "fetch_binance": lambda *_args: None,
        "fetch_eodhd": fetch_eodhd,
        "fetch_polygon": lambda *_args: None,
        "fetch_yfinance": lambda *_args: None,
        "tf_b": {},
    }

    fetch_candles(pair, "H1", 1, **common)
    fetch_candles(pair, "H1", 1, force_refresh=True, **common)

    assert calls["eodhd"] == 2


def test_scan_request_forwards_manual_market_refresh_flag():
    received = {}

    handle_scan_request(
        {"style": "intraday", "asset_class": "crypto", "refresh_market_data": True},
        run_full_scan=lambda **kwargs: received.update(kwargs) or {"success": True},
    )

    assert received == {
        "style": "intraday",
        "asset_class": "crypto",
        "refresh_market_data": True,
    }
