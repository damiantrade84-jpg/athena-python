"""Tests for OPUS order placement: pending orders, expiry and reconciliation.

The premise of every assertion here is that an order reaches the broker AT THE
PLANNED ENTRY or does not reach it at all. OPUS geometry produces limit and
stop entries; filling those at whatever the market happens to be quoting
changes the risk, the reward multiple and the size that every gate was
evaluated against.
"""

from __future__ import annotations

import time

import pytest

import opus.config as config
from opus.types import (
    Archetype, Decision, Direction, Levels, Quote, Readiness, Regime, Signal,
)


def _signal(**overrides) -> Signal:
    base = dict(
        symbol="EURUSD", display="EUR/USD", asset_class="forex",
        direction=Direction.LONG, archetype=Archetype.SWEEP_RECLAIM,
        regime=Regime.VOLATILE_RANGE, decision=Decision.TRADE,
        readiness=Readiness.READY, conviction=0.6, coherence=0.7,
        probability=0.55, expectancy_r=0.3, cost_r=0.1,
        levels=Levels(entry=1.1000, stop=1.0960, targets=[1.1060],
                      entry_kind="limit"),
        size_units=10_000.0, risk_pct=0.5, signal_id="opus_orders_1",
        quote=Quote("EURUSD", 1.10195, 1.10205, ts=time.time(), source="MT5"),
        provenance={"venue": "mt5"},
    )
    base.update(overrides)
    return Signal(**base)


# --------------------------------------------------------------------------
# the order plan: entry kind + direction + live quote -> a resting order
# --------------------------------------------------------------------------

def test_long_limit_below_the_market_rests_as_a_buy_limit():
    from opus.execution.orders import resolve_order_plan

    signal = _signal()          # entry 1.1000, ask 1.10205
    plan = resolve_order_plan(signal, signal.quote)

    assert plan.kind == "limit"
    assert plan.price == pytest.approx(1.1000)
    assert plan.rejected is False


def test_long_limit_above_the_market_is_refused_not_filled_at_market():
    """A buy limit above the ask cannot rest - it would fill immediately.

    That instant fill is precisely the defect: the order executes at the market
    price while every gate was evaluated against the planned entry.
    """
    from opus.execution.orders import resolve_order_plan

    signal = _signal(
        quote=Quote("EURUSD", 1.09795, 1.09805, ts=time.time(), source="MT5"),
    )
    plan = resolve_order_plan(signal, signal.quote)

    assert plan.rejected is True
    assert "limit" in plan.reason.lower()


def test_long_stop_below_the_market_is_refused_because_the_break_happened():
    from opus.execution.orders import resolve_order_plan

    signal = _signal(
        levels=Levels(entry=1.1000, stop=1.0960, targets=[1.1060],
                      entry_kind="stop"),
        quote=Quote("EURUSD", 1.10195, 1.10205, ts=time.time(), source="MT5"),
    )
    plan = resolve_order_plan(signal, signal.quote)

    assert plan.rejected is True
    assert "already" in plan.reason.lower()


def test_long_stop_above_the_market_rests_as_a_buy_stop():
    from opus.execution.orders import resolve_order_plan

    signal = _signal(
        levels=Levels(entry=1.1050, stop=1.1010, targets=[1.1110],
                      entry_kind="stop"),
    )
    plan = resolve_order_plan(signal, signal.quote)

    assert plan.kind == "stop"
    assert plan.price == pytest.approx(1.1050)
    assert plan.rejected is False


def test_short_sides_are_mirrored_against_the_bid():
    from opus.execution.orders import resolve_order_plan

    quote = Quote("EURUSD", 1.10195, 1.10205, ts=time.time(), source="MT5")
    sell_limit = _signal(
        direction=Direction.SHORT,
        levels=Levels(entry=1.1040, stop=1.1080, targets=[1.0980],
                      entry_kind="limit"),
        quote=quote,
    )
    assert resolve_order_plan(sell_limit, quote).kind == "limit"

    sell_stop = _signal(
        direction=Direction.SHORT,
        levels=Levels(entry=1.1000, stop=1.1040, targets=[1.0940],
                      entry_kind="stop"),
        quote=quote,
    )
    assert resolve_order_plan(sell_stop, quote).kind == "stop"

    # A sell limit BELOW the bid cannot rest, and a sell stop ABOVE it means
    # the break already happened.
    assert resolve_order_plan(
        _signal(direction=Direction.SHORT,
                levels=Levels(entry=1.1000, stop=1.1040, targets=[1.0940],
                              entry_kind="limit"),
                quote=quote),
        quote,
    ).rejected is True
    assert resolve_order_plan(
        _signal(direction=Direction.SHORT,
                levels=Levels(entry=1.1040, stop=1.1080, targets=[1.0980],
                              entry_kind="stop"),
                quote=quote),
        quote,
    ).rejected is True


def test_market_entries_price_off_the_live_quote():
    """VALUE_FADE is the one archetype that is genuinely a market entry."""
    from opus.execution.orders import resolve_order_plan

    signal = _signal(
        archetype=Archetype.VALUE_FADE,
        levels=Levels(entry=1.1020, stop=1.0980, targets=[1.1080],
                      entry_kind="market"),
    )
    plan = resolve_order_plan(signal, signal.quote)

    assert plan.kind == "market"
    assert plan.price == pytest.approx(signal.quote.ask)
    assert plan.rejected is False


def test_a_pending_plan_carries_an_expiry_and_a_market_plan_does_not():
    from opus.execution.orders import resolve_order_plan

    now = time.time()
    pending = resolve_order_plan(_signal(), _signal().quote, now=now)
    expiry = float(config.load()["EXECUTION"]["limit_expiry_sec"])
    assert pending.expires_ts == pytest.approx(now + expiry)

    market = resolve_order_plan(
        _signal(levels=Levels(entry=1.1020, stop=1.0980, targets=[1.1080],
                              entry_kind="market")),
        _signal().quote, now=now,
    )
    assert market.expires_ts is None


def test_a_missing_quote_cannot_produce_a_plan():
    from opus.execution.orders import resolve_order_plan

    plan = resolve_order_plan(_signal(quote=None), None)
    assert plan.rejected is True
    assert "quote" in plan.reason.lower()


# --------------------------------------------------------------------------
# the paper broker: a resting order is WORKING, not filled
# --------------------------------------------------------------------------

def test_paper_pending_order_rests_at_the_planned_price():
    from opus.execution.broker import PaperBroker
    from opus.execution.orders import resolve_order_plan

    signal = _signal()
    plan = resolve_order_plan(signal, signal.quote)
    result = PaperBroker().place(signal, units=10_000.0, plan=plan)

    assert result.ok is True
    assert result.status == "working"
    # The whole point: the order sits at the planned entry, NOT at the market.
    assert result.entry == pytest.approx(signal.levels.entry)
    assert result.entry != pytest.approx(signal.quote.ask)
    assert result.order_type == "limit"
    assert result.detail["expiresTs"] == pytest.approx(plan.expires_ts)


def test_paper_market_order_still_crosses_the_spread_and_slips():
    from opus.execution.broker import PaperBroker
    from opus.execution.orders import resolve_order_plan

    signal = _signal(
        levels=Levels(entry=1.1020, stop=1.0980, targets=[1.1080],
                      entry_kind="market"),
    )
    plan = resolve_order_plan(signal, signal.quote)
    result = PaperBroker().place(signal, units=10_000.0, plan=plan)

    assert result.status == "filled"
    assert result.entry >= signal.quote.ask


def test_paper_broker_refuses_a_rejected_plan():
    from opus.execution.broker import PaperBroker
    from opus.execution.orders import OrderPlan

    result = PaperBroker().place(
        _signal(), units=10_000.0,
        plan=OrderPlan(kind="reject", reason="cannot rest"),
    )
    assert result.ok is False
    assert "cannot rest" in result.message


def test_paper_working_order_can_be_cancelled():
    from opus.execution.broker import PaperBroker
    from opus.execution.orders import resolve_order_plan

    signal = _signal()
    broker = PaperBroker()
    placed = broker.place(signal, units=10_000.0,
                          plan=resolve_order_plan(signal, signal.quote))
    cancelled = broker.cancel(placed.as_store_row())

    assert cancelled.ok is True
    assert cancelled.status == "cancelled"
    assert cancelled.order_id == placed.order_id


# --------------------------------------------------------------------------
# the router: what may be sent, at what size, labelled as what
# --------------------------------------------------------------------------

@pytest.fixture()
def tmp_store():
    import os
    import shutil
    import tempfile

    from opus.data.store import Store

    tmp = tempfile.mkdtemp()
    store = Store(os.path.join(tmp, "opus_orders_test.sqlite3"))
    try:
        yield store
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_submit_rests_a_pending_order_and_records_it(tmp_store):
    from opus.execution import router

    result = router.submit(_signal(), store=tmp_store)

    assert result.ok is True
    assert result.status == "working"
    assert result.entry == pytest.approx(1.1000)

    rows = tmp_store.orders(limit=10)
    assert rows[0]["status"] == "working"
    assert rows[0]["order_type"] == "limit"


def test_submit_refuses_a_signal_whose_entry_the_market_has_passed(tmp_store):
    """The reclaim level is gone: there is no order to place, not a market one."""
    from opus.execution import router

    passed = _signal(
        quote=Quote("EURUSD", 1.09795, 1.09805, ts=time.time(), source="MT5"),
    )
    result = router.submit(passed, store=tmp_store)

    assert result.ok is False
    assert result.status == "rejected"
    assert "immediately" in result.message


def test_submit_refuses_a_size_larger_than_the_risk_model_authorised(tmp_store):
    """`units` is a client-supplied field on /api/opus/execute.

    Taking it at face value let a caller place 6451x the authorised size, with
    no reference to risk_pct_max at all.
    """
    from opus.execution import router

    signal = _signal()
    result = router.submit(signal, units=signal.size_units * 4.0, store=tmp_store)

    assert result.ok is False
    assert "authorised" in result.message


def test_submit_allows_a_smaller_size_than_authorised(tmp_store):
    from opus.execution import router

    signal = _signal()
    result = router.submit(signal, units=signal.size_units / 2.0, store=tmp_store)

    assert result.ok is True
    assert result.units == pytest.approx(signal.size_units / 2.0)


def test_revalidation_rejects_a_future_dated_quote():
    """`Quote.age_sec` floors at zero, so a future-dated quote reads as fresh.

    That is the same fail-open the scan-time gate was hardened against: an MT5
    server on UTC+3 read as UTC dates every tick three hours ahead.
    """
    from opus.execution import router

    now = time.time()
    skewed = _signal(quote=Quote("EURUSD", 1.10195, 1.10205, ts=now + 3 * 3600,
                                 source="MT5"))
    ok, reason = router.revalidate(skewed, now=now)

    assert ok is False
    assert "future" in reason.lower()
