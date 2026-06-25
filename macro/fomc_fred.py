"""FRED historical confirmation for FOMC events (confirmation only — never the trigger).

Reuses the EXISTING FRED ingestion path via ``carry_feed.get_fred_latest_rate`` (which is
backed by the shared ``carry_cache.db``). No new FRED client, no second data path.

For a decision that has already occurred it fills the previous/actual federal-funds target
range and a realized ``surprise_bps`` (change in target midpoint, in basis points). When FRED
values are not confidently available the fields stay null and a non-fatal warning is logged.

Series (all via the shared client):
* DFEDTARU — Federal Funds Target Range Upper Limit
* DFEDTARL — Federal Funds Target Range Lower Limit
* FEDFUNDS — Effective Federal Funds Rate (optional sanity confirmation)

CLI:  python -m macro.fomc_fred --confirm
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from macro import config
from macro.store import MacroEventStore, default_store

log = logging.getLogger("macro.fomc_fred")

SERIES_TARGET_UPPER = "DFEDTARU"
SERIES_TARGET_LOWER = "DFEDTARL"
SERIES_EFFECTIVE = "FEDFUNDS"

# Event types that have an associated policy-rate decision worth confirming.
_CONFIRMABLE = ("FOMC_RATE_DECISION", "FOMC_STATEMENT")


def _fred(series_id: str, as_of: str, *, blocking: bool) -> float | None:
    try:
        from carry_feed import get_fred_latest_rate

        val = get_fred_latest_rate(series_id, as_of, blocking=blocking)
        return float(val) if val is not None else None
    except Exception as exc:  # reuse path unavailable → non-fatal
        log.debug("[FOMC-FRED] %s lookup failed: %s", series_id, exc)
        return None


def confirm_event(
    store: MacroEventStore,
    event: dict[str, Any],
    *,
    now: datetime | None = None,
    blocking: bool = False,
) -> dict[str, Any] | None:
    """Fill previous/actual target range + surprise_bps for one past decision event.

    Returns the merged update dict applied to the store, or None when nothing was confirmed.
    """
    if str(event.get("event_type")) not in _CONFIRMABLE:
        return None
    sched = str(event.get("scheduled_time_utc") or "")
    if not sched:
        return None
    try:
        d = datetime.fromisoformat(sched)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    now = now or config.now_utc()
    if d > now:
        return None  # future meeting — nothing to confirm yet

    decision_day = d.date()
    prev_day = (decision_day - timedelta(days=1)).isoformat()
    # Range becomes effective the business day after the announcement; look a few days
    # forward but never past 'now' (FRED will not have a future observation).
    actual_target = min(decision_day + timedelta(days=3), now.date())
    actual_day = actual_target.isoformat()

    prev_lower = _fred(SERIES_TARGET_LOWER, prev_day, blocking=blocking)
    prev_upper = _fred(SERIES_TARGET_UPPER, prev_day, blocking=blocking)
    actual_lower = actual_upper = None
    if actual_target > decision_day:
        actual_lower = _fred(SERIES_TARGET_LOWER, actual_day, blocking=blocking)
        actual_upper = _fred(SERIES_TARGET_UPPER, actual_day, blocking=blocking)

    surprise_bps = None
    if None not in (prev_lower, prev_upper, actual_lower, actual_upper):
        prev_mid = (prev_lower + prev_upper) / 2.0  # type: ignore[operator]
        actual_mid = (actual_lower + actual_upper) / 2.0  # type: ignore[operator]
        # V1: realized change in target midpoint (no market-expectations source yet).
        surprise_bps = round((actual_mid - prev_mid) * 100.0, 1)

    if all(v is None for v in (prev_lower, prev_upper, actual_lower, actual_upper)):
        log.debug("[FOMC-FRED] no FRED values yet for %s (left null)", event.get("id"))
        return None

    update = {
        "id": event.get("id"),
        "previous_lower": prev_lower,
        "previous_upper": prev_upper,
        "actual_lower": actual_lower,
        "actual_upper": actual_upper,
        "surprise_bps": surprise_bps,
    }
    store.upsert_event(update)
    return update


def confirm_recent_events(
    store: MacroEventStore | None = None,
    *,
    now: datetime | None = None,
    lookback_days: int = 21,
    blocking: bool = False,
) -> dict[str, int]:
    """Confirm recent past decisions that are missing actual target-range numbers."""
    store = store or default_store()
    now = now or config.now_utc()
    start = (now - timedelta(days=lookback_days)).isoformat()
    confirmed = 0
    scanned = 0
    for ev in store.list_events(
        event_types=_CONFIRMABLE, start_iso=start, end_iso=now.isoformat()
    ):
        if ev.get("actual_lower") is not None and ev.get("actual_upper") is not None:
            continue
        scanned += 1
        try:
            if confirm_event(store, ev, now=now, blocking=blocking):
                confirmed += 1
        except Exception as exc:
            log.debug("[FOMC-FRED] confirm failed for %s: %s", ev.get("id"), exc)
    return {"scanned": scanned, "confirmed": confirmed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FOMC FRED rate confirmation")
    parser.add_argument("--confirm", action="store_true", help="confirm recent past decisions")
    parser.add_argument("--lookback-days", type=int, default=21)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.confirm:
        parser.print_help()
        return 0
    result = confirm_recent_events(lookback_days=args.lookback_days, blocking=True)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
