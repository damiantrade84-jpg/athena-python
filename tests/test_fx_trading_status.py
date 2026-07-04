"""FX trading readiness endpoint."""

from __future__ import annotations

from flask import Flask

from athena_app.api.routes_forex_factor import register_forex_factor_routes


def test_trading_status_defaults_are_safe(monkeypatch) -> None:
    import athena_app.api.routes_forex_factor as routes

    monkeypatch.setitem(routes.CONFIG, "EXECUTION_ENABLED", False)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_EXECUTION_ENABLED", False)
    monkeypatch.setitem(routes.CONFIG, "MT5_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "KILL_SWITCH", False)

    app = Flask(__name__)
    register_forex_factor_routes(app)
    response = app.test_client().get("/api/forex/trading-status")

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    status = body["data"]
    assert status["can_dry_run"] is True
    assert status["can_demo_execute"] is False
    assert "EXECUTION_DISABLED" in status["block_reasons"]
    assert "FX_FACTOR_EXECUTION_DISABLED" in status["block_reasons"]


def test_trading_status_detects_kill_switch(monkeypatch) -> None:
    import athena_app.api.routes_forex_factor as routes

    monkeypatch.setitem(routes.CONFIG, "EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "MT5_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "KILL_SWITCH", True)

    app = Flask(__name__)
    register_forex_factor_routes(app)
    response = app.test_client().get("/api/forex/trading-status")

    status = response.get_json()["data"]
    assert status["kill_switch"] is True
    assert status["can_demo_execute"] is False
    assert "KILL_SWITCH" in status["block_reasons"]
