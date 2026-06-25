"""Macro (FOMC) risk API — read-only server-trusted macro state for UI / chart / AI.

`/api/macro/state`            GET  active macro-risk state (global)
`/api/macro/state?symbol=...` GET  symbol-specific macro risk (affected + block flags)
`/api/macro/events`           GET  stored upcoming/recent macro events

Read-only. Exposes the same macro state the scan overlay and AI review consume. It
never executes, mutates config, or alters any score.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from flask import jsonify, request

log = logging.getLogger("athena.api.macro")


def _json_safe(obj: Any) -> Any:
    import math

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def api_macro_state():
    symbol = (request.args.get("symbol") or "").strip()
    try:
        from macro import macro_guard as guard

        if symbol:
            state = guard.get_symbol_macro_risk(symbol)
        else:
            state = guard.macro_context_for_ui()
        return jsonify({"success": True, "state": _json_safe(state)})
    except Exception as exc:
        log.debug("[MACRO] state route failed: %s", exc)
        return jsonify({"success": False, "error": str(exc), "state": {"macroRisk": "NONE"}}), 200


def api_macro_events():
    try:
        from macro import config
        from macro.macro_guard import LOCKOUT_EVENT_TYPES
        from macro.store import default_store

        now = config.now_utc()
        lookback_days = int(request.args.get("lookback_days", "10") or 10)
        lookahead_days = int(request.args.get("lookahead_days", "120") or 120)
        store = default_store()
        events = store.list_events(
            start_iso=(now - timedelta(days=lookback_days)).isoformat(),
            end_iso=(now + timedelta(days=lookahead_days)).isoformat(),
            limit=int(request.args.get("limit", "200") or 200),
        )
        upcoming = [
            e for e in events
            if str(e.get("event_type")) in LOCKOUT_EVENT_TYPES
            and str(e.get("scheduled_time_utc") or "") >= now.isoformat()
        ]
        return jsonify(
            {
                "success": True,
                "count": len(events),
                "events": _json_safe(events),
                "nextEvent": _json_safe(upcoming[0]) if upcoming else None,
                "nowUtc": now.isoformat(),
            }
        )
    except Exception as exc:
        log.debug("[MACRO] events route failed: %s", exc)
        return jsonify({"success": False, "error": str(exc), "events": []}), 200


def _tone_response(rec: dict[str, Any]) -> dict[str, Any]:
    import json

    def _parse(value):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return []
        return value or []

    return {
        "eventId": rec.get("macro_event_id"),
        "toneLabel": rec.get("tone_label"),
        "toneScore": rec.get("tone_score"),
        "confidence": rec.get("confidence"),
        "currency": rec.get("currency"),
        "source": rec.get("source"),
        "title": rec.get("title"),
        "statementUrl": rec.get("statement_url"),
        "statementPublishedUtc": rec.get("statement_published_utc"),
        "previousStatementEventId": rec.get("previous_statement_event_id"),
        "targetRangeChangeBps": rec.get("target_range_change_bps"),
        "marketExpectationSurpriseBps": rec.get("market_expectation_surprise_bps"),
        "componentScores": {
            "statementLanguage": rec.get("statement_language_score"),
            "inflationLanguage": rec.get("inflation_language_score"),
            "laborMarket": rec.get("labor_market_score"),
            "growth": rec.get("growth_score"),
            "balanceSheet": rec.get("balance_sheet_score"),
            "forwardGuidance": rec.get("forward_guidance_score"),
            "rateDecision": rec.get("rate_decision_score"),
            "dotPlot": rec.get("dot_plot_score"),
            "pressConference": rec.get("press_conference_score"),
        },
        "evidence": _parse(rec.get("evidence_json")),
        "warnings": _parse(rec.get("warnings_json")),
        "executionUse": "CONTEXT_ONLY",
    }


def api_macro_fomc_tone():
    event_id = (request.args.get("event_id") or "").strip()
    try:
        from macro.tone_store import default_tone_store

        store = default_tone_store()
        rec = store.get_by_event(event_id) if event_id else store.latest()
        if not rec:
            return jsonify({"success": True, "tone": None})
        return jsonify({"success": True, "tone": _json_safe(_tone_response(rec))})
    except Exception as exc:
        log.debug("[MACRO] fomc-tone route failed: %s", exc)
        return jsonify({"success": False, "error": str(exc), "tone": None}), 200


def register_macro_routes(app) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    if "/api/macro/state" not in rules:
        app.add_url_rule("/api/macro/state", "api_macro_state", api_macro_state, methods=["GET"])
    if "/api/macro/events" not in rules:
        app.add_url_rule("/api/macro/events", "api_macro_events", api_macro_events, methods=["GET"])
    if "/api/macro/fomc-tone" not in rules:
        app.add_url_rule(
            "/api/macro/fomc-tone", "api_macro_fomc_tone", api_macro_fomc_tone, methods=["GET"]
        )
