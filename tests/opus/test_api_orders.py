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


def _trade_signal(signal_id="opus_stored_trade", **overrides):
    """A TRADE/READY limit that can rest against a quote above the entry."""
    from opus.types import (
        Archetype, Decision, Direction, GateResult, Levels, Quote,
        Readiness, Regime, Signal,
    )

    now = time.time()
    base = dict(
        symbol="EURUSD", display="EUR/USD", asset_class="forex",
        direction=Direction.LONG, archetype=Archetype.SWEEP_RECLAIM,
        regime=Regime.VOLATILE_RANGE, decision=Decision.TRADE,
        readiness=Readiness.READY, conviction=0.6, coherence=0.7,
        probability=0.55, expectancy_r=0.3, cost_r=0.1,
        levels=Levels(
            entry=1.1000, stop=1.0960, targets=[1.1060], entry_kind="limit",
        ),
        gates=[GateResult(name="min_expectancy", passed=True, hard=True)],
        size_units=10_000.0, risk_pct=0.5, signal_id=signal_id,
        quote=Quote("EURUSD", 1.10195, 1.10205, ts=now, source="SYNTHETIC"),
        provenance={"venue": "synthetic", "barTs": now},
        created_ts=now,
    )
    base.update(overrides)
    return Signal(**base)


def test_execute_records_a_paper_order_when_a_fresh_scan_no_longer_emits_the_card(api, monkeypatch):
    """The user-visible bug: Execute on a valid TRADE card does nothing.

    /api/opus/execute re-scores the symbol and requires the exact signal_id to
    come back. If the trigger bar rolled, or the ranking merely shuffled, the
    API returns 409. The panel then auto-rescans, the card vanishes, and
    GET /api/opus/orders stays empty. Paper/demo execution must instead submit
    the stored signal (revalidated against a live quote) so a resting order
    actually appears.
    """
    from opus import api as api_mod

    client, store = api
    signal = _trade_signal()
    store.record_signals([signal])

    class _Fresh:
        signals = []

    monkeypatch.setattr(api_mod.engine, "scan", lambda **kw: _Fresh())
    monkeypatch.setattr(
        api_mod, "fetch_bundle",
        lambda spec, **kw: type("B", (), {"quote": signal.quote, "errors": []})(),
    )

    response = client.post("/api/opus/execute", json={
        "signalId": signal.signal_id, "symbol": "EURUSD",
    })
    body = response.get_json()

    assert response.status_code == 200, body
    assert body["ok"] is True
    assert body["order"]["status"] in {"working", "filled"}
    assert body["order"]["mode"] == "paper"

    orders = client.get("/api/opus/orders").get_json()["orders"]
    assert any(o.get("signal_id") == signal.signal_id for o in orders)


def test_execute_still_refuses_a_signal_that_was_never_scanned(api, monkeypatch):
    from opus import api as api_mod

    client, _store = api

    class _Fresh:
        signals = []

    monkeypatch.setattr(api_mod.engine, "scan", lambda **kw: _Fresh())
    response = client.post("/api/opus/execute", json={
        "signalId": "opus_never_seen", "symbol": "EURUSD",
    })
    assert response.status_code == 409
    assert "no longer present" in response.get_json()["error"]


def test_execute_refuses_a_stored_signal_for_a_different_symbol(api, monkeypatch):
    from opus import api as api_mod

    client, store = api
    signal = _trade_signal()
    store.record_signals([signal])

    class _Fresh:
        signals = []

    monkeypatch.setattr(api_mod.engine, "scan", lambda **kw: _Fresh())
    response = client.post("/api/opus/execute", json={
        "signalId": signal.signal_id, "symbol": "GBPUSD",
    })
    assert response.status_code == 409
    assert client.get("/api/opus/orders").get_json()["orders"] == []


def test_execute_refuses_a_stored_signal_when_the_quote_is_stale(api, monkeypatch):
    """Store fallback must not skip submit-time quote freshness."""
    from opus import api as api_mod
    from opus.types import Quote

    client, store = api
    signal = _trade_signal()
    signal.quote = Quote("EURUSD", 1.10195, 1.10205, ts=time.time() - 600.0, source="SYNTHETIC")
    store.record_signals([signal])

    class _Fresh:
        signals = []

    stale = Quote("EURUSD", 1.10195, 1.10205, ts=time.time() - 600.0, source="SYNTHETIC")
    monkeypatch.setattr(api_mod.engine, "scan", lambda **kw: _Fresh())
    monkeypatch.setattr(
        api_mod, "fetch_bundle",
        lambda spec, **kw: type("B", (), {"quote": stale, "errors": []})(),
    )

    response = client.post("/api/opus/execute", json={
        "signalId": signal.signal_id, "symbol": "EURUSD",
    })
    body = response.get_json()
    assert response.status_code == 422
    assert "revalidation" in (body.get("error") or "").lower()
    orders = client.get("/api/opus/orders").get_json()["orders"]
    assert all(str(o.get("status", "")).lower() == "rejected" for o in orders)


def test_execute_refuses_a_stored_signal_older_than_the_configured_window(api, monkeypatch):
    """Store fallback is for bar-id churn, not a 21-minute-old card."""
    from opus import api as api_mod

    client, store = api
    signal = _trade_signal(created_ts=time.time() - 10_000.0)
    store.record_signals([signal])

    class _Fresh:
        signals = []

    monkeypatch.setattr(api_mod.engine, "scan", lambda **kw: _Fresh())
    response = client.post("/api/opus/execute", json={
        "signalId": signal.signal_id, "symbol": "EURUSD",
    })
    body = response.get_json()
    assert response.status_code == 409, body
    assert "old" in (body.get("error") or "").lower()
    assert client.get("/api/opus/orders").get_json()["orders"] == []


def test_execute_refuses_stored_card_when_fresh_scan_has_opposite_trade(api, monkeypatch):
    from opus import api as api_mod
    from opus.types import Direction

    client, store = api
    stored = _trade_signal()
    store.record_signals([stored])
    opposite = _trade_signal(
        signal_id="opus_fresh_short", direction=Direction.SHORT,
    )

    class _Fresh:
        signals = [opposite]

    monkeypatch.setattr(api_mod.engine, "scan", lambda **kw: _Fresh())
    response = client.post("/api/opus/execute", json={
        "signalId": stored.signal_id, "symbol": "EURUSD",
    })
    body = response.get_json()
    assert response.status_code == 409, body
    assert "opposite" in (body.get("error") or "").lower()
    assert client.get("/api/opus/orders").get_json()["orders"] == []
