"""Offline tests for the FOMC calendar ingestion (no network, no live website)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from macro import config
from macro.fomc_calendar import build_events, ingest_calendar, parse_calendar_html
from macro.store import MacroEventStore

# Minimal sample mirroring the federalreserve.gov calendar block structure.
_SAMPLE_HTML = """
<html><body>
<h4>2026 FOMC Meetings</h4>
<div class="fomc-meeting__month">January</div>
<div class="fomc-meeting__date">27-28</div>
<div class="fomc-meeting__month">April/May</div>
<div class="fomc-meeting__date">28-29</div>
<div class="fomc-meeting__month">June</div>
<div class="fomc-meeting__date">16-17</div>
</body></html>
"""


def _store():
    d = tempfile.mkdtemp(prefix="macro_cal_")
    return MacroEventStore(os.path.join(d, "m.db"))


def test_parse_calendar_html_extracts_decision_dates():
    dates = parse_calendar_html(_SAMPLE_HTML)
    # Decision day = LAST day of each meeting; cross-month resolves to second month.
    assert dates == ["2026-01-28", "2026-05-29", "2026-06-17"]


def test_build_events_uses_default_release_and_presser_times():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = build_events(["2026-06-17"], now=now)
    assert len(events) == 1
    ev = events[0]
    # 14:00 ET in June (EDT, -4) → 18:00 UTC; presser 14:30 ET → 18:30 UTC.
    assert ev["scheduled_time_utc"] == config.eastern_to_utc(
        datetime(2026, 6, 17, 14, 0)
    ).isoformat()
    assert ev["press_conference_time_utc"] == config.eastern_to_utc(
        datetime(2026, 6, 17, 14, 30)
    ).isoformat()
    assert ev["event_type"] == "FOMC_RATE_DECISION"
    assert ev["currency"] == "USD"
    assert ev["time_source"] == "assumed_default"
    assert ev["status"] == "SCHEDULED"  # future relative to `now`


def test_ingest_calendar_offline_is_idempotent():
    store = _store()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = ingest_calendar(store, html=_SAMPLE_HTML, now=now, confirm_fred=False)
    assert first["ok"] is True
    assert first["dates"] == 3
    assert first["inserted"] == 3
    count_after_first = store.count()
    # Re-running must update in place, never duplicate.
    second = ingest_calendar(store, html=_SAMPLE_HTML, now=now, confirm_fred=False)
    assert second["ok"] is True
    assert second["inserted"] == 0
    assert second["updated"] == 3
    assert store.count() == count_after_first == 3
