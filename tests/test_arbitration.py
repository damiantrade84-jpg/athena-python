"""Signal arbitration tests."""

from __future__ import annotations

from athena_ase.horizon import HORIZONS
from athena_ase.instruments import Instrument
from athena_ase.signals.arbitrate import Candidate, EventSpacingFilter, FiredSignal, arbitrate


def _inst():
    return Instrument("EURUSD", "EUR/USD", "forex", "major")


def test_arbitrate_agree():
    fired = [
        FiredSignal("tsmom", 1, 0.8),
        FiredSignal("carry", 1, 0.6),
    ]
    c = arbitrate(_inst(), "swing", 1000, 10, 0.0, 0.01, fired)
    assert c is not None
    assert c.direction == 1
    assert c.agreement_count == 2


def test_arbitrate_conflict_none():
    fired = [
        FiredSignal("a", 1, 0.5),
        FiredSignal("b", -1, 0.5),
    ]
    assert arbitrate(_inst(), "swing", 1000, 10, 0.0, 0.01, fired) is None


def test_arbitrate_dominance():
    fired = [
        FiredSignal("a", 1, 0.8),
        FiredSignal("b", -1, 0.3),
    ]
    c = arbitrate(_inst(), "swing", 1000, 10, 0.0, 0.01, fired)
    assert c is not None
    assert c.direction == 1
    assert c.conflict_flag is True


def test_event_spacing_and_flip():
    filt = EventSpacingFilter()
    h = HORIZONS["intraday"].max_hold_bars
    spacing = h // 2
    inst = _inst()
    c1 = Candidate("EURUSD", "forex", "intraday", 1, 100, 1, [], 1, False, 0.01, 0.0)
    assert filt.allow(c1)
    filt.record(c1)
    c2 = Candidate("EURUSD", "forex", "intraday", 2, 100 + spacing - 1, 1, [], 1, False, 0.01, 0.0)
    assert not filt.allow(c2)
    c3 = Candidate("EURUSD", "forex", "intraday", 3, 100 + spacing, -1, [], 1, False, 0.01, 0.0)
    assert filt.allow(c3)
