"""MT5 order placement: pending orders, broker-side expiry, honest sizing.

Driven against a stub terminal. The assertions that matter are the ones about
what is SENT: a resting order at the planned price, an expiry the server will
read correctly, and a size derived from tick economics or not sent at all.
"""

from __future__ import annotations

import time

import pytest

from opus.execution.orders import resolve_order_plan
from opus.types import Levels, Quote
from tests.opus.test_execution_orders import _signal


class FakeSymbolInfo:
    def __init__(self, **kw):
        self.digits = kw.get("digits", 5)
        self.volume_step = kw.get("volume_step", 0.01)
        self.volume_min = kw.get("volume_min", 0.01)
        self.volume_max = kw.get("volume_max", 100.0)
        self.filling_mode = kw.get("filling_mode", 2)
        self.expiration_mode = kw.get("expiration_mode", 15)
        self.trade_contract_size = kw.get("trade_contract_size", 100_000.0)
        self.trade_tick_size = kw.get("trade_tick_size", 0.00001)
        self.trade_tick_value = kw.get("trade_tick_value", 0.018)   # ZAR account
        self.trade_mode = kw.get("trade_mode", 4)
        self.visible = True


class FakeTick:
    def __init__(self, bid=1.10195, ask=1.10205):
        self.bid = bid
        self.ask = ask
        self.time = int(time.time())
        self.time_msc = int(time.time() * 1000)


class FakeAccount:
    def __init__(self, equity=100_000.0, trade_mode=0):
        self.equity = equity
        self.balance = equity
        self.currency = "ZAR"
        self.leverage = 100
        self.margin_free = equity
        self.login = 250006835
        self.server = "ATFXGM16-LIVE"
        self.trade_mode = trade_mode


class FakeResult:
    def __init__(self, retcode=10009, order=555, price=0.0, volume=0.0):
        self.retcode = retcode
        self.order = order
        self.deal = 777
        self.price = price
        self.volume = volume
        self.comment = "ok"


_DEFAULT = object()


class FakeMT5:
    """Only the surface MT5Broker actually touches.

    Passing `account=None` means the terminal answered with nothing, which is
    a different state from "not specified" and is the one that matters.
    """

    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    ORDER_TIME_GTC = 0
    ORDER_TIME_SPECIFIED = 2
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    SYMBOL_TRADE_MODE_DISABLED = 0
    ORDER_STATE_PLACED = 1
    ORDER_STATE_CANCELED = 2
    ORDER_STATE_FILLED = 4
    ORDER_STATE_REJECTED = 5
    ORDER_STATE_EXPIRED = 6

    def __init__(self, account=_DEFAULT, info=None, pending=(), history=()):
        self._account = FakeAccount() if account is _DEFAULT else account
        self._info = info if info is not None else FakeSymbolInfo()
        self._pending = list(pending)
        self._history = list(history)
        self.sent = []
        self.checked = []

    def orders_get(self, ticket=None):
        return tuple(o for o in self._pending if o.ticket == ticket)

    def history_orders_get(self, ticket=None):
        return tuple(o for o in self._history if o.ticket == ticket)

    def account_info(self):
        return self._account

    def symbol_info(self, symbol):
        return self._info

    def symbol_info_tick(self, symbol):
        return FakeTick()

    def order_check(self, request):
        self.checked.append(request)
        return FakeResult(retcode=0)

    def order_send(self, request):
        self.sent.append(request)
        return FakeResult(price=request.get("price", 0.0),
                          volume=request.get("volume", 0.0))

    def last_error(self):
        return (0, "ok")


def _install(monkeypatch, fake):
    from opus.execution import mt5_broker

    monkeypatch.setattr(mt5_broker, "_mt5", lambda: fake)
    monkeypatch.setattr(mt5_broker, "ensure_connected", lambda: True)
    monkeypatch.setattr(mt5_broker, "resolve_symbol", lambda s: s)
    monkeypatch.setattr(mt5_broker, "server_time_offset", lambda: 10800.0)
    return fake


@pytest.fixture()
def fake_mt5(monkeypatch):
    return _install(monkeypatch, FakeMT5())


def test_mt5_rests_a_buy_limit_at_the_planned_entry(fake_mt5):
    from opus.execution.mt5_broker import MT5Broker

    signal = _signal()
    plan = resolve_order_plan(signal, signal.quote)
    result = MT5Broker().place(signal, units=10_000.0, plan=plan)

    assert result.ok is True
    assert result.status == "working"
    request = fake_mt5.sent[-1]
    assert request["action"] == FakeMT5.TRADE_ACTION_PENDING
    assert request["type"] == FakeMT5.ORDER_TYPE_BUY_LIMIT
    assert request["price"] == pytest.approx(1.1000)
    assert request["sl"] == pytest.approx(signal.levels.stop)


def test_mt5_rests_a_sell_stop_for_a_short_break(fake_mt5):
    from opus.execution.mt5_broker import MT5Broker
    from opus.types import Direction

    signal = _signal(
        direction=Direction.SHORT,
        levels=Levels(entry=1.1000, stop=1.1040, targets=[1.0940],
                      entry_kind="stop"),
    )
    MT5Broker().place(signal, units=10_000.0,
                      plan=resolve_order_plan(signal, signal.quote))

    assert fake_mt5.sent[-1]["type"] == FakeMT5.ORDER_TYPE_SELL_STOP


def test_mt5_pending_expiry_is_sent_in_broker_server_time(fake_mt5):
    """MT5 reads expiration in the SERVER's timezone, not UTC.

    OPUS normalises broker timestamps to UTC on the way in, so an expiry sent
    as UTC to a UTC+3 server expires three hours early - or is rejected as a
    time in the past.
    """
    from opus.execution.mt5_broker import MT5Broker

    signal = _signal()
    plan = resolve_order_plan(signal, signal.quote)
    MT5Broker().place(signal, units=10_000.0, plan=plan)

    request = fake_mt5.sent[-1]
    assert request["type_time"] == FakeMT5.ORDER_TIME_SPECIFIED
    assert request["expiration"] == pytest.approx(plan.expires_ts + 10800.0, abs=1.5)


def test_mt5_falls_back_to_gtc_when_the_symbol_forbids_a_timed_expiry(monkeypatch):
    """expiration_mode is a bitmask; SPECIFIED is not always allowed."""
    from opus.execution.mt5_broker import MT5Broker

    fake = _install(monkeypatch, FakeMT5(info=FakeSymbolInfo(expiration_mode=1)))
    signal = _signal()
    MT5Broker().place(signal, units=10_000.0,
                      plan=resolve_order_plan(signal, signal.quote))

    request = fake.sent[-1]
    assert request["type_time"] == FakeMT5.ORDER_TIME_GTC
    assert "expiration" not in request


def test_mt5_size_puts_exactly_the_authorised_risk_on_the_stop(monkeypatch):
    """Worked by hand, so the assertion cannot restate the implementation.

    Account holds 100 000 ZAR and the signal authorises 0.5% -> 500 ZAR at
    risk. The stop is 0.0040 away and one tick is 0.00001, so the stop is 400
    ticks. The symbol pays 0.05 ZAR per tick per lot, so one lot loses
    400 x 0.05 = 20 ZAR if the stop is hit. 500 / 20 = 25 lots.

    Sizing from base-currency units instead would send 10 000/100 000 = 0.1
    lots - the currency-blind number that oversized this account by 16.27x.
    """
    from opus.execution.mt5_broker import MT5Broker

    fake = _install(monkeypatch, FakeMT5(
        account=FakeAccount(equity=100_000.0),
        info=FakeSymbolInfo(trade_tick_value=0.05, volume_max=1000.0),
    ))
    signal = _signal()          # entry 1.1000, stop 1.0960, risk_pct 0.5

    MT5Broker().place(signal, units=10_000.0,
                      plan=resolve_order_plan(signal, signal.quote))

    assert fake.sent[-1]["volume"] == pytest.approx(25.00, abs=0.005)


def test_mt5_size_halves_when_the_stop_is_twice_as_wide(monkeypatch):
    """A property that holds whatever the formula is: risk is what is fixed."""
    from opus.execution.mt5_broker import MT5Broker

    fake = _install(monkeypatch, FakeMT5(
        account=FakeAccount(equity=100_000.0),
        info=FakeSymbolInfo(trade_tick_value=0.05, volume_max=1000.0),
    ))
    wide = _signal(levels=Levels(entry=1.1000, stop=1.0920, targets=[1.1160],
                                 entry_kind="limit"))

    MT5Broker().place(wide, units=10_000.0,
                      plan=resolve_order_plan(wide, wide.quote))

    assert fake.sent[-1]["volume"] == pytest.approx(12.50, abs=0.005)


def test_mt5_refuses_to_size_when_the_account_cannot_be_read(monkeypatch):
    """Equity of 0 means 'the account did not answer', not 'size it anyway'.

    The removed fallback sized from base-currency units, which on this ZAR
    account oversized every position by the USDZAR rate - measured at 16.27x.
    """
    from opus.execution.mt5_broker import MT5Broker

    fake = _install(monkeypatch, FakeMT5(account=None))
    signal = _signal()
    result = MT5Broker().place(signal, units=10_000.0,
                               plan=resolve_order_plan(signal, signal.quote))

    assert result.ok is False
    assert fake.sent == []
    assert "equity" in result.message.lower() or "account" in result.message.lower()


def test_mt5_refuses_to_size_without_tick_economics(monkeypatch):
    from opus.execution.mt5_broker import MT5Broker

    fake = _install(monkeypatch, FakeMT5(info=FakeSymbolInfo(trade_tick_value=0.0)))
    signal = _signal()
    result = MT5Broker().place(signal, units=10_000.0,
                               plan=resolve_order_plan(signal, signal.quote))

    assert result.ok is False
    assert fake.sent == []
    assert "tick" in result.message.lower()


def test_mt5_market_plan_still_sends_a_deal(fake_mt5):
    from opus.execution.mt5_broker import MT5Broker

    signal = _signal(
        levels=Levels(entry=1.1020, stop=1.0980, targets=[1.1080],
                      entry_kind="market"),
    )
    result = MT5Broker().place(signal, units=10_000.0,
                               plan=resolve_order_plan(signal, signal.quote))

    assert result.status == "filled"
    assert fake_mt5.sent[-1]["action"] == FakeMT5.TRADE_ACTION_DEAL


def test_mt5_refuses_a_rejected_plan_without_touching_the_terminal(fake_mt5):
    from opus.execution.mt5_broker import MT5Broker
    from opus.execution.orders import OrderPlan

    result = MT5Broker().place(
        _signal(), units=10_000.0,
        plan=OrderPlan(kind="reject", reason="cannot rest"),
    )

    assert result.ok is False
    assert fake_mt5.sent == []


def test_mt5_cancels_a_working_order_by_ticket(fake_mt5):
    from opus.execution.mt5_broker import MT5Broker

    result = MT5Broker().cancel({
        "order_id": "opus-abc", "broker_ref": "555", "symbol": "EURUSD",
        "direction": "LONG", "mode": "live", "status": "working",
    })

    assert result.ok is True
    assert result.status == "cancelled"
    assert fake_mt5.sent[-1]["action"] == FakeMT5.TRADE_ACTION_REMOVE
    assert fake_mt5.sent[-1]["order"] == 555


def test_mt5_cancel_without_a_ticket_is_refused(fake_mt5):
    from opus.execution.mt5_broker import MT5Broker

    result = MT5Broker().cancel({"order_id": "opus-abc", "symbol": "EURUSD"})

    assert result.ok is False
    assert fake_mt5.sent == []


class FakeOrder:
    def __init__(self, ticket=555, state=1, price_open=1.1000, price_current=1.1000):
        self.ticket = ticket
        self.state = state
        self.price_open = price_open
        self.price_current = price_current


def test_mt5_reports_a_still_resting_order_as_working(monkeypatch):
    from opus.execution.mt5_broker import MT5Broker

    _install(monkeypatch, FakeMT5(pending=[FakeOrder(ticket=555)]))
    state = MT5Broker().order_state({"broker_ref": "555", "symbol": "EURUSD"})

    assert state["status"] == "working"


def test_mt5_reports_a_filled_order_with_its_fill_price(monkeypatch):
    from opus.execution.mt5_broker import MT5Broker

    _install(monkeypatch, FakeMT5(history=[
        FakeOrder(ticket=555, state=FakeMT5.ORDER_STATE_FILLED, price_open=1.10004),
    ]))
    state = MT5Broker().order_state({"broker_ref": "555", "symbol": "EURUSD"})

    assert state["status"] == "filled"
    assert state["entry"] == pytest.approx(1.10004)


def test_mt5_reports_a_cancelled_order_as_cancelled(monkeypatch):
    from opus.execution.mt5_broker import MT5Broker

    _install(monkeypatch, FakeMT5(history=[
        FakeOrder(ticket=555, state=FakeMT5.ORDER_STATE_EXPIRED),
    ]))
    state = MT5Broker().order_state({"broker_ref": "555", "symbol": "EURUSD"})

    assert state["status"] == "cancelled"


def test_mt5_order_state_is_unknown_when_the_terminal_has_no_record(monkeypatch):
    """Neither pending nor in history: the terminal cannot tell us, so the
    reconciler must not conclude the order is gone."""
    from opus.execution.mt5_broker import MT5Broker

    _install(monkeypatch, FakeMT5())
    state = MT5Broker().order_state({"broker_ref": "555", "symbol": "EURUSD"})

    assert state["status"] == "unknown"
