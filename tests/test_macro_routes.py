"""Offline tests for the read-only macro API routes (Flask test client, no network)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

import macro.store as store_mod
from athena_app.api.routes_macro import register_macro_routes
from macro.store import MacroEventStore


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="macro_routes_")
    store = MacroEventStore(os.path.join(d, "m.db"))
    # Event 15 min out → guard reports LOCKOUT (pre-release window) at real "now".
    T = datetime.now(timezone.utc) + timedelta(minutes=15)
    store.upsert_event(
        {
            "id": "FOMC_RATE_DECISION:routes-test",
            "event_type": "FOMC_RATE_DECISION",
            "currency": "USD",
            "title": "FOMC rate decision",
            "scheduled_time_utc": T.isoformat(),
            "press_conference_time_utc": (T + timedelta(minutes=30)).isoformat(),
            "status": "SCHEDULED",
            "source": "fed_fomc_calendar",
        }
    )
    monkeypatch.setattr(store_mod, "_default_store", store, raising=False)
    app = Flask(__name__)
    register_macro_routes(app)
    return app.test_client()


def test_global_state_route_reports_lockout(client):
    resp = client.get("/api/macro/state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["state"]["macroRisk"] == "LOCKOUT"
    assert body["state"]["blockNewTrades"] is True


def test_symbol_state_route_affected_vs_unaffected(client):
    affected = client.get("/api/macro/state?symbol=XAU/USD").get_json()
    assert affected["state"]["affected"] is True
    assert affected["state"]["blockNewTrades"] is True

    unaffected = client.get("/api/macro/state?symbol=DAX 40").get_json()
    assert unaffected["state"]["affected"] is False
    assert unaffected["state"]["blockNewTrades"] is False


def test_events_route_returns_next_event(client):
    body = client.get("/api/macro/events").get_json()
    assert body["success"] is True
    assert body["count"] >= 1
    assert body["nextEvent"] is not None
    assert body["nextEvent"]["event_type"] == "FOMC_RATE_DECISION"
