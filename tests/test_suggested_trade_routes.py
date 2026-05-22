"""Tests for suggested trade API routes."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from athena_app.api.routes_suggested_trades import register_suggested_trade_routes
from config import CONFIG


def _valid_flag_body(**overrides):
    body = {
        "symbol": "EURUSD",
        "display": "EUR/USD",
        "source": "ai_chart_review",
        "suggestedTradePlan": {
            "schemaVersion": "suggested_trade_plan.v1",
            "armable": True,
            "source": "ai_chart_review",
            "symbol": "EURUSD",
            "direction": "SHORT",
            "action": "WAIT_FOR_ZONE",
            "triggerType": "PULLBACK_TO_ZONE",
            "zoneLow": 1.084,
            "zoneHigh": 1.086,
            "expiresInSeconds": 900,
        },
        "aiReviewSummary": {"humanAction": "wait"},
        "createdAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    body.update(overrides)
    return body


def _mock_fetch_candles(symbol, tf, limit=5):
    return {
        "candles": [
            {"t": "2026-05-22T12:00:00+00:00", "o": 1.085, "h": 1.086, "l": 1.084, "c": 1.0855},
        ]
    }


@pytest.fixture()
def app_client(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="athena_suggested_trades_"))
    active = tmp_dir / "active.json"
    events = tmp_dir / "events.jsonl"
    monkeypatch.setattr("suggested_trade_monitor.DEFAULT_ACTIVE_PATH", active)
    monkeypatch.setattr("suggested_trade_monitor.DEFAULT_EVENTS_PATH", events)
    monkeypatch.setattr("athena_app.api.routes_suggested_trades.DEFAULT_ACTIVE_PATH", active)
    monkeypatch.setattr("athena_app.api.routes_suggested_trades.DEFAULT_EVENTS_PATH", events)

    app = Flask(__name__)
    cfg = dict(CONFIG)
    monitor_cfg = dict(cfg.get("SUGGESTED_TRADE_MONITOR") or {})
    monitor_cfg["RUNNER_ENABLED"] = False
    cfg["SUGGESTED_TRADE_MONITOR"] = monitor_cfg
    register_suggested_trade_routes(
        app,
        SimpleNamespace(
            CONFIG=cfg,
            json_safe=lambda v: v,
            log=__import__("logging").getLogger("test"),
            fetch_candles=_mock_fetch_candles,
            live_prices={"EURUSD": {"price": 1.0855}},
            live_prices_lock=None,
        ),
    )
    yield app.test_client(), active


def test_flag_route_stores_valid_watch(app_client):
    client, active = app_client
    resp = client.post("/api/suggested-trades/flag", json=_valid_flag_body())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["alert_only"] is True
    assert active.exists()
    stored = json.loads(active.read_text(encoding="utf-8"))
    assert len(stored["watches"]) == 1


def test_list_route_returns_active_watches(app_client):
    client, _ = app_client
    client.post("/api/suggested-trades/flag", json=_valid_flag_body())
    resp = client.get("/api/suggested-trades")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] >= 1
    assert data["alert_only"] is True
    assert "counts" in data
    assert "runner" in data
    assert data["runner"]["alertOnly"] is True


def test_status_route_returns_runner_and_counts(app_client):
    client, _ = app_client
    client.post("/api/suggested-trades/flag", json=_valid_flag_body())
    resp = client.get("/api/suggested-trades/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "counts" in data
    assert "runner" in data
    assert data["runner"]["alertOnly"] is True


def test_evaluate_now_includes_runner_and_counts(app_client):
    client, _ = app_client
    client.post("/api/suggested-trades/flag", json=_valid_flag_body())
    resp = client.post("/api/suggested-trades/evaluate-now")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["evaluation"]["alert_only"] is True
    assert "counts" in data
    assert "runner" in data
    watches = data["watches"]
    assert any(w.get("status") == "READY_FOR_REVIEW" for w in watches)


def test_routes_module_has_no_execution_calls():
    source = Path(__file__).resolve().parents[1] / "athena_app/api/routes_suggested_trades.py"
    text = source.read_text(encoding="utf-8")
    assert "quick_execute" not in text
    assert "scalp_execute" not in text
    assert "execution.py" not in text


def test_cancel_route_cancels_watch(app_client):
    client, active = app_client
    flag = client.post("/api/suggested-trades/flag", json=_valid_flag_body()).get_json()
    watch_id = flag["watch"]["watch_id"]
    resp = client.post(f"/api/suggested-trades/{watch_id}/cancel")
    assert resp.status_code == 200
    stored = json.loads(active.read_text(encoding="utf-8"))
    assert stored["watches"][0]["status"] == "CANCELLED"


def test_evaluate_now_marks_zone_reached(app_client):
    client, active = app_client
    client.post("/api/suggested-trades/flag", json=_valid_flag_body())
    resp = client.post("/api/suggested-trades/evaluate-now")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["evaluation"]["alert_only"] is True
    watches = data["watches"]
    assert any(w.get("status") == "READY_FOR_REVIEW" for w in watches)
