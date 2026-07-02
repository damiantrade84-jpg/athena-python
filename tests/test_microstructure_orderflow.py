"""Taker imbalance ratio used for WebSocket orderflow_delta metrics."""

from datetime import datetime, timezone
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from athena.microstructure.orderbook_metrics import orderflow_delta


def test_taker_imbalance_all_buys():
    assert orderflow_delta(10.0, 0.0) == 1.0


def test_taker_imbalance_all_sells():
    assert orderflow_delta(0.0, 10.0) == -1.0


def test_taker_imbalance_mixed_fractional():
    # buy=3, sell=7 → (3-7)/10 = -0.4
    assert abs(orderflow_delta(3.0, 7.0) - (-0.4)) < 1e-9


def test_taker_imbalance_zero_trades():
    assert orderflow_delta(0.0, 0.0) == 0.0


def test_binance_ws_emit_uses_ratio_from_accumulators():
    from athena.datafeeds.binance_ws import BinanceWS

    ws = BinanceWS(symbol="btcusdt", emit_interval=60.0)
    ws._handle_trade({"q": "2", "m": False})  # buy taker
    ws._handle_trade({"q": "1", "m": True})  # sell taker
    r = _taker_ratio_from_ws(ws)
    assert abs(r - (1.0 / 3.0)) < 1e-9


def test_binance_ws_trade_stream_stores_price_bucket(monkeypatch):
    from athena.datafeeds import binance_ws

    stored = []
    monkeypatch.setattr(binance_ws, "store_trade", lambda **kwargs: stored.append(kwargs))
    ws = binance_ws.BinanceWS(symbol="btcusdt", emit_interval=60.0)

    ws._handle_trade({"p": "65000.5", "q": "0.25", "m": False, "T": 1710000000000})

    assert stored == [
        {
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "price": 65000.5,
            "quantity": 0.25,
            "is_buyer_maker": False,
            "ts": 1710000000.0,
        }
    ]


def test_binance_ws_aggtrade_stores_price_bucket(monkeypatch):
    from athena.datafeeds import binance_ws

    stored = []
    monkeypatch.setattr(binance_ws, "store_trade", lambda **kwargs: stored.append(kwargs))
    ws = binance_ws.BinanceWS(symbol="btcusdt", emit_interval=60.0)

    ws._handle_agg_trade({"p": "65000.5", "q": "0.25", "m": False, "T": 1710000000000})

    assert stored == [
        {
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "price": 65000.5,
            "quantity": 0.25,
            "is_buyer_maker": False,
            "ts": 1710000000.0,
        }
    ]


def test_binance_futures_micro_stream_url_includes_aggtrade():
    from athena.datafeeds.binance_ws import binance_futures_micro_stream_url

    url = binance_futures_micro_stream_url("ethusdt")
    assert "aggTrade" in url
    assert "@trade" in url
    assert "ethusdt@depth20@100ms" in url


def test_bucketed_volume_profile_uses_price_level_volume():
    from volume_profile import compute_bucketed_volume_profile

    buckets = [
        {"price_bucket": 99.0, "total_volume": 10.0, "delta": -5.0},
        {"price_bucket": 100.0, "total_volume": 50.0, "delta": 20.0},
        {"price_bucket": 101.0, "total_volume": 25.0, "delta": 10.0},
        {"price_bucket": 102.0, "total_volume": 5.0, "delta": -2.0},
    ]

    vp = compute_bucketed_volume_profile(buckets, value_area_pct=0.70, lvn_threshold=0.2)

    assert vp["profile_valid"] is True
    assert vp["source"] == "trade_buckets"
    assert vp["poc"] == 100.0
    assert vp["val"] == 100.0
    assert vp["vah"] == 101.0
    assert vp["cvd_value"] == 23.0


def test_trade_bucket_query_supports_point_in_time_upper_bound(tmp_path, monkeypatch):
    from athena.microstructure import trade_bucket_store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "trade_buckets.db")
    store._ensure_db()

    early_ts = datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc).timestamp()
    late_ts = datetime(2026, 3, 26, 15, 0, tzinfo=timezone.utc).timestamp()
    store.store_trade(
        exchange="binance",
        symbol="BTCUSDT",
        price=100.0,
        quantity=1.0,
        is_buyer_maker=False,
        ts=early_ts,
    )
    store.store_trade(
        exchange="binance",
        symbol="BTCUSDT",
        price=101.0,
        quantity=1.0,
        is_buyer_maker=True,
        ts=late_ts,
    )

    rows = store.query_session_buckets(
        "BTCUSDT",
        exchange="binance",
        session_id="2026-03-26",
        max_last_ts=early_ts + 60,
    )

    assert len(rows) == 1
    assert rows[0]["price_bucket"] == 100.0
    assert rows[0]["last_ts"] <= early_ts + 60


def test_scalp_trade_bucket_helpers_bound_historical_reference_ts(monkeypatch):
    import scalp_engine
    import volume_profile
    from athena.microstructure import trade_bucket_store as store

    cfg = dict(scalp_engine.CONFIG.get("SCALP_ENGINE", {}) or {})
    cfg["TRADE_BUCKET_MIN_LEVELS"] = 1
    cfg["TRADE_BUCKET_MIN_VOLUME"] = 0.0
    monkeypatch.setitem(scalp_engine.CONFIG, "SCALP_ENGINE", cfg)

    calls = []

    def fake_query(symbol, **kwargs):
        calls.append({"symbol": symbol, **kwargs})
        return [{"price_bucket": 100.0, "total_volume": 10.0, "delta": 2.0, "last_ts": 0.0}]

    monkeypatch.setattr(store, "query_session_buckets", fake_query)
    monkeypatch.setattr(
        volume_profile,
        "compute_bucketed_volume_profile",
        lambda rows, value_area_pct=0.70, lvn_threshold=0.30: {
            "profile_valid": True,
            "poc": 100.0,
            "vah": 101.0,
            "val": 99.0,
            "session_high": 102.0,
            "session_low": 98.0,
            "total_volume": 10.0,
        },
    )

    out = scalp_engine._build_trade_bucket_volume_profile(
        "BTC/USDT",
        reference_ts="2026-03-26T14:15:00+00:00",
        require_fresh=False,
    )

    assert out["valid"] is True
    assert calls[0]["session_id"] == "2026-03-26"
    assert calls[0]["min_last_ts"] is None
    assert calls[0]["max_last_ts"] == datetime(2026, 3, 26, 14, 15, tzinfo=timezone.utc).timestamp()


def test_shared_aggtrade_helpers_build_vp_and_cvd_from_same_bucket_query(monkeypatch):
    from athena.microstructure import trade_bucket_store as store
    from athena.microstructure.aggtrade_volume import (
        build_trade_bucket_volume_profile,
        check_trade_bucket_cvd,
    )

    calls = []
    rows = [
        {"price_bucket": 99.0, "total_volume": 10.0, "delta": -4.0, "last_ts": 1.0},
        {"price_bucket": 100.0, "total_volume": 40.0, "delta": 8.0, "last_ts": 2.0},
        {"price_bucket": 101.0, "total_volume": 25.0, "delta": 12.0, "last_ts": 3.0},
    ]

    def fake_query(symbol, **kwargs):
        calls.append({"symbol": symbol, **kwargs})
        return rows

    monkeypatch.setattr(store, "query_session_buckets", fake_query)
    cfg = {
        "TRADE_BUCKET_EXCHANGE": "bybit",
        "TRADE_BUCKET_MIN_LEVELS": 3,
        "TRADE_BUCKET_MIN_VOLUME": 0.0,
        "TRADE_BUCKET_MAX_AGE_SEC": 900,
        "VP_VALUE_AREA_PCT": 0.70,
        "VP_LVN_THRESHOLD": 0.15,
        "CVD_MIN_SLOPE": 0.0,
    }

    vp = build_trade_bucket_volume_profile(
        "BTC/USDT",
        cfg,
        reference_ts="2026-03-26T14:15:00+00:00",
        require_fresh=False,
    )
    cvd = check_trade_bucket_cvd(
        "BTC/USDT",
        cfg,
        reference_ts="2026-03-26T14:15:00+00:00",
        require_fresh=False,
    )

    assert vp["valid"] is True
    assert vp["volume_source"] == "bybit_public_trade"
    assert cvd["source"] == "bybit_public_trade"
    assert calls[0]["exchange"] == "bybit"
    assert cvd["direction"] == "LONG"
    assert calls[0]["symbol"] == "BTCUSDT"
    assert calls[0]["session_id"] == "2026-03-26"
    assert calls[0]["max_last_ts"] == datetime(2026, 3, 26, 14, 15, tzinfo=timezone.utc).timestamp()


def test_shared_trade_bucket_helpers_allow_binance_override(monkeypatch):
    from athena.microstructure import trade_bucket_store as store
    from athena.microstructure.aggtrade_volume import build_trade_bucket_volume_profile

    calls = []

    def fake_query(symbol, **kwargs):
        calls.append({"symbol": symbol, **kwargs})
        return [
            {"price_bucket": 99.0, "total_volume": 10.0, "delta": -4.0, "last_ts": 1.0},
            {"price_bucket": 100.0, "total_volume": 40.0, "delta": 8.0, "last_ts": 2.0},
            {"price_bucket": 101.0, "total_volume": 25.0, "delta": 12.0, "last_ts": 3.0},
        ]

    monkeypatch.setattr(store, "query_session_buckets", fake_query)

    vp = build_trade_bucket_volume_profile(
        "BTC/USDT",
        {
            "TRADE_BUCKET_EXCHANGE": "binance",
            "TRADE_BUCKET_MIN_LEVELS": 3,
            "TRADE_BUCKET_MIN_VOLUME": 0.0,
        },
        require_fresh=False,
    )

    assert vp["valid"] is True
    assert vp["volume_source"] == "binance_aggtrade"
    assert calls[0]["exchange"] == "binance"


def test_bybit_ws_emit_uses_ratio_from_accumulators():
    from athena.datafeeds.bybit_ws import BybitWS

    ws = BybitWS(symbol="BTCUSDT", emit_interval=60.0)
    ws._handle_trade({"v": "4", "S": "Buy"})
    ws._handle_trade({"v": "4", "S": "Sell"})
    r = _taker_ratio_from_ws(ws)
    assert r == 0.0


def _taker_ratio_from_ws(ws):
    """Mirror emit math without running async loop."""
    from athena.microstructure.orderbook_metrics import orderflow_delta as _r

    return _r(ws.buy_taker_volume, ws.sell_taker_volume)


# ─────────────────────────────────────────────────────────────────────────────
# F1: BinanceMicroMultiWS — multiplexed combined-stream client tests
# ─────────────────────────────────────────────────────────────────────────────


def test_binance_micro_multi_ws_url_includes_all_streams():
    from athena.datafeeds.binance_ws import binance_futures_multi_micro_stream_url

    url = binance_futures_multi_micro_stream_url(["btcusdt", "ethusdt"])
    for sym in ("btcusdt", "ethusdt"):
        assert f"{sym}@aggTrade" in url
        assert f"{sym}@trade" in url
        assert f"{sym}@depth20@100ms" in url
    assert url.startswith("wss://fstream.binance.com/stream?streams=")


def test_binance_micro_multi_ws_url_dedupes_and_normalises():
    from athena.datafeeds.binance_ws import binance_futures_multi_micro_stream_url

    url = binance_futures_multi_micro_stream_url(["BTC/USDT", "btcusdt", "ethusdt"])
    # btcusdt appears once despite two inputs
    assert url.count("btcusdt@aggTrade") == 1
    assert url.count("ethusdt@aggTrade") == 1


def test_binance_micro_multi_ws_dispatches_per_symbol_aggtrade(monkeypatch):
    """Combined-stream envelope must route each @aggTrade payload to store_trade with the right symbol."""
    import asyncio

    from athena.datafeeds import binance_ws

    stored: list[dict] = []
    monkeypatch.setattr(binance_ws, "store_trade", lambda **kwargs: stored.append(kwargs))

    multi = binance_ws.BinanceMicroMultiWS(symbols=["btcusdt", "ethusdt"])

    async def _drive():
        await multi._handle_message(
            {
                "stream": "btcusdt@aggTrade",
                "data": {"p": "65000.0", "q": "0.1", "m": False, "T": 1710000000000},
            }
        )
        await multi._handle_message(
            {
                "stream": "ethusdt@aggTrade",
                "data": {"p": "3500.0", "q": "1.0", "m": True, "T": 1710000001000},
            }
        )

    asyncio.run(_drive())

    assert [r["symbol"] for r in stored] == ["BTCUSDT", "ETHUSDT"]
    assert stored[0]["price"] == 65000.0
    assert stored[1]["is_buyer_maker"] is True


def test_binance_micro_multi_ws_per_symbol_state_isolation():
    """A @trade for one symbol must not bleed accumulators into another."""
    import asyncio

    from athena.datafeeds.binance_ws import BinanceMicroMultiWS

    multi = BinanceMicroMultiWS(symbols=["btcusdt", "ethusdt"])

    async def _drive():
        await multi._handle_message(
            {
                "stream": "btcusdt@trade",
                "data": {"p": "65000.0", "q": "2.0", "m": False, "T": 1710000000000},
            }
        )

    asyncio.run(_drive())

    btc = multi._state["BTCUSDT"]
    eth = multi._state["ETHUSDT"]
    assert btc.buy_taker_volume == 2.0
    assert btc.sell_taker_volume == 0.0
    assert eth.buy_taker_volume == 0.0
    assert eth.sell_taker_volume == 0.0
    assert btc.last_msg_ts > 0
    assert eth.last_msg_ts == 0.0


def test_binance_micro_multi_ws_stale_symbol_watchdog_detects_silent_stream():
    """If any one symbol has been silent past stale_symbol_seconds, watchdog must list it."""
    import time as _time

    from athena.datafeeds.binance_ws import BinanceMicroMultiWS

    multi = BinanceMicroMultiWS(
        symbols=["btcusdt", "ethusdt"], stale_symbol_seconds=60.0
    )
    fake_now = _time.time()
    # Simulate a connection that has been up for 5 minutes.
    multi._connected_at = fake_now - 300
    # ETH heartbeating recently; BTC silent for >60s.
    multi._state["BTCUSDT"].last_msg_ts = fake_now - 120
    multi._state["ETHUSDT"].last_msg_ts = fake_now - 5

    stale = multi._check_stale_symbols(now=fake_now)
    assert stale == ["BTCUSDT"]


def test_binance_micro_multi_ws_stale_watchdog_silent_during_cold_start():
    """Watchdog must not fire while the connection has been up less than the stale window."""
    import time as _time

    from athena.datafeeds.binance_ws import BinanceMicroMultiWS

    multi = BinanceMicroMultiWS(
        symbols=["btcusdt"], stale_symbol_seconds=120.0
    )
    fake_now = _time.time()
    # Connection just came up; even if last_msg_ts looks old, suppress until cold-start window passes.
    multi._connected_at = fake_now - 30
    multi._state["BTCUSDT"].last_msg_ts = fake_now - 200

    assert multi._check_stale_symbols(now=fake_now) == []


def test_binance_micro_multi_ws_stream_cap_guard():
    """Constructor must refuse symbol lists that would exceed the per-connection stream cap."""
    import pytest

    from athena.datafeeds.binance_ws import BinanceMicroMultiWS

    too_many = [f"sym{i}usdt" for i in range(BinanceMicroMultiWS._MAX_STREAMS // 3 + 1)]
    with pytest.raises(ValueError):
        BinanceMicroMultiWS(symbols=too_many)


def test_binance_micro_multi_ws_rejects_empty_symbols():
    import pytest

    from athena.datafeeds.binance_ws import BinanceMicroMultiWS

    with pytest.raises(ValueError):
        BinanceMicroMultiWS(symbols=[])


def test_default_bucket_size_major_crypto_granularity():
    from athena.microstructure.trade_bucket_store import bucket_price, default_bucket_size

    assert default_bucket_size(61250.0) == 1.0
    assert default_bucket_size(1648.0) == 0.1
    assert default_bucket_size(82.0) == 0.001

    btc_buckets = {bucket_price(p)[0] for p in (61250.0, 61251.2, 61252.8)}
    eth_buckets = {bucket_price(p)[0] for p in (1648.05, 1648.15, 1648.35)}
    sol_buckets = {bucket_price(p)[0] for p in (82.26, 82.27, 82.28)}

    assert len(btc_buckets) >= 3
    assert len(eth_buckets) >= 3
    assert len(sol_buckets) >= 3
