from __future__ import annotations

from types import SimpleNamespace
import threading

from flask import Flask

from athena_app.api.routes_live_dashboard import (
    _ld_build_engine_a_row,
    _ld_build_engine_b_row,
    register_live_dashboard_routes,
)


class _Log:
    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def _runtime(**overrides):
    runtime = SimpleNamespace(
        CONFIG={
            "LIVE_DASHBOARD": {"ENABLED": True, "DEFAULT_SYMBOLS": [], "MAX_SYMBOLS": 8},
            "PAPER_SOAK": {"ENABLED": True},
            "REAL_ORDERS_ALLOWED": False,
        },
        AUDIT_DB=":memory:",
        ALL_PAIRS=[],
        ACTIVE_PAIRS=[],
        live_prices={},
        live_prices_lock=threading.Lock(),
        live_dashboard_scalp_cache={},
        live_dashboard_scalp_cache_lock=threading.Lock(),
        fetch_candles=lambda *_args, **_kwargs: [],
        scan_candle_limits=lambda: {"H1": 100, "H4": 100, "D1": 100},
        get_candle_fetch_meta=lambda *_args, **_kwargs: {},
        get_candle_builder=lambda: None,
        json_safe=lambda value: value,
        mt5_connection_health=lambda: {"available": True, "connected": False},
        engine_b_cache_get=lambda _key: None,
        resolve_pair_from_signal=lambda _sig: None,
        get_min_confluence_threshold=lambda _pair: 0.0,
        last_scan_results=lambda: {},
        binance_ws=lambda: None,
        kill_switch=lambda: False,
        log=_Log(),
    )
    for key, value in overrides.items():
        setattr(runtime, key, value)
    return runtime


def _client(runtime):
    app = Flask(__name__)
    register_live_dashboard_routes(app, runtime)
    return app.test_client(), app


def test_live_dashboard_routes_register_expected_methods():
    _client_obj, app = _client(_runtime())

    methods_by_path = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    assert "GET" in methods_by_path["/api/live-feed-diagnostics"]
    assert "POST" in methods_by_path["/api/live-feed-diagnostics"]
    assert "GET" in methods_by_path["/api/live-dashboard/snapshot"]
    assert "POST" in methods_by_path["/api/live-dashboard/paper-execute"]
    assert "GET" in methods_by_path["/api/shadow-signals"]


def test_snapshot_disabled_returns_503_without_runtime_side_effects():
    runtime = _runtime(CONFIG={"LIVE_DASHBOARD": {"ENABLED": False}})
    client, _app = _client(runtime)

    resp = client.get("/api/live-dashboard/snapshot")

    assert resp.status_code == 503
    assert resp.get_json()["error"] == "Live Dashboard disabled in config"


def test_paper_execute_blocks_when_real_orders_allowed():
    runtime = _runtime(
        CONFIG={
            "LIVE_DASHBOARD": {"ENABLED": True},
            "PAPER_SOAK": {"ENABLED": True},
            "REAL_ORDERS_ALLOWED": True,
        }
    )
    client, _app = _client(runtime)

    resp = client.post(
        "/api/live-dashboard/paper-execute",
        json={"symbol": "EUR/USD", "direction": "LONG"},
    )

    assert resp.status_code == 403
    assert "REAL_ORDERS_ALLOWED is true" in resp.get_json()["error"]


def test_live_feed_diagnostics_empty_runtime_is_read_only_success():
    client, _app = _client(_runtime())

    resp = client.get("/api/live-feed-diagnostics")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["count"] == 0
    assert data["tradesPlaced"] == 0


def test_ld_build_engine_b_row_maps_fallback_level_fields():
    row = _ld_build_engine_b_row(
        {
            "current_price": 65000.0,
            "final_stop_loss": 63000.0,
            "final_take_profit": 68000.0,
            "rr_used_for_gate": 1.8,
            "confidence_score": 4.5,
            "min_score_used": 4.0,
            "max_possible": 6.0,
            "direction": "LONG",
            "structural_verdict": "CLEAR",
            "confidence": {"passed": True},
        }
    )

    assert row["entry"] == 65000.0
    assert row["sl"] == 63000.0
    assert row["tp"] == 68000.0
    assert row["rr"] == 1.8
    assert row["score"] == 4.5
    assert row["threshold"] == 4.0
    assert row["maxScore"] == 6.0
    assert row["confidencePassed"] is True


def test_ld_build_engine_b_row_preserves_legacy_fields():
    row = _ld_build_engine_b_row(
        {
            "entry": 1.085,
            "sl": 1.08,
            "tp": 1.095,
            "rr": 2.0,
            "score": 5.0,
            "min_score": 4.5,
            "max_score": 6.0,
            "direction": "SHORT",
            "structural_verdict": "CLEAR",
        }
    )

    assert row["entry"] == 1.085
    assert row["sl"] == 1.08
    assert row["tp"] == 1.095
    assert row["rr"] == 2.0
    assert row["score"] == 5.0
    assert row["threshold"] == 4.5
    assert row["maxScore"] == 6.0


def test_ld_build_engine_b_row_emits_gate_score_and_floor():
    row = _ld_build_engine_b_row(
        {
            "confidence": {
                "passed": False,
                "gate_score": 4.0,
                "gate_max_possible": 5.0,
                "score": 5.5,
                "min_score_scaled": 4.5,
            },
            "direction": "LONG",
            "structural_verdict": "CLEAR",
        }
    )

    assert row["gateScore"] == 4.0
    assert row["gateMax"] == 5.0
    assert row["totalScore"] == 5.5
    assert row["threshold"] == 4.5


def test_ld_build_engine_a_row_v3_uses_confluence_threshold_and_decision():
    row = _ld_build_engine_a_row(
        {
            "engine": "ENGINE_A_V3",
            "contractVersion": "3.1.0",
            "confluenceScore": 2.4,
            "confluenceThreshold": 2.2,
            "decision": "WATCH",
            "qualified": False,
            "direction": "LONG",
        }
    )

    assert row["threshold"] == 2.2
    assert row["passed"] is False
    assert row["decision"] == "WATCH"


def test_ld_build_engine_a_row_no_threshold_fails_closed():
    row = _ld_build_engine_a_row(
        {
            "confluenceScore": 2.0,
            "direction": "LONG",
        }
    )

    assert row["passed"] is False
