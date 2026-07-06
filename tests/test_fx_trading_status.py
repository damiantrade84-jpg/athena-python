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


def test_trading_status_blocks_demo_when_validation_required_but_missing(monkeypatch) -> None:
    import athena_app.api.routes_forex_factor as routes

    monkeypatch.setitem(routes.CONFIG, "EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "MT5_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "KILL_SWITCH", False)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_REQUIRE_VALIDATION_FOR_EXECUTION", True)
    monkeypatch.setattr(routes, "_fx_backtest_validation_state", lambda: (False, None, None))

    app = Flask(__name__)
    register_forex_factor_routes(app)
    response = app.test_client().get("/api/forex/trading-status")

    status = response.get_json()["data"]
    assert status["validation_required"] is True
    assert status["validation_pass"] is False
    assert status["can_demo_execute"] is False
    assert "FX_FACTOR_VALIDATION_REQUIRED" in status["block_reasons"]


def test_validation_pass_recomputed_from_legacy_family_counts() -> None:
    import athena_app.api.routes_forex_factor as routes

    legacy = {
        "family_trade_counts": {"carry": 51, "momentum": 88, "value": 84},
        "n_warning": False,
    }
    assert routes._validation_pass_from_snapshot(legacy, run_status="completed") is True
    enriched = routes._enrich_validation_snapshot(legacy, run_status="completed")
    assert enriched is not None
    assert enriched["pass"] is True


def test_trading_status_allows_demo_when_legacy_validation_counts_qualify(monkeypatch) -> None:
    import athena_app.api.routes_forex_factor as routes

    monkeypatch.setitem(routes.CONFIG, "EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "MT5_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "KILL_SWITCH", False)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_REQUIRE_VALIDATION_FOR_EXECUTION", True)
    monkeypatch.setattr(
        routes,
        "_latest_completed_backtest",
        lambda: {
            "run_id": "legacy-run",
            "status": "completed",
            "report": {
                "validation": {
                    "family_trade_counts": {"carry": 51, "momentum": 88, "value": 84},
                    "n_warning": False,
                }
            },
        },
    )

    class _FakeDeps:
        def get_positions(self):
            return [], {}

        def get_account(self):
            return {"balance": 10000}

        def get_mt5_trade_mode(self):
            return 0

    monkeypatch.setattr(
        "athena_fx.execution.default_fx_execution_deps",
        lambda: _FakeDeps(),
    )

    app = Flask(__name__)
    register_forex_factor_routes(app)
    response = app.test_client().get("/api/forex/trading-status")

    status = response.get_json()["data"]
    assert status["validation_pass"] is True
    assert status["validation_run_id"] == "legacy-run"
    assert status["can_demo_execute"] is True
    assert "FX_FACTOR_VALIDATION_REQUIRED" not in status["block_reasons"]
