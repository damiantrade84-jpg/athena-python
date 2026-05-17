from __future__ import annotations

import sys
from types import SimpleNamespace

from flask import Flask

from athena_app.api.routes_broker_status import register_broker_status_routes


def _client(config=None):
    app = Flask(__name__)
    register_broker_status_routes(
        app,
        SimpleNamespace(CONFIG=config or {"EXECUTION_ENABLED": True}),
    )
    return app.test_client(), app


def test_broker_status_routes_register_expected_methods():
    _client_obj, app = _client()

    methods_by_path = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    assert "GET" in methods_by_path["/api/mt5-status"]
    assert "GET" in methods_by_path["/api/mt5-positions"]
    assert "GET" in methods_by_path["/api/bybit-status"]
    assert "GET" in methods_by_path["/api/binance-status"]


def test_mt5_status_and_positions_use_read_only_executor_calls(monkeypatch):
    calls = []
    mt5_module = SimpleNamespace(
        mt5_get_account=lambda: calls.append("account") or {"balance": 1000},
        mt5_get_positions=lambda: calls.append("positions") or {
            "positions": [{"pair": "EUR/USD"}]
        },
    )
    monkeypatch.setitem(sys.modules, "mt5_executor", mt5_module)
    client, _app = _client({"EXECUTION_ENABLED": True})

    status = client.get("/api/mt5-status")
    positions = client.get("/api/mt5-positions")

    assert status.status_code == 200
    assert status.get_json()["connected"] is True
    assert status.get_json()["executionEnabled"] is True
    assert positions.status_code == 200
    assert positions.get_json()["positions"][0]["pair"] == "EUR/USD"
    assert calls == ["account", "positions", "positions"]


def test_bybit_and_legacy_binance_status_share_read_only_response(monkeypatch):
    bybit_module = SimpleNamespace(
        bybit_get_account=lambda: {"equity": 2500},
        bybit_get_positions=lambda: {"positions": [{"pair": "BTC/USDT"}]},
    )
    monkeypatch.setitem(sys.modules, "bybit_executor", bybit_module)
    client, _app = _client()

    bybit = client.get("/api/bybit-status")
    binance = client.get("/api/binance-status")

    assert bybit.status_code == 200
    assert bybit.get_json()["connected"] is True
    assert bybit.get_json()["positions"][0]["pair"] == "BTC/USDT"
    assert binance.status_code == 200
    assert binance.get_json()["positions"][0]["pair"] == "BTC/USDT"


def test_mt5_positions_error_preserves_503_shape(monkeypatch):
    mt5_module = SimpleNamespace(
        mt5_get_positions=lambda: {"error": True, "detail": "not connected"},
    )
    monkeypatch.setitem(sys.modules, "mt5_executor", mt5_module)
    client, _app = _client()

    resp = client.get("/api/mt5-positions")

    assert resp.status_code == 503
    assert resp.get_json() == {"positions": [], "error": "not connected"}
