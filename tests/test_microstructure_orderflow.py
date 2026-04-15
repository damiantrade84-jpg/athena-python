"""Taker imbalance ratio used for WebSocket orderflow_delta metrics."""

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
