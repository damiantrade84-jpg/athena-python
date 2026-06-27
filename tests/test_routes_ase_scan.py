"""Tests for /api/ase-scan ingest wiring."""

from __future__ import annotations

from flask import Flask

from athena_app.api import routes_ase


def _register_and_call(json_body, monkeypatch):
    calls: list[dict] = []

    def fake_run_ase_scan(**kwargs):
        calls.append(kwargs)
        return {"success": True, "signals": [], "executions": []}

    def fake_run_ase_dual_horizon_scan(**kwargs):
        calls.append({"dual": True, "kwargs": kwargs})
        return {"success": True, "signals": [], "executions": []}

    monkeypatch.setattr(routes_ase, "run_ase_scan", fake_run_ase_scan)
    monkeypatch.setattr(routes_ase, "run_ase_dual_horizon_scan", fake_run_ase_dual_horizon_scan)

    app = Flask(__name__)
    routes_ase.register_ase_routes(app)
    response = app.test_client().post("/api/ase-scan", json=json_body)
    return response, calls


def test_ase_scan_default_uses_scan_default_sources(monkeypatch):
    response, calls = _register_and_call({}, monkeypatch)
    assert response.status_code == 200
    assert len(calls) == 1
    assert "dual" in calls[0]
    # When no ingest controls are sent, the route omits ingest_sources so the
    # scan function uses its own DEFAULT_SCAN_INGEST_SOURCES.
    assert "ingest_sources" not in calls[0]["kwargs"]


def test_ase_scan_skip_ingest(monkeypatch):
    response, calls = _register_and_call({"skipIngest": True}, monkeypatch)
    assert response.status_code == 200
    assert calls[0]["kwargs"]["ingest_sources"] == ()


def test_ase_scan_custom_ingest_sources(monkeypatch):
    response, calls = _register_and_call({"ingestSources": ["binance", "bybit"]}, monkeypatch)
    assert response.status_code == 200
    assert calls[0]["kwargs"]["ingest_sources"] == ("binance", "bybit")


def test_ase_scan_intraday(monkeypatch):
    response, calls = _register_and_call({"horizon": "intraday"}, monkeypatch)
    assert response.status_code == 200
    assert "dual" not in calls[0]
    assert calls[0]["horizon"] == "intraday"
    assert "ingest_sources" not in calls[0]


def test_ase_scan_intraday_skip_ingest(monkeypatch):
    response, calls = _register_and_call({"horizon": "intraday", "skipIngest": True}, monkeypatch)
    assert response.status_code == 200
    assert calls[0]["ingest_sources"] == ()
