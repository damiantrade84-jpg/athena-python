"""Reconciliation of working orders.

A resting order is a promise the system has to keep track of. Three states have
to be handled and none of them may be guessed: it filled, it expired, or it is
still working. Marking an order gone while it might still be live at the broker
is the failure that matters, so anything unknown stays working.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time

import numpy as np
import pytest

import opus.config as config
from opus.data import feed as feed_mod
from opus.data.store import Store
from opus.types import Candles


@pytest.fixture()
def store():
    tmp = tempfile.mkdtemp()
    st = Store(os.path.join(tmp, "opus_reconcile_test.sqlite3"))
    try:
        yield st
    finally:
        st.close()
        shutil.rmtree(tmp, ignore_errors=True)


class StubFeed(feed_mod.Feed):
    """A feed whose M5 bars are supplied by the test."""

    name = "STUB"

    def __init__(self, low: float, high: float, start_ts: float):
        self.low = low
        self.high = high
        self.start_ts = start_ts

    def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        n = 6
        ts = np.array([self.start_ts + i * 300 for i in range(n)], dtype=float)
        close = np.full(n, (self.low + self.high) / 2.0)
        return Candles(
            symbol=symbol, timeframe=timeframe, ts=ts,
            open=close.copy(), high=np.full(n, self.high),
            low=np.full(n, self.low), close=close,
            volume=np.full(n, 100.0), source="STUB",
        )


def _working_row(store, *, order_id="opus-w1", entry=1.1000, expires_in=900.0,
                 mode="paper", submitted_ts=None, order_type="limit",
                 direction="LONG", broker="paper", broker_ref=None):
    submitted = float(submitted_ts if submitted_ts is not None else time.time())
    store.record_order({
        "order_id": order_id, "signal_id": "opus_sig_1",
        "submitted_ts": submitted, "mode": mode, "broker": broker,
        "symbol": "EURUSD", "direction": direction, "order_type": order_type,
        "units": 10_000.0, "entry": entry, "stop": 1.0960, "target": 1.1060,
        "status": "working", "broker_ref": broker_ref,
        "detail": {"venue": "stub", "expiresTs": submitted + expires_in},
    })
    return submitted


def _row(store, order_id="opus-w1"):
    return next(r for r in store.orders(limit=50) if r["order_id"] == order_id)


# --------------------------------------------------------------------------
# paper: a resting order fills when its price actually trades
# --------------------------------------------------------------------------

def test_paper_limit_fills_when_price_trades_through_it(store, monkeypatch):
    from opus.execution import reconcile

    submitted = _working_row(store, entry=1.1000)
    monkeypatch.setitem(feed_mod._REGISTRY, "stub",
                        StubFeed(low=1.0990, high=1.1030, start_ts=submitted + 300))

    report = reconcile.reconcile(store, now=submitted + 600)

    assert report.filled == 1
    row = _row(store)
    assert row["status"] == "filled"
    assert row["entry"] == pytest.approx(1.1000)   # a limit fills AT its price


def test_paper_limit_stays_working_while_price_never_reaches_it(store, monkeypatch):
    from opus.execution import reconcile

    submitted = _working_row(store, entry=1.1000)
    monkeypatch.setitem(feed_mod._REGISTRY, "stub",
                        StubFeed(low=1.1010, high=1.1050, start_ts=submitted + 300))

    report = reconcile.reconcile(store, now=submitted + 600)

    assert report.filled == 0
    assert report.working == 1
    assert _row(store)["status"] == "working"


def test_paper_stop_order_pays_slippage_on_the_fill(store, monkeypatch):
    """A stop is a market order once triggered; filling it at its trigger
    price would flatter every breakout setup."""
    from opus.execution import reconcile

    submitted = _working_row(store, entry=1.1000, order_type="stop")
    monkeypatch.setitem(feed_mod._REGISTRY, "stub",
                        StubFeed(low=1.0990, high=1.1030, start_ts=submitted + 300))

    reconcile.reconcile(store, now=submitted + 600)

    slip = float(config.load()["EXECUTION"]["paper_slippage_bps"]) / 10_000.0
    assert _row(store)["entry"] == pytest.approx(1.1000 * (1.0 + slip))


def test_an_expired_paper_order_is_expired_not_filled(store, monkeypatch):
    from opus.execution import reconcile

    submitted = _working_row(store, entry=1.1000, expires_in=900.0)
    # Price never reaches the level, and the expiry has passed.
    monkeypatch.setitem(feed_mod._REGISTRY, "stub",
                        StubFeed(low=1.1010, high=1.1050, start_ts=submitted + 300))

    report = reconcile.reconcile(store, now=submitted + 1200)

    assert report.expired == 1
    assert _row(store)["status"] == "expired"


def test_bars_before_the_order_existed_cannot_fill_it(store, monkeypatch):
    """Otherwise an order 'fills' on history that predates its submission."""
    from opus.execution import reconcile

    submitted = _working_row(store, entry=1.1000)
    monkeypatch.setitem(feed_mod._REGISTRY, "stub",
                        StubFeed(low=1.0990, high=1.1030,
                                 start_ts=submitted - 3600))

    report = reconcile.reconcile(store, now=submitted + 600)

    assert report.filled == 0
    assert _row(store)["status"] == "working"


# --------------------------------------------------------------------------
# live: the broker is the authority, and silence leaves the order alone
# --------------------------------------------------------------------------

class StubBroker:
    name = "stub"
    is_live = True

    def __init__(self, state):
        self.state = state
        self.cancelled = []

    def order_state(self, order_row: dict) -> dict:
        return self.state

    def cancel(self, order_row: dict):
        from opus.execution.broker import OrderResult

        self.cancelled.append(order_row["order_id"])
        return OrderResult(
            ok=True, order_id=order_row["order_id"], status="cancelled",
            broker=self.name, mode="live", symbol=order_row["symbol"],
            direction=order_row["direction"], message="cancelled",
        )


def test_a_live_order_the_broker_reports_filled_is_recorded_filled(store, monkeypatch):
    from opus.execution import reconcile

    submitted = _working_row(store, mode="live", broker="stub", broker_ref="555")
    broker = StubBroker({"status": "filled", "entry": 1.10012})
    monkeypatch.setattr(reconcile, "_broker_for_row", lambda row: broker)

    report = reconcile.reconcile(store, now=submitted + 600)

    assert report.filled == 1
    row = _row(store)
    assert row["status"] == "filled"
    assert row["entry"] == pytest.approx(1.10012)


def test_an_unknown_live_state_leaves_the_order_working(store, monkeypatch):
    """The broker did not answer. That is not evidence the order is gone."""
    from opus.execution import reconcile

    submitted = _working_row(store, mode="live", broker="stub", broker_ref="555")
    broker = StubBroker({"status": "unknown"})
    monkeypatch.setattr(reconcile, "_broker_for_row", lambda row: broker)

    report = reconcile.reconcile(store, now=submitted + 600)

    assert report.working == 1
    assert _row(store)["status"] == "working"


def test_an_expired_live_order_is_cancelled_at_the_broker_first(store, monkeypatch):
    from opus.execution import reconcile

    submitted = _working_row(store, mode="live", broker="stub", broker_ref="555",
                             expires_in=900.0)
    broker = StubBroker({"status": "working"})
    monkeypatch.setattr(reconcile, "_broker_for_row", lambda row: broker)

    report = reconcile.reconcile(store, now=submitted + 1200)

    assert broker.cancelled == ["opus-w1"]
    assert report.expired == 1
    assert _row(store)["status"] == "expired"


def test_a_failed_cancel_leaves_the_order_working(store, monkeypatch):
    """If the venue would not cancel it, the order may still be live."""
    from opus.execution import reconcile
    from opus.execution.broker import OrderResult

    submitted = _working_row(store, mode="live", broker="stub", broker_ref="555",
                             expires_in=900.0)
    broker = StubBroker({"status": "working"})
    broker.cancel = lambda row: OrderResult(
        ok=False, order_id=row["order_id"], status="working", broker="stub",
        mode="live", symbol=row["symbol"], direction=row["direction"],
        message="venue refused",
    )
    monkeypatch.setattr(reconcile, "_broker_for_row", lambda row: broker)

    report = reconcile.reconcile(store, now=submitted + 1200)

    assert report.expired == 0
    assert _row(store)["status"] == "working"
    assert report.errors


def test_an_order_with_no_recorded_expiry_still_expires(store, monkeypatch):
    """Rows written before expiry was recorded, or by an adapter that did not
    report one, would otherwise sit working forever and hold a position slot."""
    from opus.execution import reconcile

    submitted = time.time() - 4000.0
    store.record_order({
        "order_id": "opus-noexp", "signal_id": "opus_sig_2",
        "submitted_ts": submitted, "mode": "paper", "broker": "paper",
        "symbol": "EURUSD", "direction": "LONG", "order_type": "limit",
        "units": 10_000.0, "entry": 1.1000, "stop": 1.0960, "target": 1.1060,
        "status": "working", "broker_ref": None,
        "detail": {"venue": "stub"},          # no expiresTs
    })
    monkeypatch.setitem(feed_mod._REGISTRY, "stub",
                        StubFeed(low=1.1010, high=1.1050, start_ts=submitted + 300))

    report = reconcile.reconcile(store, now=time.time())

    assert report.expired == 1
    assert _row(store, "opus-noexp")["status"] == "expired"


def test_a_recent_order_with_no_expiry_is_left_alone(store, monkeypatch):
    from opus.execution import reconcile

    submitted = time.time() - 10.0
    store.record_order({
        "order_id": "opus-noexp2", "signal_id": "opus_sig_3",
        "submitted_ts": submitted, "mode": "paper", "broker": "paper",
        "symbol": "EURUSD", "direction": "LONG", "order_type": "limit",
        "units": 10_000.0, "entry": 1.1000, "stop": 1.0960, "target": 1.1060,
        "status": "working", "broker_ref": None, "detail": {"venue": "stub"},
    })
    monkeypatch.setitem(feed_mod._REGISTRY, "stub",
                        StubFeed(low=1.1010, high=1.1050, start_ts=submitted + 1))

    report = reconcile.reconcile(store, now=time.time())

    assert report.expired == 0
    assert _row(store, "opus-noexp2")["status"] == "working"
