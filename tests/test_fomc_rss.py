"""Offline tests for the FOMC monetary-policy RSS ingestion (no network)."""

from __future__ import annotations

import os
import tempfile

from macro.fomc_rss import build_event_from_item, classify_item, ingest_rss, parse_rss
from macro.store import MacroEventStore

_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Federal Reserve issues FOMC statement</title>
    <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm</link>
    <description>The Federal Open Market Committee decided to maintain the target range.</description>
    <pubDate>Wed, 17 Jun 2026 18:00:00 GMT</pubDate>
    <guid>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm</guid>
  </item>
  <item>
    <title>Minutes of the Federal Open Market Committee, April 28-29, 2026</title>
    <link>https://www.federalreserve.gov/monetarypolicy/fomcminutes20260429.htm</link>
    <description>The Federal Open Market Committee minutes.</description>
    <pubDate>Wed, 20 May 2026 18:00:00 GMT</pubDate>
    <guid>fomc-minutes-2026-04</guid>
  </item>
  <item>
    <title>Speech by Chair on the economic outlook</title>
    <link>https://www.federalreserve.gov/newsevents/speech/sample.htm</link>
    <description>Remarks on community development and the labor market.</description>
    <pubDate>Mon, 15 Jun 2026 14:00:00 GMT</pubDate>
    <guid>speech-sample</guid>
  </item>
</channel></rss>
"""


def _store():
    d = tempfile.mkdtemp(prefix="macro_rss_")
    return MacroEventStore(os.path.join(d, "m.db"))


def test_parse_rss_returns_all_items():
    items = parse_rss(_SAMPLE_RSS)
    assert len(items) == 3
    assert items[0]["title"] == "Federal Reserve issues FOMC statement"
    assert items[0]["guid"].endswith("monetary20260617a.htm")


def test_classify_item_distinguishes_statement_minutes_and_nonfomc():
    assert classify_item("Federal Reserve issues FOMC statement", "") == (True, "FOMC_STATEMENT")
    assert classify_item("Minutes of the Federal Open Market Committee", "") == (True, "FOMC_MINUTES")
    # Unrelated speech with no FOMC term is rejected.
    assert classify_item("Speech by Chair on the economic outlook", "labor market") == (False, "")


def test_build_event_from_item_shape():
    items = parse_rss(_SAMPLE_RSS)
    ev = build_event_from_item(items[0])
    assert ev is not None
    assert ev["event_type"] == "FOMC_STATEMENT"
    assert ev["status"] == "RELEASED"
    assert ev["currency"] == "USD"
    assert ev["time_source"] == "exact"
    assert ev["scheduled_time_utc"].startswith("2026-06-17T18:00:00")


def test_ingest_rss_flips_same_day_scheduled_to_released():
    store = _store()
    # Pre-seed a scheduled rate decision on the same UTC day as the RSS statement.
    store.upsert_event(
        {
            "id": "FOMC_RATE_DECISION:2026-06-17",
            "event_type": "FOMC_RATE_DECISION",
            "currency": "USD",
            "title": "FOMC rate decision (2026-06-17)",
            "scheduled_time_utc": "2026-06-17T18:00:00+00:00",
            "status": "SCHEDULED",
            "source": "fed_fomc_calendar",
        }
    )
    counts = ingest_rss(store, xml=_SAMPLE_RSS, confirm_fred=False)
    assert counts["ok"] is True
    # Only the statement + minutes are FOMC; the speech is ignored.
    assert counts["matched"] == 2
    decision = store.get_event("FOMC_RATE_DECISION:2026-06-17")
    assert decision is not None
    assert decision["status"] == "RELEASED"
