"""Macro-risk guard: turns stored FOMC events into a lockout/caution state machine.

Pure, time-driven logic over the local macro store. It blocks/downgrades NEW trade
candidates during FOMC windows; it never touches Engine A/B/D score math, SL/TP,
sizing, or execution code, and it cannot enable auto execution. Auto-execution
blocking is delegated to the existing ``majorEventRisk.blocksAutoExecution`` contract
(see macro/scan_integration.py).

Risk states: NONE, UPCOMING, LOCKOUT, ACTIVE_RELEASE, POST_RELEASE_CAUTION, COMPLETED.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from macro import config
from macro.store import MacroEventStore, default_store

log = logging.getLogger("macro.guard")

# Event types that drive a lockout (speeches/minutes do not hard-block in V1).
LOCKOUT_EVENT_TYPES = ("FOMC_RATE_DECISION", "FOMC_STATEMENT")
BLOCKING_STATES = ("LOCKOUT", "ACTIVE_RELEASE")
DOWNGRADE_STATES = ("LOCKOUT", "ACTIVE_RELEASE", "POST_RELEASE_CAUTION")

_SEVERITY = {
    "NONE": 0,
    "COMPLETED": 1,
    "UPCOMING": 2,
    "POST_RELEASE_CAUTION": 3,
    "LOCKOUT": 4,
    "ACTIVE_RELEASE": 5,
}

AFFECTED_GROUP_LABELS = [
    "USD forex pairs",
    "XAU/USD",
    "XAG/USD",
    "US indices",
    "BTC/USDT",
    "ETH/USDT",
    "Major USD/USDT crypto",
    "DXY",
]

AI_MACRO_INSTRUCTION = (
    "FOMC macro event is active/upcoming. Do not treat normal technical confluence as "
    "fully reliable. Do not recommend immediate execution during lockout. Focus on "
    "post-news structure, liquidity sweep/retest confirmation, and risk reduction."
)

# US index display names / tickers (FOMC-affected). Non-US indices are excluded.
_US_INDEX_NAMES = {
    "S&P 500", "SPX", "SPY", "US500", "NASDAQ-100", "NASDAQ", "NDX", "QQQ", "NAS100",
    "DOW JONES", "DJIA", "US30", "RUSSELL 2000", "IWM", "US TECH 100",
}
_NON_US_INDEX_NAMES = {
    "UK100", "DAX 40", "DAX", "ASX 200", "NIKKEI 225", "NIKKEI", "EURO STOXX 50",
    "HANG SENG", "CAC 40", "IBEX 35",
}
_PRECIOUS_USD = {"XAU/USD", "XAG/USD", "XPT/USD", "XPD/USD"}


# ── time parsing ────────────────────────────────────────────────────────────────
def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── affected-symbol logic ───────────────────────────────────────────────────────
def is_symbol_affected(symbol: str | None, asset_type: str | None = None) -> bool:
    """True when an FOMC/USD macro event materially affects this instrument."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    a = str(asset_type or "").strip().lower()
    compact = sym.replace(" ", "")

    if "DXY" in {sym, compact} or sym.endswith("/DXY") or compact == "USDOLLARINDEX":
        return True
    if sym in _PRECIOUS_USD:
        return True
    # US indices (explicit allow-list; never non-US)
    if sym in _US_INDEX_NAMES or compact in {n.replace(" ", "") for n in _US_INDEX_NAMES}:
        return True
    if sym in _NON_US_INDEX_NAMES:
        return False

    # FX-style pair with a USD leg
    legs = sym.split("/")
    if len(legs) == 2 and all(len(p) == 3 and p.isalpha() for p in legs):
        if "USD" in legs:
            # crypto vs forex: USDT handled below; pure 3-letter fiat USD pair is forex
            return True
    # Crypto majors quoted in USDT/USD
    if config.affected_crypto():
        if sym.endswith("/USDT") or sym.endswith("USDT"):
            return True
        if a == "crypto" and (sym.endswith("/USD") or sym.endswith("USD")):
            return True
    if a == "forex" and "USD" in sym:
        return True
    return False


# ── per-event window + state ────────────────────────────────────────────────────
def _event_windows(event: dict[str, Any]) -> tuple[datetime, datetime, datetime, datetime] | None:
    T = _parse_iso(event.get("scheduled_time_utc"))
    if T is None:
        return None
    pre = timedelta(minutes=config.pre_lockout_min())
    post = timedelta(minutes=config.post_lockout_min())
    caution = timedelta(minutes=config.extended_caution_min())
    lockout_start = T - pre
    hard_end = T + post
    P = _parse_iso(event.get("press_conference_time_utc"))
    if P is not None:
        presser_block_end = P + timedelta(minutes=config.presser_post_lockout_min())
        presser_block_start = P - timedelta(minutes=config.presser_pre_lockout_min())
        hard_end = max(hard_end, presser_block_end)
        lockout_start = min(lockout_start, presser_block_start)
    caution_end = max(T + caution, hard_end)
    return T, lockout_start, hard_end, caution_end


def _event_state(event: dict[str, Any], now: datetime) -> str:
    win = _event_windows(event)
    if win is None:
        return "NONE"
    T, lockout_start, hard_end, caution_end = win
    if now < lockout_start:
        horizon = timedelta(minutes=config.upcoming_horizon_min())
        return "UPCOMING" if now >= lockout_start - horizon else "NONE"
    if lockout_start <= now < T:
        return "LOCKOUT"
    if T <= now < hard_end:
        return "ACTIVE_RELEASE"
    if hard_end <= now < caution_end:
        return "POST_RELEASE_CAUTION"
    return "COMPLETED"


def _is_active_event(event: dict[str, Any]) -> bool:
    if str(event.get("event_type")) not in LOCKOUT_EVENT_TYPES:
        return False
    return str(event.get("status", "")).upper() not in ("CANCELLED", "ERROR")


# ── payload assembly ────────────────────────────────────────────────────────────
def _none_payload(now: datetime, *, reason: str, next_event: dict | None = None) -> dict[str, Any]:
    payload = {
        "macroRisk": "NONE",
        "eventType": None,
        "title": None,
        "currency": "USD",
        "blockNewTrades": False,
        "confidenceDowngrade": False,
        "allowAiReview": True,
        "allowManualWatchlist": True,
        "allowAutoExecution": True,
        "scheduledTimeUtc": None,
        "pressConferenceTimeUtc": None,
        "lockoutStartUtc": None,
        "lockoutEndUtc": None,
        "cautionEndUtc": None,
        "timeToEventSec": None,
        "timeSinceReleaseSec": None,
        "source": None,
        "status": None,
        "affectedSymbolsGroups": list(AFFECTED_GROUP_LABELS),
        "reason": reason,
        "nowUtc": now.isoformat(),
    }
    if next_event is not None:
        T = _parse_iso(next_event.get("scheduled_time_utc"))
        payload.update(
            {
                "eventType": next_event.get("event_type"),
                "title": next_event.get("title"),
                "scheduledTimeUtc": next_event.get("scheduled_time_utc"),
                "pressConferenceTimeUtc": next_event.get("press_conference_time_utc"),
                "source": next_event.get("source"),
                "status": next_event.get("status"),
                "timeToEventSec": int((T - now).total_seconds()) if T and T > now else None,
            }
        )
    return payload


_REASONS = {
    "LOCKOUT": "FOMC macro lockout active (pre-release). Normal technical signals are "
               "downgraded; new entries blocked and auto-execution disabled.",
    "ACTIVE_RELEASE": "FOMC release window active. Elevated volatility; new entries blocked "
                      "and auto-execution disabled.",
    "POST_RELEASE_CAUTION": "FOMC post-release caution. Treat technical confluence as less "
                            "reliable; prefer post-news structure and reduced risk.",
    "UPCOMING": "FOMC event upcoming. Prepare for macro lockout; confidence downgraded near release.",
}


def _build_payload(event: dict[str, Any], state: str, now: datetime) -> dict[str, Any]:
    win = _event_windows(event)
    T = _parse_iso(event.get("scheduled_time_utc"))
    lockout_start = win[1] if win else None
    hard_end = win[2] if win else None
    caution_end = win[3] if win else None
    blocking = state in BLOCKING_STATES
    time_to = int((T - now).total_seconds()) if T and T > now else None
    time_since = int((now - T).total_seconds()) if T and T <= now else None
    return {
        "macroRisk": state,
        "eventType": event.get("event_type"),
        "title": event.get("title"),
        "currency": event.get("currency") or "USD",
        "blockNewTrades": blocking,
        "confidenceDowngrade": state in DOWNGRADE_STATES,
        "allowAiReview": True,
        "allowManualWatchlist": True,
        "allowAutoExecution": not blocking,
        "scheduledTimeUtc": event.get("scheduled_time_utc"),
        "pressConferenceTimeUtc": event.get("press_conference_time_utc"),
        "lockoutStartUtc": lockout_start.isoformat() if lockout_start else None,
        "lockoutEndUtc": hard_end.isoformat() if hard_end else None,
        "cautionEndUtc": caution_end.isoformat() if caution_end else None,
        "timeToEventSec": time_to,
        "timeSinceReleaseSec": time_since,
        "source": event.get("source"),
        "status": event.get("status"),
        "affectedSymbolsGroups": list(AFFECTED_GROUP_LABELS),
        "reason": _REASONS.get(state, "No active FOMC macro risk."),
        "nowUtc": now.isoformat(),
    }


# ── public API ──────────────────────────────────────────────────────────────────
def get_active_macro_state(
    now: datetime | None = None, store: MacroEventStore | None = None
) -> dict[str, Any]:
    now = now or config.now_utc()
    if not config.enabled():
        return _none_payload(now, reason="FOMC macro layer disabled.")
    try:
        store = store or default_store()
    except Exception as exc:  # store unavailable → safe NONE
        log.debug("[MACRO] store unavailable: %s", exc)
        return _none_payload(now, reason="Macro store unavailable.")

    horizon = config.upcoming_horizon_min()
    lookback_min = max(config.extended_caution_min(), 24 * 60) + 120
    lookahead_min = horizon + config.pre_lockout_min() + 120
    start = (now - timedelta(minutes=lookback_min)).isoformat()
    end = (now + timedelta(minutes=lookahead_min)).isoformat()

    best_event: dict | None = None
    best_state = "NONE"
    best_rank: tuple[int, float] = (-1, 0.0)
    for ev in store.events_between(start, end):
        if not _is_active_event(ev):
            continue
        state = _event_state(ev, now)
        if state in ("NONE", "COMPLETED"):
            continue
        T = _parse_iso(ev.get("scheduled_time_utc"))
        closeness = -abs((T - now).total_seconds()) if T else float("-inf")
        rank = (_SEVERITY[state], closeness)
        if rank > best_rank:
            best_rank, best_state, best_event = rank, state, ev

    if best_event is not None:
        return _build_payload(best_event, best_state, now)

    # No active window — surface the next upcoming meeting for the UI countdown.
    upcoming = store.list_events(
        event_types=LOCKOUT_EVENT_TYPES,
        statuses=("SCHEDULED", "ACTIVE"),
        start_iso=now.isoformat(),
        limit=1,
    )
    return _none_payload(
        now,
        reason="No active FOMC macro risk.",
        next_event=upcoming[0] if upcoming else None,
    )


def get_symbol_macro_risk(
    symbol: str | None,
    now: datetime | None = None,
    *,
    asset_type: str | None = None,
    store: MacroEventStore | None = None,
) -> dict[str, Any]:
    state = get_active_macro_state(now=now, store=store)
    affected = is_symbol_affected(symbol, asset_type)
    out = dict(state)
    out["symbol"] = symbol
    out["affected"] = affected
    if not affected:
        # Global FOMC risk is still reported, but an unaffected instrument is never
        # blocked or downgraded by it.
        out["blockNewTrades"] = False
        out["confidenceDowngrade"] = False
        out["allowAutoExecution"] = True
    return out


def should_block_new_trade(
    symbol: str | None,
    now: datetime | None = None,
    *,
    asset_type: str | None = None,
    store: MacroEventStore | None = None,
) -> bool:
    return bool(
        get_symbol_macro_risk(symbol, now=now, asset_type=asset_type, store=store).get(
            "blockNewTrades"
        )
    )


def macro_context_for_ai(
    symbol: str | None,
    now: datetime | None = None,
    *,
    asset_type: str | None = None,
    store: MacroEventStore | None = None,
) -> dict[str, Any]:
    risk = get_symbol_macro_risk(symbol, now=now, asset_type=asset_type, store=store)
    active = risk.get("macroRisk") not in ("NONE", "COMPLETED")
    return {
        "available": True,
        "macroEventActive": bool(active),
        "macroRisk": risk.get("macroRisk"),
        "eventType": risk.get("eventType"),
        "title": risk.get("title"),
        "currency": risk.get("currency"),
        "affected": risk.get("affected"),
        "blockNewTrades": risk.get("blockNewTrades"),
        "confidenceDowngrade": risk.get("confidenceDowngrade"),
        "allowAiReview": True,
        "scheduledTimeUtc": risk.get("scheduledTimeUtc"),
        "timeToEventSec": risk.get("timeToEventSec"),
        "timeSinceReleaseSec": risk.get("timeSinceReleaseSec"),
        "lockoutEndUtc": risk.get("lockoutEndUtc"),
        "cautionEndUtc": risk.get("cautionEndUtc"),
        "reason": risk.get("reason"),
        "instruction": AI_MACRO_INSTRUCTION,
    }


def macro_context_for_ui(
    now: datetime | None = None, store: MacroEventStore | None = None
) -> dict[str, Any]:
    return get_active_macro_state(now=now, store=store)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Macro guard state")
    parser.add_argument("--state", nargs="?", const="", default=None,
                        help="print macro state (optionally for a symbol)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.state is None:
        parser.print_help()
        return 0
    import json

    if args.state:
        print(json.dumps(get_symbol_macro_risk(args.state), indent=2, default=str))
    else:
        print(json.dumps(get_active_macro_state(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
