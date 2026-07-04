"""Direct FX candidate execution endpoint remains dry-run/demo gated."""

from __future__ import annotations

from flask import Flask

from athena_app.api.routes_forex_factor import register_forex_factor_routes
from athena_fx.models import ACTION_BUY, ACTION_FLATTEN, ACTION_NO_TRADE


def _client(monkeypatch):
    app = Flask(__name__)
    register_forex_factor_routes(app)
    return app.test_client()


def _candidate(**overrides) -> dict:
    base = {
        "scan_id": "scan-1",
        "symbol": "EURUSD",
        "action": ACTION_BUY,
        "direction": "LONG",
        "tradable_now": True,
        "factor_eligible": True,
        "execution_eligible": True,
        "entry": 1.1002,
        "sl": 1.0952,
        "tp1": 1.1102,
        "tp2": 1.1152,
        "rr1": 2.0,
        "target_weight": 0.02,
        "size_multiplier": 1.0,
        "aligned_families": ["carry", "momentum"],
        "blocked_families": [],
        "family_scores": {},
        "gate_results": {
            "cost": {"result": "pass", "reason_code": "cost_ok"},
            "volatility": {"result": "pass", "reason_code": "vol_ok"},
            "data_quality": {"result": "pass", "reason_code": "data_ok"},
        },
        "reason": "2 families aligned long",
        "last_factor_decision_date": "2026-01-15",
    }
    base.update(overrides)
    return base


def test_execute_candidate_dry_run_succeeds_without_broker(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.post(
        "/api/forex/execute-candidate",
        json={"candidate": _candidate(), "dryRun": True, "confirm": True},
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["dry_run"] is True
    assert body["data"]["reason"] == "dry_run"
    assert body["data"]["signal"]["engine"] == "FX_FACTOR"


def test_execute_candidate_rejects_no_trade() -> None:
    client = _client(None)

    response = client.post(
        "/api/forex/execute-candidate",
        json={"candidate": _candidate(action=ACTION_NO_TRADE, tradable_now=False), "dryRun": True},
    )

    body = response.get_json()
    assert response.status_code == 400
    assert body["success"] is False
    assert body["diagnostics"][0]["code"] == "ACTION_NOT_EXECUTABLE"


def test_execute_candidate_rejects_failed_gate() -> None:
    client = _client(None)
    candidate = _candidate(
        gate_results={"cost": {"result": "fail", "reason_code": "missing_spread"}},
        reason="gate failure: cost (missing_spread)",
    )

    response = client.post(
        "/api/forex/execute-candidate",
        json={"candidate": candidate, "dryRun": True},
    )

    body = response.get_json()
    assert response.status_code == 403
    assert body["success"] is False
    assert body["diagnostics"][0]["code"] == "HARD_GATE_FAILED"


def test_execute_candidate_real_execution_blocked_when_fx_flag_disabled(monkeypatch) -> None:
    import athena_app.api.routes_forex_factor as routes

    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_EXECUTION_ENABLED", False)
    client = _client(None)

    response = client.post(
        "/api/forex/execute-candidate",
        json={"candidate": _candidate(), "dryRun": False, "confirm": True},
    )

    body = response.get_json()
    assert response.status_code == 403
    assert body["success"] is False
    assert body["diagnostics"][0]["code"] == "FX_FACTOR_EXECUTION_DISABLED"


def test_execute_candidate_real_execution_requires_validation_when_configured(monkeypatch) -> None:
    import athena_app.api.routes_forex_factor as routes

    monkeypatch.setitem(routes.CONFIG, "EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "MT5_EXECUTION_ENABLED", True)
    monkeypatch.setitem(routes.CONFIG, "KILL_SWITCH", False)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_REQUIRE_VALIDATION_FOR_EXECUTION", True)
    monkeypatch.setattr(routes, "_latest_completed_backtest", lambda: None)
    client = _client(monkeypatch)

    response = client.post(
        "/api/forex/execute-candidate",
        json={"candidate": _candidate(), "dryRun": False, "confirm": True},
    )

    body = response.get_json()
    assert response.status_code == 403
    assert body["success"] is False
    assert body["diagnostics"][0]["code"] == "FX_FACTOR_VALIDATION_REQUIRED"


def test_execute_candidate_flatten_uses_close_path(monkeypatch) -> None:
    calls: list[str] = []

    def fake_close(symbol, *, dry_run=False, deps=None):
        calls.append(f"{symbol}:{dry_run}")
        return {"executed": False, "reason": "dry_run_close", "symbol": symbol, "dry_run": dry_run}

    monkeypatch.setattr("athena_fx.execution.close_fx_symbol_position", fake_close)
    client = _client(monkeypatch)

    response = client.post(
        "/api/forex/execute-candidate",
        json={"candidate": _candidate(action=ACTION_FLATTEN, tradable_now=False), "dryRun": True},
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["data"]["reason"] == "dry_run_close"
    assert calls == ["EURUSD:True"]
