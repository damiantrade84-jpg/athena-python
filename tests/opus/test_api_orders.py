"""The API surface for resting orders.

A pending order that nothing ever sweeps is worse than a market order: the
store shows exposure that may not exist, and the position caps count it
forever. So the sweep has to be reachable, and it has to run on its own.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest

import opus.config as config
from opus.data.store import Store


@pytest.fixture()
def api(monkeypatch):
    from flask import Flask

    from opus import api as api_mod
    from opus.api import create_opus_blueprint

    tmp = tempfile.mkdtemp()
    store = Store(os.path.join(tmp, "opus_api_test.sqlite3"))
    monkeypatch.setattr(api_mod, "default_store", lambda: store)

    # Sweep on every poll: the throttle is a production concern, and leaving
    # it on would make these tests depend on which one ran first.
    execution = config.load()["EXECUTION"]
    original = execution.get("reconcile_interval_sec")
    execution["reconcile_interval_sec"] = 0.0

    app = Flask(__name__)
    app.register_blueprint(create_opus_blueprint())
    try:
        yield app.test_client(), store
    finally:
        execution["reconcile_interval_sec"] = original
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _working(store, order_id="opus-api-1", expires_in=900.0):
    submitted = time.time() - 10.0
    store.record_order({
        "order_id": order_id, "signal_id": "opus_sig_api",
        "submitted_ts": submitted, "mode": "paper", "broker": "paper",
        "symbol": "EURUSD", "direction": "LONG", "order_type": "limit",
        "units": 10_000.0, "entry": 1.1000, "stop": 1.0960, "target": 1.1060,
        "status": "working", "broker_ref": None,
        "detail": {"venue": "nowhere", "expiresTs": submitted + expires_in},
    })
    return submitted


def test_the_sweep_is_throttled_between_polls(api):
    """Two polls a second apart must not both call out to a venue."""
    client, store = api
    config.load()["EXECUTION"]["reconcile_interval_sec"] = 3600.0
    _working(store)

    first = client.get("/api/opus/status").get_json()["reconcile"]
    second = client.get("/api/opus/status").get_json()["reconcile"]

    assert first is not None and first["examined"] == 1
    assert second is None


def test_reconcile_endpoint_reports_what_it_swept(api):
    client, store = api
    _working(store)

    body = client.post("/api/opus/reconcile", json={}).get_json()

    assert body["ok"] is True
    assert body["report"]["examined"] == 1


def test_status_counts_working_orders(api):
    client, store = api
    _working(store)

    body = client.get("/api/opus/status").get_json()

    assert body["store"]["working"] == 1


def test_status_sweeps_expired_orders(api):
    """The panel polls status; that is the heartbeat a resting order gets."""
    client, store = api
    _working(store, expires_in=1.0)          # already past its expiry

    client.get("/api/opus/status")

    row = next(r for r in store.orders(limit=10) if r["order_id"] == "opus-api-1")
    assert row["status"] == "expired"


def test_orders_endpoint_exposes_the_resting_price(api):
    client, store = api
    _working(store)

    body = client.get("/api/opus/orders").get_json()
    row = body["orders"][0]

    assert row["status"] == "working"
    assert row["entry"] == pytest.approx(1.1000)
    assert row["detail"]["expiresTs"] > row["submitted_ts"]
