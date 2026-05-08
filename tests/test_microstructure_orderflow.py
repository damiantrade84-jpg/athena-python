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
