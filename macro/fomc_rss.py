"""Official Fed monetary-policy RSS polling → FOMC_STATEMENT / FOMC_MINUTES events.

Polls https://www.federalreserve.gov/feeds/press_monetary.xml, detects FOMC-related
items by title/body terms, and stores them as released macro events. This RSS feed is
the *authoritative live trigger* for a released FOMC statement (FRED is confirmation
only — see macro/fomc_fred.py).

Uses the Python stdlib XML parser (no feedparser dependency). Network/parse failures
log a degraded-mode warning and return empty — never raising into scans/trading/UI.
Deduplication is by (event_type, published-day, stable link/guid).

CLI:  python -m macro.fomc_rss --poll
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from macro import config
from macro.store import MacroEventStore, default_store

log = logging.getLogger("macro.fomc_rss")

# Title/body terms that mark an item as FOMC-related.
_FOMC_TERMS = (
    "fomc",
    "federal open market committee",
    "monetary policy",
    "federal reserve issues fomc statement",
)


def _local(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _item_fields(el: ET.Element) -> dict[str, str]:
    f = {"title": "", "link": "", "description": "", "pubDate": "", "guid": ""}
    for child in el:
        ln = _local(child.tag)
        text = (child.text or "").strip()
        if ln == "title":
            f["title"] = text
        elif ln == "link":
            f["link"] = text or child.get("href", "")
        elif ln in ("description", "summary", "content"):
            f["description"] = text or f["description"]
        elif ln in ("pubdate", "published", "updated"):
            f["pubDate"] = text or f["pubDate"]
        elif ln == "guid":
            f["guid"] = text
        elif ln == "id" and not f["guid"]:
            f["guid"] = text
    return f


def parse_rss(xml_text: str | bytes) -> list[dict[str, str]]:
    """Parse RSS/Atom into a list of raw item dicts. Tolerant of namespaces."""
    if not xml_text:
        return []
    try:
        data = xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text
        if data[:3] == b"\xef\xbb\xbf":  # strip UTF-8 BOM (Fed feed ships one)
            data = data[3:]
        root = ET.fromstring(data)
    except Exception as exc:
        log.warning("[FOMC-RSS] xml parse failed (degraded mode): %s", exc)
        return []
    items: list[dict[str, str]] = []
    for el in root.iter():
        if _local(el.tag) in ("item", "entry"):
            items.append(_item_fields(el))
    return items


def classify_item(title: str, description: str) -> tuple[bool, str]:
    """Return (is_fomc, event_type). Non-FOMC items return (False, '')."""
    text = f"{title} {description}".lower()
    if not any(term in text for term in _FOMC_TERMS):
        return False, ""
    if "minutes" in text:
        return True, "FOMC_MINUTES"
    return True, "FOMC_STATEMENT"


def _parse_pubdate(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        # Atom ISO-8601 fallback
        try:
            iso = value.strip()
            if iso.endswith("Z"):
                iso = iso[:-1] + "+00:00"
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _stable_key(item: dict[str, str]) -> str:
    raw = (item.get("guid") or item.get("link") or item.get("title") or "").strip()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def build_event_from_item(item: dict[str, str], now: datetime | None = None) -> dict[str, Any] | None:
    is_fomc, event_type = classify_item(item.get("title", ""), item.get("description", ""))
    if not is_fomc:
        return None
    published = _parse_pubdate(item.get("pubDate", "")) or (now or config.now_utc())
    pub_day = published.date().isoformat()
    importance = "MEDIUM" if event_type == "FOMC_MINUTES" else "HIGH"
    raw_text = re.sub(r"<[^>]+>", " ", item.get("description") or "").strip()[:2000] or None
    return {
        "id": f"{event_type}:{pub_day}:{_stable_key(item)}",
        "source": "fed_press_monetary_rss",
        "event_type": event_type,
        "country": "US",
        "currency": "USD",
        "title": (item.get("title") or "FOMC statement").strip()[:300],
        "scheduled_time_utc": published.isoformat(),
        "press_conference_time_utc": None,
        "importance": importance,
        "status": "RELEASED",
        "raw_url": (item.get("link") or "").strip() or None,
        "raw_text": raw_text,
        "time_source": "exact",
        "_published_day": pub_day,
        "_event_type": event_type,
    }


def fetch_rss(url: str | None = None) -> bytes | None:
    target = url or config.rss_url()
    try:
        from feed_http import feed_get

        resp = feed_get(target)
        resp.raise_for_status()
        # Return raw bytes: the Fed feed declares encoding="utf-8" with a BOM but sends
        # no charset header, so requests mis-guesses ISO-8859-1 for .text. Bytes let the
        # XML parser honor the declared encoding.
        return resp.content
    except Exception as exc:
        log.warning("[FOMC-RSS] rss fetch failed (degraded mode): %s", exc)
        return None


def ingest_rss(
    store: MacroEventStore | None = None,
    *,
    xml: str | bytes | None = None,
    url: str | None = None,
    now: datetime | None = None,
    confirm_fred: bool = True,
) -> dict[str, Any]:
    store = store or default_store()
    if xml is None:
        xml = fetch_rss(url)
    if not xml:
        return {"ok": False, "reason": "no_xml", "inserted": 0, "updated": 0, "matched": 0}
    items = parse_rss(xml)
    events: list[dict[str, Any]] = []
    released_days: list[str] = []
    statement_ids: list[str] = []
    for raw in items:
        ev = build_event_from_item(raw, now=now)
        if ev is None:
            continue
        if ev.pop("_event_type", "") == "FOMC_STATEMENT":
            released_days.append(str(ev.pop("_published_day", "")))
            statement_ids.append(str(ev.get("id")))
        else:
            ev.pop("_published_day", None)
        events.append(ev)
    counts = store.upsert_events(events)
    counts["ok"] = True
    counts["matched"] = len(events)
    # Flip any same-day scheduled rate-decision row to RELEASED (numbers via FRED).
    for day in {d for d in released_days if d}:
        try:
            store.mark_scheduled_released(day)
        except Exception as exc:
            log.debug("[FOMC-RSS] mark_scheduled_released skipped for %s: %s", day, exc)
    if confirm_fred and events:
        try:
            from macro.fomc_fred import confirm_recent_events

            confirm_recent_events(store, now=now)
        except Exception as exc:
            log.debug("[FOMC-RSS] FRED confirmation skipped: %s", exc)
    # Best-effort V2 tone scoring (CONTEXT only; never blocks ingest).
    for sid in statement_ids:
        try:
            from macro.fomc_tone import process_event

            process_event(sid, store=store, now=now, fetch_text=True)
        except Exception as exc:
            log.debug("[FOMC-RSS] tone scoring skipped for %s: %s", sid, exc)
    log.info(
        "[FOMC-RSS] poll ok: %d FOMC items (%s inserted, %s updated)",
        len(events), counts.get("inserted"), counts.get("updated"),
    )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FOMC monetary-policy RSS poll")
    parser.add_argument("--poll", action="store_true", help="fetch + ingest live RSS")
    parser.add_argument("--url", default=None, help="override RSS URL")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.poll:
        parser.print_help()
        return 0
    result = ingest_rss(url=args.url)
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
