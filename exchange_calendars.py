"""Deterministic exchange trading-session calendars for cash-equity CFDs.

Side-effect-free and wall-clock-free, matching trigger_lifecycle.py and
timeframe_policy.py: every function takes an explicit ``datetime`` and never
calls ``datetime.now()``, so live and backtest/replay callers get identical
behaviour for the same instant.

Scope and honest limitations
-----------------------------
Covers weekday + local continuous-trading-hours only, for the four calendars
Athena's ATFX cash-equity CFD book needs (2026-07-28 ATFX additions). It does
**not** model exchange holidays: on a public holiday the corresponding
exchange will incorrectly report its normal weekday phase. Holiday calendars
are external, time-varying data this module does not fabricate — wire a real
holiday feed before treating ``SessionPhase.OPEN`` as authoritative for
anything holiday-sensitive (position sizing over a holiday, etc).

Closing-auction windows are also not modelled explicitly. The continuous
close time is used as the ``OPEN`` boundary, so auction-only trading after
that point already reads as ``POST_CLOSE`` rather than being mistaken for
ordinary continuous-session liquidity — a conservative choice that satisfies
"exclude the closing auction from ordinary continuous-session triggers"
without needing exact per-exchange auction-window data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SessionPhase(str, Enum):
    """Where an instant falls relative to an exchange's continuous session(s)."""

    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    LUNCH_BREAK = "LUNCH_BREAK"
    POST_CLOSE = "POST_CLOSE"
    CLOSED = "CLOSED"  # weekend, or (unmodelled) holiday


#: Phases in which continuous trading is not happening — used to gate trigger
#: arming/expiry, never to change score, direction, or conviction.
_BREAK_PHASES = frozenset({
    SessionPhase.PRE_OPEN,
    SessionPhase.LUNCH_BREAK,
    SessionPhase.POST_CLOSE,
    SessionPhase.CLOSED,
})


@dataclass(frozen=True)
class ExchangeCalendar:
    calendar_id: str
    timezone: str  # IANA name
    #: (open_time, close_time) pairs in local time, in session order. A
    #: calendar with more than one pair has a break between consecutive
    #: sessions (HKEX's midday lunch break).
    sessions: tuple[tuple[time, time], ...]
    weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})  # Mon-Fri


#: Standard continuous-trading hours, local time, for the four exchanges the
#: ATFX cash-equity CFD book spans. IANA timezone names.
EXCHANGE_CALENDARS: dict[str, ExchangeCalendar] = {
    "NYSE_NASDAQ": ExchangeCalendar(
        calendar_id="NYSE_NASDAQ",
        timezone="America/New_York",
        sessions=((time(9, 30), time(16, 0)),),
    ),
    "XETRA": ExchangeCalendar(
        calendar_id="XETRA",
        timezone="Europe/Berlin",
        sessions=((time(9, 0), time(17, 30)),),
    ),
    "EURONEXT_PARIS": ExchangeCalendar(
        calendar_id="EURONEXT_PARIS",
        timezone="Europe/Paris",
        sessions=((time(9, 0), time(17, 30)),),
    ),
    "HKEX": ExchangeCalendar(
        calendar_id="HKEX",
        timezone="Asia/Hong_Kong",
        sessions=((time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))),
    ),
}


def is_break_phase(phase: SessionPhase | str | None) -> bool:
    """True for any phase that is not continuous trading."""
    if isinstance(phase, SessionPhase):
        return phase in _BREAK_PHASES
    try:
        return SessionPhase(str(phase)) in _BREAK_PHASES
    except ValueError:
        return False


def compute_session_phase(calendar_id: str | None, at: datetime) -> SessionPhase | None:
    """Return the trading phase for ``calendar_id`` at instant ``at``.

    ``at`` must be timezone-aware; a naive datetime returns None rather than
    being silently assumed to be UTC or local — a wrong assumption here would
    misclassify every observation near a session boundary. Returns None for
    an unrecognised ``calendar_id`` so the caller can keep its prior/default
    state instead of a fabricated phase.
    """
    if not calendar_id:
        return None
    calendar = EXCHANGE_CALENDARS.get(calendar_id)
    if calendar is None or at.tzinfo is None:
        return None
    try:
        zone = ZoneInfo(calendar.timezone)
    except ZoneInfoNotFoundError:
        return None
    local = at.astimezone(zone)
    if local.weekday() not in calendar.weekdays:
        return SessionPhase.CLOSED
    local_time = local.time()
    sessions = calendar.sessions
    if local_time < sessions[0][0]:
        return SessionPhase.PRE_OPEN
    if local_time >= sessions[-1][1]:
        return SessionPhase.POST_CLOSE
    for idx, (open_t, close_t) in enumerate(sessions):
        if open_t <= local_time < close_t:
            return SessionPhase.OPEN
        if idx + 1 < len(sessions) and close_t <= local_time < sessions[idx + 1][0]:
            return SessionPhase.LUNCH_BREAK
    # Unreachable given the boundary checks above; fail closed rather than
    # silently defaulting to OPEN if the session table is ever malformed.
    return SessionPhase.CLOSED
