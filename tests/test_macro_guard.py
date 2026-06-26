"""Offline tests for the macro-risk guard state machine (no network)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from macro import config
from macro import macro_guard as guard
from macro.macro_guard import _event_state, get_active_macro_state, get_symbol_macro_risk
from macro.store import MacroEventStore

_T = datetime(2026, 6, 17, 18, 0, tzinfo=timezone.utc)  # scheduled rate decision


def _store_with_event(scheduled=_T):
    d = tempfile.mkdtemp(prefix="macro_guard_")
    store = MacroEventStore(os.path.join(d, "m.db"))
    store.upsert_event(
        {
            "id": "FOMC_RATE_DECISION:2026-06-17",
            "event_type": "FOMC_RATE_DECISION",
            "currency": "USD",
            "title": "FOMC rate decision (2026-06-17)",
            "scheduled_time_utc": scheduled.isoformat(),
            "press_conference_time_utc": (scheduled + timedelta(minutes=30)).isoformat(),
            "status": "SCHEDULED",
            "source": "fed_fomc_calendar",
        }
    )
    return store


@pytest.mark.parametrize(
    "offset_min, expected",
    [
        (-7200, "NONE"),               # 5 days before → outside UPCOMING horizon
        (-60, "UPCOMING"),             # within horizon, before lockout_start (T-30)
        (-20, "LOCKOUT"),              # inside pre-release lockout
        (10, "ACTIVE_RELEASE"),        # after release, inside hard window
        (90, "POST_RELEASE_CAUTION"),  # after hard_end (T+75), before caution_end (T+120)
        (200, "COMPLETED"),            # past caution_end
    ],
)
def test_event_state_across_timeline(offset_min, expected):
    store = _store_with_event()
    ev = store.get_event("FOMC_RATE_DECISION:2026-06-17")
    assert ev is not None
    now = _T + timedelta(minutes=offset_min)
    assert _event_state(ev, now) == expected


def test_affected_symbol_matrix():
    assert guard.is_symbol_affected("EUR/USD") is True
    assert guard.is_symbol_affected("XAU/USD") is True
    assert guard.is_symbol_affected("US500") is True
    assert guard.is_symbol_affected("BTC/USDT") is True
    assert guard.is_symbol_affected("DXY") is True
    # Not USD-affected:
    assert guard.is_symbol_affected("DAX 40") is False
    assert guard.is_symbol_affected("EUR/GBP") is False


def test_active_state_blocks_affected_but_not_unaffected():
    store = _store_with_event()
    now = _T - timedelta(minutes=20)  # LOCKOUT
    glob = get_active_macro_state(now=now, store=store)
    assert glob["macroRisk"] == "LOCKOUT"
    assert glob["blockNewTrades"] is True
    assert glob["allowAutoExecution"] is False

    xau = get_symbol_macro_risk("XAU/USD", now=now, store=store)
    assert xau["affected"] is True
    assert xau["blockNewTrades"] is True

    dax = get_symbol_macro_risk("DAX 40", now=now, store=store)
    assert dax["macroRisk"] == "LOCKOUT"  # global risk still surfaced
    assert dax["affected"] is False
    assert dax["blockNewTrades"] is False
    assert dax["allowAutoExecution"] is True


def test_disabled_layer_returns_none(monkeypatch):
    store = _store_with_event()
    monkeypatch.setenv("ATHENA_FOMC_ENABLED", "false")
    now = _T - timedelta(minutes=20)  # would be LOCKOUT if enabled
    assert config.enabled() is False
    state = get_active_macro_state(now=now, store=store)
    assert state["macroRisk"] == "NONE"
    assert state["blockNewTrades"] is False
