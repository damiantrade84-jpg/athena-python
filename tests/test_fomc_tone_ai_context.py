"""Offline tests for FOMC tone in AI-review context + lockout precedence (no network)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

import macro.store as store_mod
import macro.tone_store as tone_store_mod
from ai_review.macro_context import build_macro_prompt_context, render_macro_prompt_block
from macro.fomc_tone import process_event
from macro.macro_guard import get_symbol_macro_risk
from macro.store import MacroEventStore
from macro.tone_store import MacroToneStore

BASE = (
    "The Committee seeks to achieve maximum employment and inflation at the rate of two "
    "percent over the longer run. The Committee will continue to monitor incoming data."
)
# Strongly hawkish current statement (many added hawkish phrases) vs neutral previous.
STRONG_HAWKISH = (
    BASE + " Inflation remains elevated. Inflation has risen. Labor market remains tight. "
    "Does not expect it will be appropriate to reduce the target range."
)


@pytest.fixture()
def seeded(monkeypatch):
    d = tempfile.mkdtemp(prefix="tone_ai_")
    ev_store = MacroEventStore(os.path.join(d, "m.db"))
    t_store = MacroToneStore(os.path.join(d, "m.db"))
    monkeypatch.setattr(store_mod, "_default_store", ev_store, raising=False)
    monkeypatch.setattr(tone_store_mod, "_default_tone_store", t_store, raising=False)
    return ev_store, t_store


def test_ai_context_includes_context_only_warning(seeded):
    ev_store, t_store = seeded
    ev_store.upsert_event({
        "id": "FOMC_STATEMENT:ai", "event_type": "FOMC_STATEMENT", "currency": "USD",
        "title": "FOMC statement", "scheduled_time_utc": "2026-06-17T18:00:00+00:00",
        "status": "RELEASED", "source": "fed_press_monetary_rss",
        "raw_text": STRONG_HAWKISH,
    })
    process_event("FOMC_STATEMENT:ai", store=ev_store, tone_store=t_store)

    ctx = build_macro_prompt_context("XAU/USD", "commodity")
    assert "fomcTone" in ctx
    assert ctx["fomcTone"]["executionUse"] == "CONTEXT_ONLY"

    block = render_macro_prompt_block("XAU/USD", "commodity")
    assert "FOMC tone is macro context only" in block
    assert "CONTEXT_ONLY" in block


def test_lockout_blocks_even_with_strong_directional_tone(seeded):
    ev_store, t_store = seeded
    # Strongly hawkish tone present...
    ev_store.upsert_event({
        "id": "FOMC_STATEMENT:tone", "event_type": "FOMC_STATEMENT", "currency": "USD",
        "title": "FOMC statement", "scheduled_time_utc": "2026-06-17T18:00:00+00:00",
        "status": "RELEASED", "source": "fed_press_monetary_rss",
        "raw_text": STRONG_HAWKISH,
    })
    process_event("FOMC_STATEMENT:tone", store=ev_store, tone_store=t_store)
    # ...and an active FOMC lockout window right now.
    T = datetime.now(timezone.utc) + timedelta(minutes=15)
    ev_store.upsert_event({
        "id": "FOMC_RATE_DECISION:live", "event_type": "FOMC_RATE_DECISION", "currency": "USD",
        "title": "FOMC rate decision", "scheduled_time_utc": T.isoformat(),
        "press_conference_time_utc": (T + timedelta(minutes=30)).isoformat(),
        "status": "SCHEDULED", "source": "fed_fomc_calendar",
    })

    risk = get_symbol_macro_risk("XAU/USD", store=ev_store)
    assert risk["macroRisk"] == "LOCKOUT"
    assert risk["blockNewTrades"] is True  # tone never unblocks a lockout

    block = render_macro_prompt_block("XAU/USD", "commodity")
    assert "MUST NOT output an execution-ready recommendation" in block
