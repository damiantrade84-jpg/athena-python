"""The daily loss cap.

`RISK.max_daily_loss_pct` has been in the configuration, and named in the
router's own docstring as one of the portfolio limits, while nothing read it.
A cap nobody enforces is worse than no cap: it reads as protection.

The measurement is account-wide, not OPUS-only. What is being protected is the
account's capital, and a per-engine cap on a shared account can be evaded by
running two engines that are each individually under their own limit.
"""

from __future__ import annotations

import datetime as dt
import time

import pytest

import opus.config as config


class StubLiveBroker:
    name = "stub"
    is_live = True

    def __init__(self, pnl, equity=100_000.0):
        self._pnl = pnl
        self._equity = equity

    def realised_pnl_today(self, now=None):
        return self._pnl

    def account(self) -> dict:
        return {"broker": self.name, "available": True, "live": True,
                "equity": self._equity}

    def account_kind(self) -> str:
        return "demo"


def test_a_loss_under_the_cap_allows_trading():
    from opus.execution import router

    ok, reason = router.daily_loss_ok(StubLiveBroker(pnl=-1_000.0))   # 1% of 100k
    assert ok is True
    assert "1.0" in reason or "within" in reason.lower()


def test_a_loss_at_the_cap_stops_trading():
    from opus.execution import router

    cap = float(config.load()["RISK"]["max_daily_loss_pct"])          # 3.0
    ok, reason = router.daily_loss_ok(StubLiveBroker(pnl=-cap * 1_000.0))

    assert ok is False
    assert "daily loss" in reason.lower()


def test_a_profitable_day_is_never_a_loss():
    from opus.execution import router

    ok, _ = router.daily_loss_ok(StubLiveBroker(pnl=+8_000.0))
    assert ok is True


def test_an_unmeasurable_pnl_blocks_rather_than_assumes_zero():
    """'The broker did not tell us' is not 'we are flat'."""
    from opus.execution import router

    ok, reason = router.daily_loss_ok(StubLiveBroker(pnl=None))
    assert ok is False
    assert "could not" in reason.lower() or "unmeasur" in reason.lower()


def test_an_unreadable_equity_blocks_too():
    from opus.execution import router

    ok, _ = router.daily_loss_ok(StubLiveBroker(pnl=-100.0, equity=0.0))
    assert ok is False


def test_the_cap_can_be_disabled_by_configuring_zero(monkeypatch):
    from opus.execution import router

    risk = config.load()["RISK"]
    original = risk["max_daily_loss_pct"]
    try:
        risk["max_daily_loss_pct"] = 0.0
        ok, reason = router.daily_loss_ok(StubLiveBroker(pnl=None))
        assert ok is True
        assert "disabled" in reason.lower()
    finally:
        risk["max_daily_loss_pct"] = original


def test_a_live_submit_is_refused_once_the_daily_loss_cap_is_hit(monkeypatch):
    from opus.execution import router
    from tests.opus.test_execution_orders import _signal

    monkeypatch.setenv("OPUS_LIVE_CONFIRM", "yes")
    broker = StubLiveBroker(pnl=-9_000.0)                  # 9% of 100k
    monkeypatch.setattr(router, "_broker_for", lambda venue, live: broker)

    cfg = config.load()
    original = cfg.get("MODE")
    try:
        cfg["MODE"] = "live"
        result = router.submit(_signal(), request_live=True)
    finally:
        cfg["MODE"] = original

    assert result.ok is False
    assert "daily loss" in result.message.lower()


def test_paper_routing_is_not_blocked_by_a_broker_it_never_reaches(monkeypatch):
    """The guard protects real capital; a paper order risks none of it."""
    from opus.execution import router
    from tests.opus.test_execution_orders import _signal

    monkeypatch.delenv("OPUS_LIVE_CONFIRM", raising=False)
    result = router.submit(_signal())

    assert result.ok is True
    assert result.mode == "paper"


# --------------------------------------------------------------------------
# MT5: the day's realised P&L, in the broker's own clock
# --------------------------------------------------------------------------

class FakeDeal:
    def __init__(self, profit=0.0, commission=0.0, swap=0.0, entry=1):
        self.profit = profit
        self.commission = commission
        self.swap = swap
        self.entry = entry


def test_mt5_realised_pnl_sums_profit_commission_and_swap(monkeypatch):
    """A day that looks flat on profit alone can be well down on costs."""
    from opus.execution import mt5_broker
    from opus.execution.mt5_broker import MT5Broker
    from tests.opus.test_mt5_orders import FakeMT5, _install

    fake = _install(monkeypatch, FakeMT5())
    fake.deals = [
        FakeDeal(profit=250.0, commission=-12.0, swap=-3.0),
        FakeDeal(profit=-600.0, commission=-12.0, swap=0.0),
    ]
    fake.history_deals_get = lambda date_from=None, date_to=None: tuple(fake.deals)
    monkeypatch.setattr(mt5_broker, "server_time_offset", lambda: 10800.0)

    assert MT5Broker().realised_pnl_today() == pytest.approx(-377.0)


def test_mt5_realised_pnl_is_unknown_when_history_is_unavailable(monkeypatch):
    from opus.execution.mt5_broker import MT5Broker
    from tests.opus.test_mt5_orders import FakeMT5, _install

    fake = _install(monkeypatch, FakeMT5())

    def _raise(**kw):
        raise RuntimeError("terminal not connected")

    fake.history_deals_get = _raise

    assert MT5Broker().realised_pnl_today() is None


def test_mt5_asks_history_for_the_brokers_own_day(monkeypatch):
    """Deal times are in SERVER time; asking for UTC midnight on a UTC+3
    server silently drops the first three hours of the trading day."""
    from opus.execution import mt5_broker
    from opus.execution.mt5_broker import MT5Broker
    from tests.opus.test_mt5_orders import FakeMT5, _install

    fake = _install(monkeypatch, FakeMT5())
    seen = {}

    def _history(date_from=None, date_to=None):
        seen["from"] = date_from
        return ()

    fake.history_deals_get = _history
    monkeypatch.setattr(mt5_broker, "server_time_offset", lambda: 10800.0)

    now = time.time()
    MT5Broker().realised_pnl_today(now=now)

    utc_midnight = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    expected = dt.datetime.fromtimestamp(utc_midnight + 10800.0, tz=dt.timezone.utc)
    assert seen["from"].replace(tzinfo=dt.timezone.utc) == expected
