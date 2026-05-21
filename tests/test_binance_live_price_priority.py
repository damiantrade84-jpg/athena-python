from __future__ import annotations

import sys
import types

import pytest

if "eodhd" not in sys.modules:
    eodhd_stub = types.ModuleType("eodhd")
    eodhd_stub.APIClient = object
    sys.modules["eodhd"] = eodhd_stub

import candle_feeds


PAIRS = [
    {
        "symbol": "XRPUSDT",
        "display": "XRP/USDT",
        "type": "crypto",
        "source": "binance",
        "enabled": True,
    },
    {
        "symbol": "FILUSDT",
        "display": "FIL/USDT",
        "type": "crypto",
        "source": "binance",
        "enabled": True,
    },
]


@pytest.fixture(autouse=True)
def _clear_live_prices():
    with candle_feeds._live_prices_lock:
        old = dict(candle_feeds._live_prices)
        candle_feeds._live_prices.clear()
    try:
        yield
    finally:
        with candle_feeds._live_prices_lock:
            candle_feeds._live_prices.clear()
            candle_feeds._live_prices.update(old)


def _entry(display: str) -> dict:
    with candle_feeds._live_prices_lock:
        return dict(candle_feeds._live_prices[display])


def test_ws_update_stores_binance_ws_source_and_display_key():
    ok = candle_feeds._record_binance_ws_price(
        "XRPUSDT",
        0.51,
        bid=0.509,
        ask=0.511,
        now_ts=1000.0,
        pairs=PAIRS,
    )

    assert ok is True
    entry = _entry("XRP/USDT")
    assert "XRPUSDT" not in candle_feeds._live_prices
    assert entry["source"] == "binance_ws"
    assert entry["preferred_source"] == "binance_ws"
    assert entry["last_ws_ts"] == 1000.0
    assert entry["bid"] == pytest.approx(0.509)
    assert entry["ask"] == pytest.approx(0.511)


def test_rest_update_does_not_overwrite_fresh_ws_source_bid_ask():
    assert candle_feeds._record_binance_ws_price(
        "XRPUSDT",
        0.51,
        bid=0.509,
        ask=0.511,
        now_ts=1000.0,
        pairs=PAIRS,
    )

    assert candle_feeds._record_binance_rest_price(
        "XRP/USDT",
        0.52,
        now_ts=1003.0,
        pairs=PAIRS,
        ws_stale_sec=10.0,
    )

    entry = _entry("XRP/USDT")
    assert entry["source"] == "binance_ws"
    assert entry["preferred_source"] == "binance_ws"
    assert entry["overwrite_reason"] == "fresh_ws_preferred_over_rest"
    assert entry["price"] == pytest.approx(0.51)
    assert entry["bid"] == pytest.approx(0.509)
    assert entry["ask"] == pytest.approx(0.511)
    assert entry["last_rest_ts"] == 1003.0
    assert entry["rest_price"] == pytest.approx(0.52)


def test_rest_fills_when_ws_missing_or_stale():
    assert candle_feeds._record_binance_rest_price(
        "FILUSDT",
        5.25,
        now_ts=2000.0,
        pairs=PAIRS,
        ws_stale_sec=10.0,
    )
    missing_entry = _entry("FIL/USDT")
    assert missing_entry["source"] == "binance_rest"
    assert missing_entry["overwrite_reason"] == "ws_missing"

    assert candle_feeds._record_binance_ws_price(
        "XRPUSDT",
        0.51,
        bid=0.509,
        ask=0.511,
        now_ts=1000.0,
        pairs=PAIRS,
    )
    assert candle_feeds._record_binance_rest_price(
        "XRPUSDT",
        0.53,
        now_ts=1020.0,
        pairs=PAIRS,
        ws_stale_sec=10.0,
    )
    stale_entry = _entry("XRP/USDT")
    assert stale_entry["source"] == "binance_rest"
    assert stale_entry["preferred_source"] == "binance_rest"
    assert stale_entry["overwrite_reason"] == "ws_stale"
    assert stale_entry["price"] == pytest.approx(0.53)
    assert "bid" not in stale_entry
    assert "ask" not in stale_entry


def test_symbol_normalization_maps_raw_and_display_to_same_entry():
    assert candle_feeds._record_binance_ws_price(
        "XRPUSDT",
        0.51,
        now_ts=1000.0,
        pairs=PAIRS,
    )
    assert candle_feeds._record_binance_rest_price(
        "XRP/USDT",
        0.52,
        now_ts=1003.0,
        pairs=PAIRS,
        ws_stale_sec=10.0,
    )

    with candle_feeds._live_prices_lock:
        assert sorted(candle_feeds._live_prices.keys()) == ["XRP/USDT"]
