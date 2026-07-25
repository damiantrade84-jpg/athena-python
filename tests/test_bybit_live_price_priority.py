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
        "symbol": "TRXUSDT",
        "display": "TRX/USDT",
        "type": "crypto",
        "source": "binance",
        "enabled": True,
    },
    {
        "symbol": "XRPUSDT",
        "display": "XRP/USDT",
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


def test_ws_update_stores_bybit_ws_source_and_display_key():
    ok = candle_feeds._record_bybit_ws_price(
        "TRXUSDT",
        0.2935,
        bid=0.2934,
        ask=0.2936,
        now_ts=1000.0,
        pairs=PAIRS,
        category="linear",
    )

    assert ok is True
    entry = _entry("TRX/USDT")
    assert "TRXUSDT" not in candle_feeds._live_prices
    assert entry["source"] == "bybit_ws"
    assert entry["preferred_source"] == "bybit_ws"
    assert entry["bybit_category"] == "linear"
    assert entry["bybit_symbol"] == "TRXUSDT"
    assert entry["last_ws_ts"] == 1000.0


def test_ws_delta_without_bbo_carries_forward_known_bid_ask():
    # Bybit v5 ticker is snapshot+delta; delta ticks omit unchanged fields.
    # A price-only delta must not wipe the last known bid/ask, otherwise
    # spread stays unmeasurable and liquidity gating fails to UNAVAILABLE.
    assert candle_feeds._record_bybit_ws_price(
        "TRXUSDT",
        0.2935,
        bid=0.2934,
        ask=0.2936,
        now_ts=1000.0,
        pairs=PAIRS,
        category="linear",
    )
    assert candle_feeds._record_bybit_ws_price(
        "TRXUSDT",
        0.2940,
        now_ts=1001.0,
        pairs=PAIRS,
        category="linear",
    )

    entry = _entry("TRX/USDT")
    assert entry["price"] == pytest.approx(0.2940)
    assert entry["bid"] == pytest.approx(0.2934)
    assert entry["ask"] == pytest.approx(0.2936)


def test_rest_update_does_not_overwrite_fresh_ws():
    assert candle_feeds._record_bybit_ws_price(
        "TRXUSDT",
        0.2935,
        bid=0.2934,
        ask=0.2936,
        now_ts=1000.0,
        pairs=PAIRS,
        category="linear",
    )

    assert candle_feeds._record_bybit_rest_price(
        "TRXUSDT",
        0.295,
        now_ts=1003.0,
        pairs=PAIRS,
        category="linear",
        ws_stale_sec=10.0,
    )

    entry = _entry("TRX/USDT")
    assert entry["source"] == "bybit_ws"
    assert entry["overwrite_reason"] == "fresh_ws_preferred_over_rest"
    assert entry["price"] == pytest.approx(0.2935)


def test_rest_fills_when_ws_missing_or_stale():
    assert candle_feeds._record_bybit_rest_price(
        "XRPUSDT",
        0.51,
        now_ts=2000.0,
        pairs=PAIRS,
        category="linear",
        ws_stale_sec=10.0,
    )
    missing_entry = _entry("XRP/USDT")
    assert missing_entry["source"] == "bybit_rest"
    assert missing_entry["overwrite_reason"] == "ws_missing"

    assert candle_feeds._record_bybit_ws_price(
        "TRXUSDT",
        0.2935,
        now_ts=1000.0,
        pairs=PAIRS,
        category="linear",
    )
    assert candle_feeds._record_bybit_rest_price(
        "TRXUSDT",
        0.30,
        now_ts=1020.0,
        pairs=PAIRS,
        category="linear",
        ws_stale_sec=10.0,
    )
    stale_entry = _entry("TRX/USDT")
    assert stale_entry["source"] == "bybit_rest"
    assert stale_entry["overwrite_reason"] == "ws_stale"
    assert stale_entry["price"] == pytest.approx(0.30)


def test_rest_does_not_downgrade_ws_when_global_feed_active():
    assert candle_feeds._record_bybit_ws_price(
        "TRXUSDT",
        0.2935,
        now_ts=1000.0,
        pairs=PAIRS,
        category="linear",
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        candle_feeds,
        "_bybit_ws_runtime_state",
        lambda: {"websocket_active": True, "websocket_stale": False},
    )
    try:
        assert candle_feeds._record_bybit_rest_price(
            "TRXUSDT",
            0.30,
            now_ts=1020.0,
            pairs=PAIRS,
            category="linear",
            ws_stale_sec=10.0,
        )
        entry = _entry("TRX/USDT")
        assert entry["source"] == "bybit_ws"
        assert entry["price"] == pytest.approx(0.2935)
        assert entry["rest_price"] == pytest.approx(0.30)
        assert entry["overwrite_reason"] == "rest_metadata_while_ws_active"
    finally:
        monkeypatch.undo()


def test_get_bybit_live_tick_keeps_ws_while_global_feed_active_even_if_symbol_stale():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        candle_feeds,
        "_bybit_ws_runtime_state",
        lambda: {"websocket_active": True, "websocket_stale": False},
    )
    monkeypatch.setattr(candle_feeds.time, "time", lambda: 1025.0)
    try:
        assert candle_feeds._record_bybit_ws_price(
            "TRXUSDT",
            0.2935,
            now_ts=1000.0,
            pairs=PAIRS,
            category="linear",
        )
        with candle_feeds._live_prices_lock:
            candle_feeds._live_prices["TRX/USDT"]["source"] = "bybit_rest"

        result = candle_feeds.get_bybit_live_tick(
            "TRX/USDT",
            bybit_symbol="TRXUSDT",
            category="linear",
            pairs=PAIRS,
        )
        assert result["fallback_reason"] is None
        assert result["tick"]["source"] == "bybit_ws"
        assert result["tick"]["price"] == pytest.approx(0.2935)
        assert result["tick"]["age_seconds"] == pytest.approx(25.0)
    finally:
        monkeypatch.undo()


def test_get_bybit_live_tick_returns_ws_when_fresh():
    import time

    now = time.time()
    assert candle_feeds._record_bybit_ws_price(
        "TRXUSDT",
        0.2935,
        now_ts=now,
        pairs=PAIRS,
        category="linear",
    )
    result = candle_feeds.get_bybit_live_tick(
        "TRX/USDT",
        bybit_symbol="TRXUSDT",
        category="linear",
        pairs=PAIRS,
    )
    assert result["fallback_reason"] is None
    assert result["tick"]["source"] == "bybit_ws"
    assert result["tick"]["bybit_symbol"] == "TRXUSDT"
    assert result["tick"]["bybit_category"] == "linear"


def test_bybit_category_linear_for_trxusdt():
    pair = PAIRS[0]
    assert candle_feeds._bybit_category_for_pair(pair, {"CRYPTO_CHART_BYBIT_CATEGORY": "linear"}) == "linear"
    assert candle_feeds._bybit_ws_public_url("linear") == "wss://stream.bybit.com/v5/public/linear"
