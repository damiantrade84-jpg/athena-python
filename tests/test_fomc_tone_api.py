"""Offline tests for the FOMC tone API route (Flask test client, no network)."""

from __future__ import annotations

import os
import tempfile

import pytest
from flask import Flask

import macro.store as store_mod
import macro.tone_store as tone_store_mod
from athena_app.api.routes_macro import register_macro_routes
from macro.fomc_tone import process_event
from macro.store import MacroEventStore
from macro.tone_store import MacroToneStore

BASE = (
    "The Committee seeks to achieve maximum employment and inflation at the rate of two "
    "percent over the longer run. The Committee will continue to monitor incoming data."
)


@pytest.fixture()
def seeded(monkeypatch):
    d = tempfile.mkdtemp(prefix="tone_api_")
    ev_store = MacroEventStore(os.path.join(d, "m.db"))
    t_store = MacroToneStore(os.path.join(d, "m.db"))
    monkeypatch.setattr(store_mod, "_default_store", ev_store, raising=False)
    monkeypatch.setattr(tone_store_mod, "_default_tone_store", t_store, raising=False)
    return ev_store, t_store


def _seed_tone(ev_store, t_store):
    ev_store.upsert_event({
        "id": "FOMC_STATEMENT:api",
        "event_type": "FOMC_STATEMENT", "currency": "USD",
        "title": "FOMC statement", "scheduled_time_utc": "2026-06-17T18:00:00+00:00",
        "status": "RELEASED", "source": "fed_press_monetary_rss",
        "raw_text": BASE + " Inflation remains elevated.",
        "previous_lower": 5.0, "previous_upper": 5.25,
        "actual_lower": 5.25, "actual_upper": 5.5,
    })
    process_event("FOMC_STATEMENT:api", store=ev_store, tone_store=t_store)


def _client():
    app = Flask(__name__)
    register_macro_routes(app)
    return app.test_client()


def test_fomc_tone_route_returns_expected_shape(seeded):
    ev_store, t_store = seeded
    _seed_tone(ev_store, t_store)
    body = _client().get("/api/macro/fomc-tone").get_json()
    assert body["success"] is True
    tone = body["tone"]
    assert tone is not None
    for key in ("eventId", "toneLabel", "toneScore", "confidence", "componentScores",
                "evidence", "warnings", "executionUse", "marketExpectationSurpriseBps"):
        assert key in tone
    assert tone["executionUse"] == "CONTEXT_ONLY"
    assert tone["marketExpectationSurpriseBps"] is None
    assert isinstance(tone["componentScores"], dict)
    assert "inflationLanguage" in tone["componentScores"]
    assert isinstance(tone["evidence"], list)
    assert isinstance(tone["warnings"], list)


def test_fomc_tone_route_null_when_no_data(seeded):
    body = _client().get("/api/macro/fomc-tone").get_json()
    assert body["success"] is True
    assert body["tone"] is None
