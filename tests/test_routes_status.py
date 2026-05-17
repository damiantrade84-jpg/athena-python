from __future__ import annotations

from types import SimpleNamespace
import time

from flask import Flask

from athena_app.api.routes_status import register_status_routes


class _Log:
    def debug(self, *_args, **_kwargs):
        pass


def _runtime(**overrides):
    state = {
        "all_pairs": [
            {"source": "mt5"},
            {"source": "binance"},
            {"source": "mt5"},
        ],
        "active_pairs": [{"source": "mt5"}],
        "last_scan": {"signals": []},
        "micro_cache": {
            "BTCUSDT": {
                "_updated_ts": time.time(),
                "order_book_imbalance": 0.25,
                "liquidity_pressure": "buy",
            }
        },
        "kill_switch": False,
    }
    runtime = SimpleNamespace(
        CONFIG={"MICROSTRUCTURE_FEEDS_ENABLED": True},
        AUDIT_DB=":memory:",
        all_pairs=lambda: state["all_pairs"],
        active_pairs=lambda: state["active_pairs"],
        kill_switch=lambda: state["kill_switch"],
        last_scan_results=lambda: state["last_scan"],
        mt5_connection_health=lambda: {
            "available": True,
            "connected": True,
            "detail": "ok",
        },
        micro_cache=lambda: state["micro_cache"],
        json_safe=lambda value: value,
        signal_stability_index=lambda **_kwargs: {"signals": 0},
        log=_Log(),
        state=state,
    )
    for key, value in overrides.items():
        setattr(runtime, key, value)
    return runtime


def _client(runtime):
    app = Flask(__name__)
    register_status_routes(app, runtime)
    return app.test_client(), app


def test_status_routes_register_expected_methods():
    _client_obj, app = _client(_runtime())

    methods_by_path = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    assert "GET" in methods_by_path["/api/health"]
    assert "GET" in methods_by_path["/api/last-scan"]
    assert "GET" in methods_by_path["/api/conductor/last"]
    assert "GET" in methods_by_path["/api/kimi/conductor/last"]
    assert "GET" in methods_by_path["/api/conductor/pairs"]
    assert "GET" in methods_by_path["/api/signal-stability"]
    assert "GET" in methods_by_path["/api/debug/routes"]
    assert "GET" in methods_by_path["/api/microstructure-health"]


def test_health_uses_runtime_getters():
    runtime = _runtime()
    client, _app = _client(runtime)

    resp = client.get("/api/health")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["dataSources"] == ["binance", "mt5"]
    assert data["activePairs"] == 1
    assert data["mt5"]["connected"] is True


def test_last_scan_reflects_runtime_state_changes():
    runtime = _runtime()
    client, _app = _client(runtime)

    first = client.get("/api/last-scan").get_json()
    runtime.state["last_scan"] = {
        "success": True,
        "scannedAt": "2026-05-05T12:00:00+00:00",
        "signals": [],
    }
    second = client.get("/api/last-scan").get_json()

    assert first["available"] is False
    assert second["available"] is True
    assert second["scannedAt"] == "2026-05-05T12:00:00+00:00"


def test_last_scan_limit_slices_signals_without_changing_default_payload():
    runtime = _runtime()
    runtime.state["last_scan"] = {
        "success": True,
        "scannedAt": "2026-05-13T12:00:00+00:00",
        "signals": [{"pair": f"P{i}"} for i in range(10)],
    }
    client, _app = _client(runtime)

    full = client.get("/api/last-scan").get_json()
    limited = client.get("/api/last-scan?limit=3").get_json()

    assert len(full["signals"]) == 10
    assert len(limited["signals"]) == 3
    assert limited["totalSignals"] == 10
    assert limited["signalsTruncated"] is True


def test_microstructure_health_uses_runtime_cache():
    runtime = _runtime()
    client, _app = _client(runtime)

    resp = client.get("/api/microstructure-health")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["feeds_enabled"] is True
    assert data["symbol_count"] == 1
    assert data["symbols"][0]["symbol"] == "BTCUSDT"


def test_debug_routes_lists_registered_api_routes():
    client, _app = _client(_runtime())

    resp = client.get("/api/debug/routes")

    assert resp.status_code == 200
    paths = {row["path"] for row in resp.get_json()["routes"]}
    assert "/api/health" in paths
    assert "/api/debug/routes" in paths
