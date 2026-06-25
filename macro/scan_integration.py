"""Attach macro-risk metadata to scan/trade candidates — additive only.

This NEVER changes Engine A/B/D score math, direction, SL/TP, RR, or sizing. It only
adds macro* metadata fields and, for an affected candidate inside a hard FOMC lockout,
escalates the EXISTING ``majorEventRisk.blocksAutoExecution`` contract already honored
by ``auto_trader.py`` (so auto execution is blocked through the unchanged gate). It can
never un-block a trade that another gate blocked.

Fields added per candidate:
    macroRisk, macroEventActive, macroBlockNewTrades, macroConfidenceDowngrade,
    macroReason, macroEventRisk (structured).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from macro import config
from macro.macro_guard import (
    BLOCKING_STATES,
    DOWNGRADE_STATES,
    get_active_macro_state,
    is_symbol_affected,
)

log = logging.getLogger("macro.scan")


def _latest_tone() -> dict[str, Any] | None:
    """Most recent stored FOMC tone (CONTEXT only). Fail-safe → None."""
    try:
        from macro.tone_store import default_tone_store

        return default_tone_store().latest()
    except Exception as exc:
        log.debug("[MACRO] tone lookup skipped: %s", exc)
        return None


def _signal_symbol(signal: dict[str, Any]) -> str:
    return str(
        signal.get("display")
        or signal.get("symbol")
        or signal.get("pair")
        or ""
    ).strip()


def _set_inactive(signal: dict[str, Any]) -> None:
    signal["macroRisk"] = "NONE"
    signal["macroEventActive"] = False
    signal["macroBlockNewTrades"] = False
    signal["macroConfidenceDowngrade"] = False
    signal["macroReason"] = None


def _escalate_major_event_risk(signal: dict[str, Any], state_payload: dict[str, Any]) -> None:
    """Reuse the existing majorEventRisk block contract (auto_trader.py honors it)."""
    macro_reason = "FOMC_MACRO_LOCKOUT: " + str(state_payload.get("reason") or "FOMC window")
    mer = signal.get("majorEventRisk")
    mer = dict(mer) if isinstance(mer, dict) else {}
    existing_reason = mer.get("reason")
    mer["blocksAutoExecution"] = True
    mer["majorEventDetected"] = True
    mer["reason"] = (
        f"{existing_reason} | {macro_reason}" if existing_reason else macro_reason
    )
    mer["macroBlock"] = True
    mer["macroRisk"] = state_payload.get("macroRisk")
    signal["majorEventRisk"] = mer


def _attach_tone(signal: dict[str, Any], tone: dict[str, Any] | None) -> None:
    """Additive macro CONTEXT only. Never touches confluenceScore or any engine score."""
    if not tone:
        signal["fomcToneLabel"] = None
        signal["fomcToneScore"] = None
        signal["fomcToneConfidence"] = None
        signal["fomcToneDirection"] = None
        signal["fomcToneWarnings"] = []
        signal["fomcExecutionUse"] = "CONTEXT_ONLY"
        return
    import json

    score = tone.get("tone_score")
    direction = (
        "hawkish" if isinstance(score, (int, float)) and score > 10
        else "dovish" if isinstance(score, (int, float)) and score < -10
        else "neutral"
    )
    warnings = tone.get("warnings_json")
    if isinstance(warnings, str):
        try:
            warnings = json.loads(warnings)
        except (ValueError, TypeError):
            warnings = []
    signal["fomcToneLabel"] = tone.get("tone_label")
    signal["fomcToneScore"] = score
    signal["fomcToneConfidence"] = tone.get("confidence")
    signal["fomcToneDirection"] = direction
    signal["fomcToneWarnings"] = warnings or []
    signal["fomcExecutionUse"] = "CONTEXT_ONLY"


def annotate_signal(
    signal: dict[str, Any], state_payload: dict[str, Any], *, block_auto_exec: bool,
    tone: dict[str, Any] | None = None,
) -> None:
    if not isinstance(signal, dict):
        return
    _attach_tone(signal, tone)
    global_state = str(state_payload.get("macroRisk") or "NONE")
    if global_state in ("NONE", "COMPLETED"):
        _set_inactive(signal)
        return

    affected = is_symbol_affected(
        _signal_symbol(signal), signal.get("type") or signal.get("asset_type")
    )
    if not affected:
        _set_inactive(signal)
        return

    blocking = global_state in BLOCKING_STATES
    downgrade = global_state in DOWNGRADE_STATES
    signal["macroRisk"] = global_state
    signal["macroEventActive"] = True
    signal["macroBlockNewTrades"] = bool(blocking)
    signal["macroConfidenceDowngrade"] = bool(downgrade)
    signal["macroReason"] = state_payload.get("reason")
    signal["macroEventRisk"] = {
        "source": "fomc_macro_guard",
        "macroRisk": global_state,
        "eventType": state_payload.get("eventType"),
        "blockNewTrades": bool(blocking),
        "blocksAutoExecution": bool(blocking and block_auto_exec),
        "allowAiReview": True,
        "allowManualWatchlist": True,
        "allowAutoExecution": not blocking,
        "scheduledTimeUtc": state_payload.get("scheduledTimeUtc"),
        "lockoutEndUtc": state_payload.get("lockoutEndUtc"),
        "cautionEndUtc": state_payload.get("cautionEndUtc"),
        "reason": state_payload.get("reason"),
    }
    if blocking and block_auto_exec:
        _escalate_major_event_risk(signal, state_payload)


def annotate_signals(
    signals: list[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
    state_payload: dict[str, Any] | None = None,
) -> int:
    """Annotate a list of candidate dicts in place. Returns the count annotated.

    Cheap when there is no active FOMC window (single guard call, then flat NONE fields).
    Fail-safe: any internal error leaves candidates untouched rather than breaking a scan.
    """
    if not signals:
        return 0
    try:
        if not config.enabled():
            for s in signals:
                if isinstance(s, dict):
                    _set_inactive(s)
                    _attach_tone(s, None)
            return 0
        payload = state_payload if state_payload is not None else get_active_macro_state(now=now)
        block_auto_exec = config.block_auto_execution()
    except Exception as exc:
        log.debug("[MACRO] guard unavailable; scan annotation skipped: %s", exc)
        return 0

    tone = _latest_tone()

    count = 0
    for s in signals:
        try:
            annotate_signal(s, payload, block_auto_exec=block_auto_exec, tone=tone)
            count += 1
        except Exception as exc:
            log.debug("[MACRO] annotate failed for one candidate: %s", exc)
    return count


def annotate_scan_output(scan_out: dict[str, Any], *, now: datetime | None = None) -> None:
    """Annotate signals + watchlist of a full scan payload (single guard call shared)."""
    if not isinstance(scan_out, dict):
        return
    try:
        payload = get_active_macro_state(now=now) if config.enabled() else {"macroRisk": "NONE"}
    except Exception as exc:
        log.debug("[MACRO] scan-output annotation skipped: %s", exc)
        return
    for key in ("signals", "tradeSignals", "watchlist"):
        annotate_signals(scan_out.get(key), now=now, state_payload=payload)
