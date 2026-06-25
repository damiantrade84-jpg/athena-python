"""Background scheduler for the macro FOMC feed — daemon thread (existing athena pattern).

* FOMC calendar: refreshed every ATHENA_FOMC_CALENDAR_REFRESH_HOURS (default daily).
* Monetary-policy RSS: polled slowly on normal days, fast inside/near an FOMC window.

Runs on its own daemon thread so it never blocks startup, scan locks, or market-data
refresh. Every iteration is wrapped fail-safe; a network/parse failure logs degraded mode
and the loop continues. Gated by ATHENA_FOMC_ENABLED + ATHENA_FOMC_SCHEDULER_ENABLED.

Manual alternatives (no scheduler required):
    python -m macro.fomc_calendar --refresh
    python -m macro.fomc_rss --poll
    python -m macro.fomc_fred --confirm
    python -m macro.macro_guard --state XAU/USD
"""

from __future__ import annotations

import logging
import threading
import time

from macro import config

log = logging.getLogger("macro.scheduler")

_started = False
_lock = threading.Lock()


def _refresh_calendar() -> None:
    try:
        from macro.fomc_calendar import ingest_calendar

        result = ingest_calendar()
        if not result.get("ok"):
            log.debug("[MACRO-SCHED] calendar refresh degraded: %s", result.get("reason"))
    except Exception as exc:
        log.debug("[MACRO-SCHED] calendar refresh failed: %s", exc)


def _poll_rss() -> None:
    try:
        from macro.fomc_rss import ingest_rss

        ingest_rss()
    except Exception as exc:
        log.debug("[MACRO-SCHED] rss poll failed: %s", exc)


def _in_fast_window() -> bool:
    """True when an FOMC window is active/imminent (poll RSS frequently)."""
    try:
        from macro.macro_guard import BLOCKING_STATES, get_active_macro_state

        state = get_active_macro_state()
        if state.get("macroRisk") in BLOCKING_STATES or state.get("macroRisk") == "UPCOMING":
            return True
        tte = state.get("timeToEventSec")
        return tte is not None and 0 <= tte <= 6 * 3600
    except Exception:
        return False


def _loop() -> None:
    last_cal = 0.0
    while True:
        try:
            refresh_secs = config.calendar_refresh_hours() * 3600
            now_mono = time.monotonic()
            if now_mono - last_cal >= refresh_secs:
                _refresh_calendar()
                last_cal = now_mono
            _poll_rss()
            interval = (
                config.rss_poll_fomc_sec() if _in_fast_window() else config.rss_poll_normal_sec()
            )
        except Exception as exc:
            log.debug("[MACRO-SCHED] loop iteration error: %s", exc)
            interval = config.rss_poll_normal_sec()
        time.sleep(max(15, int(interval)))


def start_macro_scheduler() -> bool:
    """Start the daemon scheduler once. Returns True if running, False if disabled."""
    global _started
    if not (config.enabled() and config.scheduler_enabled()):
        log.info("[MACRO-SCHED] disabled by config; scheduler not started")
        return False
    with _lock:
        if _started:
            return True
        _started = True
    threading.Thread(target=_loop, daemon=True, name="MacroFOMCScheduler").start()
    log.info(
        "[MACRO-SCHED] started (calendar %dh; rss %ds normal / %ds fomc)",
        config.calendar_refresh_hours(),
        config.rss_poll_normal_sec(),
        config.rss_poll_fomc_sec(),
    )
    return True
