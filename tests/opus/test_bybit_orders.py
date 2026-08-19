"""Bybit order placement.

Live routing to Bybit is currently unreachable (the adapter cannot confirm the
account is a demo one, so `require_demo_account` refuses it), but the adapter
still has to speak the same interface as every other broker: a plan in, a
resting order out. An adapter that only fails when someone finally arms it is
not a safe thing to leave in the tree.
"""

from __future__ import annotations

import pytest

from opus.execution.orders import resolve_order_plan
from opus.types import Direction, Levels
from tests.opus.test_execution_orders import _signal


@pytest.fixture()
def bybit(monkeypatch):
    from opus.execution import bybit_broker

    sent = []
    monkeypatch.setattr(bybit_broker, "credentials", lambda: ("key", "secret"))
    monkeypatch.setattr(
        bybit_broker.BybitBroker, "_instrument",
        lambda self, symbol: {
            "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
            "priceFilter": {"tickSize": "0.0001"},
        },
    )

    def _post(path, body):
        sent.append((path, body))
        return {"retCode": 0, "result": {"orderId": "bybit-1",
                                         "orderLinkId": body.get("orderLinkId")}}

    monkeypatch.setattr(bybit_broker, "_signed_post", _post)
    return sent


def _crypto_signal(**kw):
    base = dict(symbol="BTCUSDT", display="BTC/USDT", asset_class="crypto",
                provenance={"venue": "bybit"})
    base.update(kw)
    return _signal(**base)


def test_bybit_rests_a_limit_order_at_the_planned_entry(bybit):
    from opus.execution.bybit_broker import BybitBroker

    signal = _crypto_signal()
    result = BybitBroker().place(signal, units=0.5,
                                 plan=resolve_order_plan(signal, signal.quote))

    assert result.status == "working"
    _, body = bybit[-1]
    assert body["orderType"] == "Limit"
    assert float(body["price"]) == pytest.approx(signal.levels.entry)
    assert body["timeInForce"] == "GTC"


def test_bybit_sends_a_stop_entry_as_a_conditional_order(bybit):
    """A buy stop is not a limit: it has to trigger on a RISE through the level."""
    from opus.execution.bybit_broker import BybitBroker

    signal = _crypto_signal(
        levels=Levels(entry=1.1050, stop=1.1010, targets=[1.1110],
                      entry_kind="stop"),
    )
    BybitBroker().place(signal, units=0.5,
                        plan=resolve_order_plan(signal, signal.quote))

    _, body = bybit[-1]
    assert float(body["triggerPrice"]) == pytest.approx(1.1050)
    assert int(body["triggerDirection"]) == 1


def test_bybit_short_stop_triggers_on_a_fall(bybit):
    from opus.execution.bybit_broker import BybitBroker

    signal = _crypto_signal(
        direction=Direction.SHORT,
        levels=Levels(entry=1.1000, stop=1.1040, targets=[1.0940],
                      entry_kind="stop"),
    )
    BybitBroker().place(signal, units=0.5,
                        plan=resolve_order_plan(signal, signal.quote))

    _, body = bybit[-1]
    assert int(body["triggerDirection"]) == 2


def test_bybit_market_plan_still_fills_now(bybit):
    from opus.execution.bybit_broker import BybitBroker

    signal = _crypto_signal(
        levels=Levels(entry=1.1020, stop=1.0980, targets=[1.1080],
                      entry_kind="market"),
    )
    result = BybitBroker().place(signal, units=0.5,
                                 plan=resolve_order_plan(signal, signal.quote))

    assert result.status == "filled"
    assert bybit[-1][1]["orderType"] == "Market"


def test_bybit_refuses_a_rejected_plan_without_calling_the_venue(bybit):
    from opus.execution.bybit_broker import BybitBroker
    from opus.execution.orders import OrderPlan

    result = BybitBroker().place(
        _crypto_signal(), units=0.5,
        plan=OrderPlan(kind="reject", reason="cannot rest"),
    )

    assert result.ok is False
    assert bybit == []


def test_bybit_cancels_a_working_order(bybit):
    from opus.execution.bybit_broker import BybitBroker

    result = BybitBroker().cancel({
        "order_id": "opus-b1", "broker_ref": "bybit-1", "symbol": "BTCUSDT",
        "direction": "LONG", "mode": "live", "status": "working",
    })

    assert result.ok is True
    assert result.status == "cancelled"
    assert bybit[-1][0].endswith("/order/cancel")


def test_bybit_snaps_an_off_grid_price_to_the_symbols_tick(bybit, monkeypatch):
    """An off-grid price is rejected by the venue, and a rejected resting
    order looks exactly like one that never filled."""
    from opus.execution import bybit_broker
    from opus.execution.bybit_broker import BybitBroker

    monkeypatch.setattr(
        bybit_broker.BybitBroker, "_instrument",
        lambda self, symbol: {
            "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
            "priceFilter": {"tickSize": "0.01"},
        },
    )
    # Below the ask, so it can rest, and off the 0.01 grid so it must snap.
    signal = _crypto_signal(
        levels=Levels(entry=1.09375, stop=1.0860, targets=[1.1060],
                      entry_kind="limit"),
    )
    BybitBroker().place(signal, units=0.5,
                        plan=resolve_order_plan(signal, signal.quote))

    assert float(bybit[-1][1]["price"]) == pytest.approx(1.09)
