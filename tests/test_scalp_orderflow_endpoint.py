"""Tests for GET /api/scalp-orderflow."""

from types import SimpleNamespace

import pytest
from flask import Flask

from athena_app.api.routes_scalp_orderflow import register_scalp_orderflow_routes
from scalp_orderflow import build_scalp_orderflow_payload


@pytest.fixture
def client():
    app = Flask(__name__)
    register_scalp_orderflow_routes(
        app,
        SimpleNamespace(find_pair_fn=lambda _symbol: {"type": "crypto", "display": "BTCUSDT"}),
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_scalp_orderflow_endpoint_returns_source_fidelity(client):
    resp = client.get("/api/scalp-orderflow?symbol=BTCUSDT&timeframe=M5")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["symbol"] == "BTCUSDT"
    assert data["timeframe"] == "M5"
    assert data["source"] in {
        "REAL_AGGTRADE",
        "REAL_BYBIT_PUBLIC_TRADE",
        "CANDLE_PROXY",
        "UNAVAILABLE",
        "DELAYED_REAL",
        "MT5_TICK_PROXY",
    }
    assert "largeTradeEvents" in data
    assert "warnings" in data


def test_scalp_orderflow_proxy_labeled_when_real_flow_missing():
    payload = build_scalp_orderflow_payload(
        symbol="EURUSD",
        timeframe="M5",
        asset_type="forex",
    )
    assert payload["source"] in {"MT5_TICK_PROXY", "UNAVAILABLE"}
    assert payload["warnings"]


def test_scalp_orderflow_crypto_uses_real_trade_bucket_when_available(monkeypatch):
    monkeypatch.setattr(
        "scalp_orderflow._query_trade_buckets",
        lambda *_a, **_k: ([{"price_bucket": 65000.0, "buy_volume": 10, "sell_volume": 2, "total_volume": 12, "last_ts": 1_700_000_000.0}], "REAL_AGGTRADE"),
    )
    payload = build_scalp_orderflow_payload(symbol="BTCUSDT", timeframe="M5", asset_type="crypto")
    assert payload["source"] == "REAL_AGGTRADE"
    assert isinstance(payload["largeTradeEvents"], list)
