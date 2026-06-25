"""Official Fed FOMC calendar ingestion → FOMC_RATE_DECISION events.

Pulls https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm, extracts the
upcoming meeting dates, and stores each as a FOMC_RATE_DECISION at the configurable
default release time (14:00 ET) with a 14:30 ET press conference. Times are converted
to UTC before storage and tagged ``time_source='assumed_default'`` (the calendar page
does not publish exact release clock times).

Fail-safe: a missing/unreachable page or a structure change logs a degraded-mode
warning and ingests nothing — it never raises into scans/trading/UI.

CLI:  python -m macro.fomc_calendar --refresh
"""

from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime
from typing import Any

from macro import config
from macro.store import MacroEventStore, default_store

log = logging.getLogger("macro.fomc_calendar")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_BLOCK_RE = re.compile(
    r"fomc-meeting__month[^>]*>(.*?)</div>.*?fomc-meeting__date[^>]*>(.*?)</div>",
    re.S | re.I,
)
_YEAR_RE = re.compile(r"(\d{4})\s+FOMC\s+Meetings", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
# Fallback: "Month[/Month] D-D" plain-text occurrences inside a year section.
_TEXT_RE = re.compile(
    r"([A-Z][a-z]{2,8})(?:\s*/\s*([A-Z][a-z]{2,8}))?\s+(\d{1,2})\s*[-–]\s*(\d{1,2})"
)


def _strip_tags(value: str) -> str:
    return _TAG_RE.sub(" ", value or "").replace("&nbsp;", " ").strip()


def _month_num(name: str) -> int | None:
    key = (name or "").strip()[:3].lower()
    return _MONTHS.get(key)


def _resolve_decision_date(year: int | None, month_txt: str, date_txt: str) -> str | None:
    """Return the meeting's decision-day date (YYYY-MM-DD) — the LAST day of the meeting."""
    if not year or not month_txt or not date_txt:
        return None
    months = [p for p in re.split(r"[/&]| and ", month_txt) if p.strip()]
    nums = re.findall(r"\d{1,2}", date_txt)
    if not months or not nums:
        return None
    last_day = int(nums[-1])
    if not (1 <= last_day <= 31):
        return None
    if len(months) >= 2:
        m1, m2 = _month_num(months[0]), _month_num(months[1])
        dec_month = m2 or m1
        dec_year = year + 1 if (m1 and m2 and m2 < m1) else year
    else:
        dec_month = _month_num(months[0])
        dec_year = year
    if not dec_month:
        return None
    return f"{dec_year:04d}-{dec_month:02d}-{last_day:02d}"


def _year_resolver(html: str):
    headings = [(m.start(), int(m.group(1))) for m in _YEAR_RE.finditer(html)]

    def year_for(pos: int) -> int | None:
        chosen = None
        for hpos, yr in headings:
            if hpos <= pos:
                chosen = yr
            else:
                break
        return chosen

    return year_for, headings


def parse_calendar_html(html: str) -> list[str]:
    """Extract sorted, unique FOMC decision-day dates (YYYY-MM-DD) from the page HTML."""
    if not html:
        return []
    year_for, headings = _year_resolver(html)
    dates: set[str] = set()

    for m in _BLOCK_RE.finditer(html):
        d = _resolve_decision_date(
            year_for(m.start()), _strip_tags(m.group(1)), _strip_tags(m.group(2))
        )
        if d:
            dates.add(d)

    if not dates and headings:
        # Tolerant fallback for older/plain-text layouts.
        for m in _TEXT_RE.finditer(html):
            month_txt = m.group(1) + ("/" + m.group(2) if m.group(2) else "")
            date_txt = f"{m.group(3)}-{m.group(4)}"
            d = _resolve_decision_date(year_for(m.start()), month_txt, date_txt)
            if d:
                dates.add(d)

    return sorted(dates)


def build_events(dates: list[str], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or config.now_utc()
    rh, rm = config.release_time_et()
    ph, pm = config.presser_time_et()
    events: list[dict[str, Any]] = []
    for d in dates:
        try:
            y, mo, day = (int(x) for x in d.split("-"))
            sched_utc = config.eastern_to_utc(datetime(y, mo, day, rh, rm))
            presser_utc = config.eastern_to_utc(datetime(y, mo, day, ph, pm))
        except (ValueError, TypeError):
            continue
        status = "SCHEDULED" if sched_utc >= now else "COMPLETED"
        events.append(
            {
                "id": f"FOMC_RATE_DECISION:{d}",
                "source": "fed_fomc_calendar",
                "event_type": "FOMC_RATE_DECISION",
                "country": "US",
                "currency": "USD",
                "title": f"FOMC rate decision ({d})",
                "scheduled_time_utc": sched_utc.isoformat(),
                "press_conference_time_utc": presser_utc.isoformat(),
                "importance": "HIGH",
                "status": status,
                "raw_url": config.calendar_url(),
                "raw_text": None,
                "time_source": "assumed_default",
            }
        )
    return events


def fetch_calendar_html(url: str | None = None) -> str | None:
    target = url or config.calendar_url()
    try:
        from feed_http import feed_get

        resp = feed_get(target)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        log.warning("[FOMC-CAL] calendar fetch failed (degraded mode): %s", exc)
        return None


def ingest_calendar(
    store: MacroEventStore | None = None,
    *,
    html: str | None = None,
    url: str | None = None,
    now: datetime | None = None,
    confirm_fred: bool = True,
) -> dict[str, Any]:
    store = store or default_store()
    if html is None:
        html = fetch_calendar_html(url)
    if not html:
        return {"ok": False, "reason": "no_html", "inserted": 0, "updated": 0, "dates": 0}
    dates = parse_calendar_html(html)
    if not dates:
        log.warning("[FOMC-CAL] parsed 0 meeting dates (page structure may have changed)")
        return {"ok": False, "reason": "no_dates", "inserted": 0, "updated": 0, "dates": 0}
    counts = store.upsert_events(build_events(dates, now=now))
    counts["ok"] = True
    counts["dates"] = len(dates)
    if confirm_fred:
        try:
            from macro.fomc_fred import confirm_recent_events

            confirm_recent_events(store, now=now)
        except Exception as exc:  # confirmation is best-effort, never fatal
            log.debug("[FOMC-CAL] FRED confirmation skipped: %s", exc)
    log.info(
        "[FOMC-CAL] ingest ok: %d dates (%s inserted, %s updated)",
        len(dates), counts.get("inserted"), counts.get("updated"),
    )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FOMC calendar ingestion")
    parser.add_argument("--refresh", action="store_true", help="fetch + ingest live calendar")
    parser.add_argument("--url", default=None, help="override calendar URL")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.refresh:
        parser.print_help()
        return 0
    result = ingest_calendar(url=args.url)
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
