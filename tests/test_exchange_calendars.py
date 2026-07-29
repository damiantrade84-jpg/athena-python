"""Tests for exchange_calendars: deterministic session-phase computation.

Every case pins a concrete UTC instant so the test is not affected by DST
transitions on the day it happens to run.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exchange_calendars import (
    EXCHANGE_CALENDARS,
    SessionPhase,
    compute_session_phase,
    is_break_phase,
)

# 2026-07-28 is a Tuesday (ordinary trading day, no ambiguity with a
# holiday/weekend), and July puts every calendar's local zone in its summer
# offset (EDT UTC-4, CEST UTC+2; Hong Kong has no DST, always UTC+8).
_TUE = datetime(2026, 7, 28, tzinfo=timezone.utc)
# Noon UTC lands within Saturday local time on every calendar here (UTC-4 to
# UTC+8): NYSE 08:00, XETRA/Euronext 14:00, HKEX 20:00 — all still Saturday.
# Midnight UTC would read as Friday evening in New York and is not a safe
# "definitely Saturday everywhere" instant.
_SAT = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _at(base: datetime, hour: int, minute: int = 0) -> datetime:
    return base.replace(hour=hour, minute=minute)


@pytest.mark.parametrize("calendar_id,utc_hour,utc_minute,expected", [
    # NYSE/Nasdaq: 09:30-16:00 America/New_York (EDT = UTC-4 in July).
    ("NYSE_NASDAQ", 12, 0, SessionPhase.PRE_OPEN),   # 08:00 ET
    ("NYSE_NASDAQ", 13, 29, SessionPhase.PRE_OPEN),  # 09:29 ET
    ("NYSE_NASDAQ", 13, 30, SessionPhase.OPEN),      # 09:30 ET, open boundary
    ("NYSE_NASDAQ", 18, 0, SessionPhase.OPEN),       # 14:00 ET
    ("NYSE_NASDAQ", 20, 0, SessionPhase.POST_CLOSE), # 16:00 ET, close boundary
    ("NYSE_NASDAQ", 23, 0, SessionPhase.POST_CLOSE), # 19:00 ET
    # XETRA: 09:00-17:30 Europe/Berlin (CEST = UTC+2 in July).
    ("XETRA", 6, 0, SessionPhase.PRE_OPEN),    # 08:00 CEST
    ("XETRA", 7, 0, SessionPhase.OPEN),        # 09:00 CEST, open boundary
    ("XETRA", 15, 29, SessionPhase.OPEN),      # 17:29 CEST
    ("XETRA", 15, 30, SessionPhase.POST_CLOSE),# 17:30 CEST, close boundary
    # Euronext Paris: identical hours/timezone offset to XETRA in July.
    ("EURONEXT_PARIS", 6, 0, SessionPhase.PRE_OPEN),
    ("EURONEXT_PARIS", 7, 0, SessionPhase.OPEN),
    ("EURONEXT_PARIS", 15, 30, SessionPhase.POST_CLOSE),
    # HKEX: 09:30-12:00, 13:00-16:00 Asia/Hong_Kong (UTC+8, no DST).
    ("HKEX", 1, 0, SessionPhase.PRE_OPEN),      # 09:00 HKT
    ("HKEX", 1, 30, SessionPhase.OPEN),         # 09:30 HKT, morning open
    ("HKEX", 3, 59, SessionPhase.OPEN),         # 11:59 HKT
    ("HKEX", 4, 0, SessionPhase.LUNCH_BREAK),   # 12:00 HKT, lunch starts
    ("HKEX", 4, 30, SessionPhase.LUNCH_BREAK),  # 12:30 HKT
    ("HKEX", 4, 59, SessionPhase.LUNCH_BREAK),  # 12:59 HKT
    ("HKEX", 5, 0, SessionPhase.OPEN),          # 13:00 HKT, afternoon open
    ("HKEX", 7, 59, SessionPhase.OPEN),         # 15:59 HKT
    ("HKEX", 8, 0, SessionPhase.POST_CLOSE),    # 16:00 HKT, close boundary
])
def test_session_phase_boundaries(calendar_id, utc_hour, utc_minute, expected):
    at = _at(_TUE, utc_hour, utc_minute)
    assert compute_session_phase(calendar_id, at) == expected


@pytest.mark.parametrize("calendar_id", list(EXCHANGE_CALENDARS))
def test_saturday_is_closed_on_every_calendar(calendar_id):
    assert compute_session_phase(calendar_id, _SAT) == SessionPhase.CLOSED


def test_naive_datetime_returns_none_rather_than_guessing_a_timezone():
    naive = datetime(2026, 7, 28, 14, 0)
    assert compute_session_phase("NYSE_NASDAQ", naive) is None


def test_unknown_calendar_id_returns_none():
    assert compute_session_phase("MARS_EXCHANGE", _TUE) is None
    assert compute_session_phase(None, _TUE) is None
    assert compute_session_phase("", _TUE) is None


def test_deterministic_no_wall_clock_dependence():
    """Same calendar_id + same instant must always produce the same phase."""
    at = _at(_TUE, 14, 0)
    results = {compute_session_phase("NYSE_NASDAQ", at) for _ in range(5)}
    assert results == {SessionPhase.OPEN}


@pytest.mark.parametrize("phase,expected", [
    (SessionPhase.OPEN, False),
    (SessionPhase.PRE_OPEN, True),
    (SessionPhase.LUNCH_BREAK, True),
    (SessionPhase.POST_CLOSE, True),
    (SessionPhase.CLOSED, True),
    ("OPEN", False),
    ("LUNCH_BREAK", True),
    ("not_a_real_phase", False),
    (None, False),
])
def test_is_break_phase(phase, expected):
    assert is_break_phase(phase) is expected


def test_all_four_required_calendars_are_registered():
    assert set(EXCHANGE_CALENDARS) == {
        "NYSE_NASDAQ", "XETRA", "EURONEXT_PARIS", "HKEX",
    }


def test_hkex_is_the_only_calendar_with_a_lunch_break():
    for calendar_id, calendar in EXCHANGE_CALENDARS.items():
        if calendar_id == "HKEX":
            assert len(calendar.sessions) == 2
        else:
            assert len(calendar.sessions) == 1
