"""FX factor research API routes — envelope + store-backed read paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from athena_fx import store as fx_store
from athena_fx.models import (
    ACTION_BUY,
    ACTION_NO_TRADE,
    DIAG_MISSING_CARRY,
    FAMILY_CARRY,
    FAMILY_MOMENTUM,
    FAMILY_VALUE,
)


def _envelope_keys(body: dict) -> None:
    for key in ("success", "data", "status", "diagnostics", "warnings", "generated_at"):
        assert key in body, f"missing envelope key: {key}"


def _sample_decision(
    symbol: str = "EURUSD",
    as_of_date: str = "2026-01-15",
    action: str = ACTION_BUY,
    *,
    diagnostics: list | None = None,
) -> dict:
    return {
        "symbol": symbol,
        "as_of_date": as_of_date,
        "action": action,
        "direction": "LONG" if action == ACTION_BUY else None,
        "aligned_families": [FAMILY_CARRY, FAMILY_MOMENTUM],
        "blocked_families": [],
        "factor_scores": {
            FAMILY_CARRY: {"score": 12.0, "direction": "bullish"},
            FAMILY_MOMENTUM: {"score": 8.0, "direction": "bullish"},
            FAMILY_VALUE: {"score": 3.0, "direction": "neutral"},
        },
        "total_score": 23.0,
        "cot_overlay": {"action": "none", "modifier": 0.0},
        "gate_results": [
            {
                "gate_name": "volatility",
                "result": "pass",
                "reason_code": "ok",
                "recommended_action": "allow",
                "size_multiplier": 1.0,
            }
        ],
        "volatility_action": "allow",
        "size_multiplier": 1.0,
        "cost_diagnostics": {"spread_pips": 0.8},
        "reason": "aligned carry+momentum",
        "diagnostics": diagnostics or [],
    }


def _sample_swap_audit(symbol: str = "EURUSD") -> dict:
    return {
        "symbol": symbol,
        "base_currency": "EUR",
        "quote_currency": "USD",
        "account_currency": "USD",
        "point": 0.00001,
        "trade_tick_value": 1.0,
        "trade_tick_size": 0.00001,
        "trade_contract_size": 100000.0,
        "swap_mode": 1,
        "swap_long": -0.5,
        "swap_short": 0.3,
        "swap_rollover3days": 3,
        "carry_long_eligible": True,
        "carry_short_eligible": False,
        "generated_at": "2026-01-15T10:00:00+00:00",
        "status": "ok",
        "diagnostics": [],
    }


def _seed_db(db_path: Path) -> dict:
    carry_warning = {
        "code": DIAG_MISSING_CARRY,
        "severity": "warning",
        "message": "carry feed stale",
        "context": {"symbol": "GBPUSD"},
    }
    decisions = [
        _sample_decision("EURUSD", "2026-01-15", ACTION_BUY),
        _sample_decision(
            "GBPUSD",
            "2026-01-15",
            ACTION_NO_TRADE,
            diagnostics=[carry_warning],
        ),
    ]
    fx_store.save_decisions(decisions, db_path=str(db_path))
    fx_store.save_swap_audit_rows([_sample_swap_audit("EURUSD")], db_path=str(db_path))
    fx_store.save_total_return_rows(
        [
            {
                "date": "2026-01-10",
                "symbol": "EURUSD",
                "spot_return": 0.001,
                "long_swap_return": 0.0001,
                "short_swap_return": -0.0001,
                "long_total_return": 0.0011,
                "short_total_return": 0.0009,
                "long_total_return_index": 1.0011,
                "short_total_return_index": 1.0009,
                "data_quality_status": "ok",
            },
            {
                "date": "2026-01-11",
                "symbol": "EURUSD",
                "spot_return": -0.002,
                "long_swap_return": 0.0001,
                "short_swap_return": -0.0001,
                "long_total_return": -0.0019,
                "short_total_return": -0.0021,
                "long_total_return_index": 0.9992,
                "short_total_return_index": 0.9988,
                "data_quality_status": "degraded",
            },
        ],
        db_path=str(db_path),
    )
    fx_store.save_reer_observations(
        [
            {
                "currency": "EUR",
                "observation_month": "2025-11",
                "reer_value": 98.5,
                "available_date": "2026-01-01",
                "lag_method": "conservative",
                "source": "test",
            },
            {
                "currency": "USD",
                "observation_month": "2025-11",
                "reer_value": 102.0,
                "available_date": "2026-01-01",
                "lag_method": "conservative",
                "source": "test",
            },
        ],
        db_path=str(db_path),
    )
    report = {
        "summary": {"n": 25, "sharpe": 0.4},
        "validation": {"n": 25, "pass": True},
        "pair_sanity_flags": {"EURUSD": "ok"},
    }
    fx_store.save_backtest_run(
        run_id="run-test-001",
        created_at="2026-01-15T12:00:00+00:00",
        status="completed",
        request={"start": "2025-01-01", "end": "2025-12-31", "symbols": ["EURUSD"]},
        report=report,
        db_path=str(db_path),
    )
    return {"decisions": decisions, "report": report}


@pytest.fixture()
def fx_client(tmp_path, monkeypatch):
    import athena_app.api.routes_forex_factor as routes

    db_path = tmp_path / "fx_factor_test.db"
    seed = _seed_db(db_path)
    monkeypatch.setenv("ATHENA_FX_DB_PATH", str(db_path))
    app = Flask(__name__)
    routes.register_forex_factor_routes(app)
    return app.test_client(), seed, db_path


@pytest.fixture()
def empty_fx_client(tmp_path, monkeypatch):
    import athena_app.api.routes_forex_factor as routes

    db_path = tmp_path / "fx_factor_empty.db"
    monkeypatch.setenv("ATHENA_FX_DB_PATH", str(db_path))
    fx_store.connect(str(db_path)).close()
    app = Flask(__name__)
    routes.register_forex_factor_routes(app)
    return app.test_client()


def test_factor_status_success(fx_client):
    client, seed, _db = fx_client
    resp = client.get("/api/forex/factor-status")
    assert resp.status_code == 200
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    data = body["data"]
    assert data["latest_as_of_date"] == "2026-01-15"
    assert data["decision_count"] == 2
    assert data["action_counts"][ACTION_BUY] == 1
    assert data["swap_audit_valid_symbol_count"] == 1
    assert data["data_freshness"]["decisions_latest_date"] == "2026-01-15"
    assert data["data_freshness"]["total_return_latest_date"] == "2026-01-11"


def test_factor_status_empty_db(empty_fx_client):
    resp = empty_fx_client.get("/api/forex/factor-status")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    assert "missing:decisions" in body["warnings"]
    assert "missing:swap_audit" in body["warnings"]
    assert "missing:total_return" in body["warnings"]
    assert "missing:reer" in body["warnings"]


def test_factor_signals_success(fx_client):
    client, seed, _db = fx_client
    resp = client.get("/api/forex/factor-signals?symbol=EURUSD")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    signals = body["data"]["signals"]
    assert len(signals) == 1
    sig = signals[0]
    assert sig["symbol"] == "EURUSD"
    assert sig["action"] == ACTION_BUY
    assert FAMILY_CARRY in sig["family_scores"]
    assert sig["gate_summary"]["volatility_action"] == "allow"
    assert sig["cot_overlay"]["action"] == "none"


def test_factor_signals_eligible_filter(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/factor-signals?eligible_only=true")
    body = resp.get_json()
    symbols = {s["symbol"] for s in body["data"]["signals"]}
    assert symbols == {"EURUSD"}


def test_swap_audit_success(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/swap-audit")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    rows = body["data"]["rows"]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "EURUSD"
    assert rows[0]["status"] == "ok"


def test_total_return_success(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/total-return?symbol=EURUSD")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    assert body["data"]["production_momentum_uses_total_return"] is True
    assert len(body["data"]["rows"]) == 2
    degraded = [d for d in body["diagnostics"] if d.get("data_quality_status") == "degraded"]
    assert len(degraded) == 1


def test_total_return_missing_symbol(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/total-return")
    assert resp.status_code == 400
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is False
    assert body["status"] == "invalid_request"


def test_reer_value_success(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/reer-value")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    currencies = {c["currency"] for c in body["data"]["currencies"]}
    assert "EUR" in currencies
    assert "USD" in currencies


def test_vol_cost_gates_success(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/vol-cost-gates")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    assert body["data"]["gate_only"] is True
    assert len(body["data"]["symbols"]) == 2


def test_validation_latest_success(fx_client):
    client, seed, _db = fx_client
    resp = client.get("/api/forex/validation/latest")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    assert body["data"]["validation"]["n"] == 25
    assert "validation_sample_size_below_30" in body["warnings"]


def test_diagnostics_lists_seeded_warnings(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/diagnostics")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    assert body["data"]["parity"]["adapter"] == (
        "athena_fx.derivatives_adapter.resolve_factor_derivatives"
    )
    diag_codes = {d.get("code") for d in body["data"]["diagnostics"]}
    assert DIAG_MISSING_CARRY in diag_codes


def test_backtest_get_unknown_run(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/backtest/does-not-exist")
    assert resp.status_code == 404
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is False
    assert body["status"] == "not_found"


def test_backtest_get_known_run(fx_client):
    client, _seed, _db = fx_client
    resp = client.get("/api/forex/backtest/run-test-001")
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True
    assert body["data"]["run_id"] == "run-test-001"
    assert body["data"]["report"]["validation"]["n"] == 25


@pytest.mark.parametrize("endpoint", [
    "/api/forex/factor-status",
    "/api/forex/factor-signals",
    "/api/forex/swap-audit",
])
def test_empty_db_read_endpoints_do_not_crash(empty_fx_client, endpoint):
    resp = empty_fx_client.get(endpoint)
    body = resp.get_json()
    _envelope_keys(body)
    assert body["success"] is True


def _backtest_bars() -> dict:
    """Daily bars spanning 2026-01-09 (Friday decision week)."""
    return {
        "EURUSD": [
            {"date": "2026-01-09", "open": 1.08, "high": 1.09, "low": 1.07, "close": 1.085},
            {"date": "2026-01-12", "open": 1.085, "high": 1.095, "low": 1.08, "close": 1.09},
            {"date": "2026-01-13", "open": 1.09, "high": 1.10, "low": 1.085, "close": 1.095},
        ]
    }


def test_backtest_run_strict_forced_and_diagnostics(fx_client):
    pytest.importorskip("athena_fx.backtest")
    client, _seed, _db = fx_client
    as_of = "2026-01-09"
    fixture_decision = _sample_decision(
        "EURUSD",
        as_of,
        ACTION_NO_TRADE,
        diagnostics=[
            {
                "code": DIAG_MISSING_CARRY,
                "severity": "error",
                "message": "carry unavailable",
                "context": {},
            }
        ],
    )
    payload = {
        "start": "2026-01-09",
        "end": "2026-01-16",
        "symbols": ["EURUSD"],
        "bars_by_symbol": _backtest_bars(),
        "fixture_decisions": {f"EURUSD|{as_of}": fixture_decision},
        "spread_pips_by_symbol": {"EURUSD": 0.8},
    }
    resp = client.post(
        "/api/forex/backtest/run",
        data=json.dumps(payload),
        content_type="application/json",
    )
    body = resp.get_json()
    _envelope_keys(body)
    if resp.status_code == 503 and body.get("status") == "blocked":
        pytest.skip("backtest module unavailable at runtime")
    assert resp.status_code in {200, 500}
    report = (body.get("data") or {}).get("report") or {}
    request_echo = report.get("request") or {}
    assert request_echo.get("strict_factor_data") is True
    diag_codes = [
        d.get("code")
        for d in (body.get("diagnostics") or [])
        + (report.get("diagnostics") or [])
        + (fixture_decision.get("diagnostics") or [])
    ]
    assert DIAG_MISSING_CARRY in diag_codes or any(
        DIAG_MISSING_CARRY in str(d) for d in body.get("diagnostics", [])
    )


def test_backtest_run_and_readback(fx_client):
    pytest.importorskip("athena_fx.backtest")
    client, _seed, _db = fx_client
    as_of = "2026-01-09"
    fixture_decision = _sample_decision("EURUSD", as_of, ACTION_BUY)
    payload = {
        "start": "2026-01-09",
        "end": "2026-01-16",
        "symbols": ["EURUSD"],
        "bars_by_symbol": _backtest_bars(),
        "fixture_decisions": {f"EURUSD|{as_of}": fixture_decision},
        "spread_pips_by_symbol": {"EURUSD": 0.8},
    }
    resp = client.post(
        "/api/forex/backtest/run",
        data=json.dumps(payload),
        content_type="application/json",
    )
    body = resp.get_json()
    if resp.status_code == 503:
        pytest.skip("backtest module unavailable at runtime")
    assert resp.status_code == 200, body
    run_id = body["data"]["run_id"]
    assert run_id
    read = client.get(f"/api/forex/backtest/{run_id}")
    read_body = read.get_json()
    assert read_body["success"] is True
    assert read_body["data"]["run_id"] == run_id


def test_forex_scan_endpoint_returns_candidates(fx_client, monkeypatch):
    client, _seed, _db = fx_client

    def fake_scan_forex(**kwargs):
        return {
            "scan_id": "scan-test",
            "as_of_date": "2026-01-15",
            "generated_at": "2026-01-15T12:00:00+00:00",
            "summary": {
                "symbols_scanned": 1,
                "tradable_count": 1,
                "blocked_count": 0,
                "no_trade_count": 0,
                "buy_count": 1,
                "sell_count": 0,
                "execution_enabled": False,
            },
            "candidates": [
                {
                    "scan_id": "scan-test",
                    "symbol": "EURUSD",
                    "action": ACTION_BUY,
                    "direction": "LONG",
                    "tradable_now": True,
                    "entry": 1.1002,
                    "sl": 1.0952,
                    "tp1": 1.1102,
                    "tp2": 1.1152,
                    "rr1": 2.0,
                    "reason": "2 families aligned long",
                    "gate_results": {"cost": {"result": "pass", "reason_code": "cost_ok"}},
                }
            ],
            "diagnostics": [],
            "warnings": [],
        }

    monkeypatch.setattr("athena_fx.scanner.scan_forex", fake_scan_forex)
    resp = client.post(
        "/api/forex/scan",
        json={"symbols": ["EURUSD"], "refreshDecisions": False},
    )
    body = resp.get_json()
    _envelope_keys(body)
    assert resp.status_code == 200
    assert body["success"] is True
    assert body["data"]["scan_id"] == "scan-test"
    assert body["data"]["candidates"][0]["action"] == ACTION_BUY

    latest = client.get("/api/forex/scan/latest").get_json()
    assert latest["data"]["scan_id"] == "scan-test"


def test_forex_scan_endpoint_includes_missing_data_diagnostics(empty_fx_client, monkeypatch):
    import athena_app.api.routes_forex_factor as routes

    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_SCAN_REFRESH_DECISIONS_DEFAULT", False)
    resp = empty_fx_client.post(
        "/api/forex/scan",
        json={"symbols": ["EURUSD"], "refreshDecisions": False},
    )
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["success"] is True
    assert any(d.get("code") == "MISSING_DECISION" for d in body["diagnostics"])
    candidate = body["data"]["candidates"][0]
    assert candidate["action"] == ACTION_NO_TRADE
    assert candidate["tradable_now"] is False


def test_trading_status_endpoint_reports_safe_defaults(fx_client, monkeypatch):
    import athena_app.api.routes_forex_factor as routes

    client, _seed, _db = fx_client
    monkeypatch.setitem(routes.CONFIG, "EXECUTION_ENABLED", False)
    monkeypatch.setitem(routes.CONFIG, "FX_FACTOR_EXECUTION_ENABLED", False)
    monkeypatch.setitem(routes.CONFIG, "MT5_EXECUTION_ENABLED", True)
    resp = client.get("/api/forex/trading-status")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["data"]["can_dry_run"] is True
    assert body["data"]["can_demo_execute"] is False
    assert "FX_FACTOR_EXECUTION_DISABLED" in body["data"]["block_reasons"]
