from __future__ import annotations

from types import SimpleNamespace
import threading

from flask import Flask

from athena_app.api.routes_market_data import register_market_data_routes


class _Log:
    def error(self, *_args, **_kwargs):
        pass


def _runtime(**overrides):
    pair = {
        "symbol": "BTCUSDT",
        "display": "BTC/USDT",
        "type": "crypto",
        "source": "binance",
        "enabled": True,
    }
    jse_pair = {
        "symbol": "NPN.JSE",
        "display": "Naspers",
        "type": "stock",
        "source": "eodhd",
        "enabled": True,
    }
    runtime = SimpleNamespace(
        CONFIG={},
        ALL_PAIRS=[pair, jse_pair],
        ACTIVE_PAIRS=[pair],
        ETF_PAIRS=[],
        JSE_PAIRS=[jse_pair],
        disabled_pairs=set(),
        live_prices={"BTC/USDT": {"price": 100.0}},
        live_prices_lock=threading.Lock(),
        fetch_yield_curve=lambda: {"success": True, "spread": 1.25},
        http_requests=None,
        fetch_candles=lambda _pair, _tf, _limit: [
            {
                "time": "2026-05-05T10:00:00",
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 99,
            }
        ],
        fetch_eodhd=lambda *_args, **_kwargs: [],
        extract_candles=lambda candles: candles,
        merge_forex_forming_ws=lambda candles, *_args, **_kwargs: (candles, None),
        resample_from_h1=lambda *_args, **_kwargs: [],
        forex_h4_resample_offset_hours=lambda: 2.0,
        eodhd_ticker_for_pair=lambda p: p.get("symbol"),
        json_safe=lambda value: value,
        log=_Log(),
    )
    for key, value in overrides.items():
        setattr(runtime, key, value)
    return runtime


def _client(runtime):
    app = Flask(__name__)
    register_market_data_routes(app, runtime)
    return app.test_client()


def test_market_data_routes_register_expected_methods():
    app = Flask(__name__)
    register_market_data_routes(app, _runtime())

    methods_by_path = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    assert "GET" in methods_by_path["/api/market-hours"]
    assert "GET" in methods_by_path["/api/prices"]
    assert "GET" in methods_by_path["/api/candles"]
    assert "POST" in methods_by_path["/api/news-sentiment"]


def test_prices_and_pairs_are_served_from_runtime_state():
    client = _client(_runtime())

    prices = client.get("/api/prices")
    pairs = client.get("/api/pairs")

    assert prices.status_code == 200
    assert prices.get_json()["prices"]["BTC/USDT"]["price"] == 100.0
    assert pairs.status_code == 200
    data = pairs.get_json()
    assert data["total"] == 1
    assert data["active"] == 1
    assert data["groups"]["Crypto"][0]["sym"] == "BTCUSDT"


def test_yield_curve_and_candles_use_runtime_fetchers():
    client = _client(_runtime())

    yc = client.get("/api/yield-curve")
    candles = client.get("/api/candles?symbol=BTCUSDT&tf=H4")

    assert yc.status_code == 200
    assert yc.get_json()["spread"] == 1.25
    assert candles.status_code == 200
    payload = candles.get_json()
    assert payload["symbol"] == "BTCUSDT"
    assert payload["candles"][0]["t"] == "2026-05-05T10:00:00Z"
    assert payload["candles"][0]["v"] == 99.0


def test_bulk_prices_uses_runtime_http_client(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return [{"code": "SPY.US", "close": 500, "volume": 123}]

    http = SimpleNamespace(get=lambda *_args, **_kwargs: _Resp())
    monkeypatch.setenv("EODHD_KEY", "test-key")
    client = _client(_runtime(http_requests=http))

    resp = client.get("/api/bulk-prices?symbols=SPY.US,QQQ.US")

    assert resp.status_code == 200
    assert resp.get_json()["prices"]["SPY.US"]["price"] == 500
