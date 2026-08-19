"""MT5 broker-clock correction: timezone offset and clock drift.

These are the inputs to every MT5 freshness gate, so the failure modes that
matter are the ones that make a stale quote look fresh.
"""

from __future__ import annotations

import time
import types

import pytest

from opus.data import mt5_feed


TZ = 3 * 3600.0          # broker on UTC+3, as ATFX is


class FakeMT5:
    """Minimal stand-in: each symbol reports a tick at a chosen age."""

    def __init__(self, ages: dict[str, float], drift: float = 0.0):
        self.ages = ages
        self.drift = drift

    def symbol_info_tick(self, name):
        if name not in self.ages:
            return None
        # tick.time = true instant + timezone + broker clock drift
        stamp = (time.time() - self.ages[name]) + TZ + self.drift
        return types.SimpleNamespace(time=stamp, time_msc=stamp * 1000.0)

    def symbols_get(self):
        return [types.SimpleNamespace(name=n) for n in self.ages]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No terminal, no cache carried between tests."""
    monkeypatch.setattr(mt5_feed, "ensure_connected", lambda: True)
    mt5_feed._server_offset = None
    mt5_feed._clock_drift = 0.0
    mt5_feed._offset_stamp = 0.0
    mt5_feed._symbol_cache.clear()
    yield
    mt5_feed._server_offset = None
    mt5_feed._clock_drift = 0.0
    mt5_feed._offset_stamp = 0.0
    mt5_feed._symbol_cache.clear()


def _install(monkeypatch, ages, drift=0.0):
    monkeypatch.setattr(mt5_feed, "_mt5", lambda: FakeMT5(ages, drift))


def test_a_stale_symbol_does_not_drag_the_offset_down(monkeypatch):
    """The freshest tick estimates the offset; a median would be biased."""
    _install(monkeypatch, {"EURUSD": 0.2, "IT40": 9000.0, "JP225": 7000.0})
    assert mt5_feed.server_time_offset(force=True) == TZ


def test_clock_drift_is_measured_and_applied_to_quotes_only(monkeypatch):
    _install(monkeypatch, {"EURUSD": 0.0}, drift=3.0)
    assert mt5_feed.server_time_offset(force=True) == TZ
    assert mt5_feed.clock_drift_sec() == pytest.approx(3.0, abs=0.5)
    # Bars take the snapped timezone; ticks take timezone + drift.
    assert mt5_feed.quote_time_offset() == pytest.approx(TZ + 3.0, abs=0.5)


def test_drift_correction_cannot_make_a_stale_quote_look_fresh(monkeypatch):
    """The weekend case: no fresh tick anywhere in the sample.

    Every raw reading then sits BELOW the true offset, and a signed correction
    would subtract less than the timezone - ageing the quote backwards into
    freshness. The correction floors at zero instead.
    """
    _install(monkeypatch, {"IT40": 600.0, "UK100": 700.0, "FRA40": 900.0})
    assert mt5_feed.server_time_offset(force=True) == TZ
    assert mt5_feed.clock_drift_sec() == 0.0
    assert mt5_feed.quote_time_offset() == TZ

    # And the resulting quote still reads as ~600s old, not as fresh.
    stamp = (time.time() - 600.0) + TZ
    assert (time.time() - (stamp - mt5_feed.quote_time_offset())) == pytest.approx(600.0, abs=2)


def test_drift_is_capped(monkeypatch):
    """A wild measurement must not age a live quote past the freshness gate."""
    _install(monkeypatch, {"EURUSD": 0.0}, drift=600.0)
    mt5_feed.server_time_offset(force=True)
    assert mt5_feed.clock_drift_sec() == mt5_feed._MAX_CLOCK_DRIFT_SEC


def test_an_unmeasurable_probe_is_not_cached(monkeypatch):
    """The bug this replaces: a failed probe pinned 0.0 for the full TTL.

    Every MT5 quote in that window is left uncorrected and reads hours in the
    future, so the whole MT5 universe fails its freshness gate.
    """
    _install(monkeypatch, {})                     # terminal up, no ticks
    assert mt5_feed.server_time_offset(force=True) == 0.0
    assert mt5_feed._server_offset is None        # nothing cached
    assert mt5_feed._offset_stamp == 0.0

    # The next call, once ticks exist, measures properly instead of serving 0.0.
    _install(monkeypatch, {"EURUSD": 0.0})
    assert mt5_feed.server_time_offset() == TZ


def test_probe_connects_before_reading_the_symbol_list(monkeypatch):
    """Cold call: without connecting first, symbols_get() returns nothing."""
    calls = []
    monkeypatch.setattr(mt5_feed, "ensure_connected",
                        lambda: calls.append("connect") or True)
    _install(monkeypatch, {"EURUSD": 0.0})
    assert mt5_feed.server_time_offset(force=True) == TZ
    assert calls == ["connect"]


def test_an_implausible_offset_is_rejected(monkeypatch):
    """A 40-hour 'timezone' is a measurement fault, not a timezone."""
    ages = {"BROKEN": -40 * 3600.0}               # tick 40h in the future
    _install(monkeypatch, ages)
    assert mt5_feed.server_time_offset(force=True) == 0.0
    assert mt5_feed._server_offset is None
