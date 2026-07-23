from __future__ import annotations

import sys
import time
import types

import pytest

if "eodhd" not in sys.modules:
    eodhd_stub = types.ModuleType("eodhd")
    eodhd_stub.APIClient = object
    sys.modules["eodhd"] = eodhd_stub

import candle_feeds

MT5_PAIR = {
    "symbol": "USDJPY=X",
    "display": "USD/JPY",
    "type": "forex",
    "source": "mt5",
    "enabled": True,
    "ws": True,
}

EODHD_PAIR = {
    "symbol": "TEST.US",
    "display": "Test Stock",
    "type": "stock",
    "source": "eodhd",
    "enabled": True,
    "ws": True,
}

MIXED_PAIRS = [MT5_PAIR, EODHD_PAIR]


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


def test_mt5_price_not_overwritten_by_eodhd_ws():
    candle_feeds._record_mt5_live_price(
        "USD/JPY",
        112.671,
        bid=112.670,
        ask=112.672,
        now_ts=1000.0,
        broker_ts=999.5,
    )

    eodhd_entry = {
        "price": 112.800,
        "bid": 112.799,
        "ask": 112.801,
        "ts": 1001.0,
        "source": "eodhd_forex",
    }
    accepted = candle_feeds._record_eodhd_ws_live_price(
        "USD/JPY",
        eodhd_entry,
        pairs=MIXED_PAIRS,
    )

    assert accepted is False
    entry = _entry("USD/JPY")
    assert entry["source"] == "mt5"
    assert entry["price"] == 112.671
    assert entry["broker_ts"] == 999.5


def test_build_ticker_map_includes_only_eodhd_pairs():
    mgr = candle_feeds.EODHDWebSocketManager("test-key")
    ticker_info = mgr._build_ticker_map(MIXED_PAIRS)

    all_tickers = ticker_info["us"] + ticker_info["forex"] + ticker_info["crypto"]
    assert "USDJPY" not in all_tickers
    assert "TEST" in all_tickers
    assert ticker_info["map"]["TEST"] == "Test Stock"


def test_eodhd_pair_accepts_ws_live_price():
    eodhd_entry = {
        "price": 245.5,
        "ts": 2000.0,
        "source": "eodhd_us",
    }
    accepted = candle_feeds._record_eodhd_ws_live_price(
        "Test Stock",
        eodhd_entry,
        pairs=MIXED_PAIRS,
    )

    assert accepted is True
    entry = _entry("Test Stock")
    assert entry["source"] == "eodhd_us"
    assert entry["price"] == 245.5


def test_record_mt5_live_price_sets_source_and_display_key():
    candle_feeds._record_mt5_live_price(
        "EUR/USD",
        1.08543,
        bid=1.08540,
        ask=1.08546,
        now_ts=3000.0,
    )

    entry = _entry("EUR/USD")
    assert entry["source"] == "mt5"
    assert entry["price"] == 1.08543
    assert entry["ts"] == 3000.0


@pytest.mark.parametrize(
    ("trade_mode", "expected"),
    ((4, "open"), (1, "open"), (0, "closed"), (3, "closed")),
)
def test_mt5_symbol_market_state_uses_trade_mode(trade_mode, expected):
    class _FakeMT5:
        SYMBOL_TRADE_MODE_DISABLED = 0
        SYMBOL_TRADE_MODE_CLOSEONLY = 3

        @staticmethod
        def symbol_info(symbol):
            return types.SimpleNamespace(trade_mode=trade_mode)

    assert candle_feeds._mt5_symbol_market_state(_FakeMT5(), "USDCHF") == expected


def test_mt5_symbol_market_state_fails_closed_without_symbol_info():
    mt5 = types.SimpleNamespace(
        SYMBOL_TRADE_MODE_DISABLED=0,
        SYMBOL_TRADE_MODE_CLOSEONLY=3,
        symbol_info=lambda symbol: None,
    )

    assert candle_feeds._mt5_symbol_market_state(mt5, "USDCHF") == "unavailable"


def test_record_mt5_live_price_preserves_verified_market_state():
    candle_feeds._record_mt5_live_price(
        "USD/CHF",
        0.81625,
        bid=0.81620,
        ask=0.81630,
        now_ts=4000.0,
        broker_ts=3999.5,
        market_state="open",
    )
    candle_feeds._record_mt5_live_price(
        "USD/CHF",
        0.81626,
        bid=0.81621,
        ask=0.81631,
        now_ts=4001.0,
        broker_ts=4000.5,
    )

    assert _entry("USD/CHF")["market_state"] == "open"

    candle_feeds._record_mt5_live_price(
        "USD/CHF",
        0.81626,
        now_ts=4002.0,
        market_state="unexpected",
    )
    assert _entry("USD/CHF")["market_state"] == "unavailable"


def test_scan_speed_state_uses_mt5_broker_timestamp(monkeypatch):
    import scanner
    import timeframe_policy

    captured: list[dict] = []

    def _capture_speed_state(*args, **kwargs):
        captured.append(kwargs)
        return types.SimpleNamespace(live_speed_class="NORMAL")

    monkeypatch.setattr(
        timeframe_policy,
        "calculate_speed_state",
        _capture_speed_state,
    )
    now = time.time()
    pair = {
        "display": "USD/CHF",
        "symbol": "USDCHF",
        "type": "forex",
        "source": "mt5",
    }
    quote = {
        "USD/CHF": {
            "price": 0.81625,
            "bid": 0.81620,
            "ask": 0.81630,
            "ts": now,
            "broker_ts": now - 10.0,
            "source": "mt5",
            "market_state": "open",
        }
    }

    scanner._scan_speed_state(pair, {}, quote)
    assert 9.0 <= captured[-1]["quote_age_sec"] <= 12.0

    quote["USD/CHF"].pop("broker_ts")
    scanner._scan_speed_state(pair, {}, quote)
    assert captured[-1]["quote_age_sec"] is None
