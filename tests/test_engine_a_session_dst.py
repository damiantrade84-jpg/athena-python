"""Engine A US cash-session DST window.

The fixed 13:00-21:00 UTC table is 09:00-17:00 America/New_York under EDT
(UTC-4). Under EST (UTC-5) the same local cash hours are 14:00-22:00 UTC.
Expected values are the NY cash clock, not the shifter's internal delta.
"""
from __future__ import annotations

from datetime import datetime, timezone

from engine_a_v3.routing import SpecialistRoute
from engine_a_v3.setups import _session_window


def _us_index_route() -> SpecialistRoute:
    return SpecialistRoute(
        score_group="us_index_etf",
        family="index",
        subclass="us_indices",
    )


def test_us_cash_session_stays_13_21_utc_in_summer(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SESSION_DST_AWARE", True)
    start, end, _label = _session_window(
        _us_index_route(),
        on_time=datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc),
    )
    assert (start, end) == (13.0, 21.0)


def test_us_cash_session_shifts_to_14_22_utc_in_winter(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SESSION_DST_AWARE", True)
    start, end, _label = _session_window(
        _us_index_route(),
        on_time=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),
    )
    # 09:00-17:00 EST = 14:00-22:00 UTC. The inverted shifter produced 12-20.
    assert (start, end) == (14.0, 22.0)


def test_us_cash_session_dst_off_keeps_fixed_utc_in_winter(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SESSION_DST_AWARE", False)
    start, end, _label = _session_window(
        _us_index_route(),
        on_time=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),
    )
    assert (start, end) == (13, 21)
