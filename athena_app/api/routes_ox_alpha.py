"""OX Alpha routes — server-authoritative scan + gated demo execution.

GET  /api/ox-alpha-status   read-only universe snapshot (filters optional)
POST /api/ox-alpha-scan     explicit fresh scan of the universe or a subset
POST /api/ox-alpha-execute  fresh re-scan + match + demo-gated execution

The route never takes an execution decision itself; ox_alpha.runtime and
ox_alpha.bridge own every gate.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from flask import jsonify, request

from ox_alpha import runtime

log = logging.getLogger("athena.api.ox_alpha")


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _filter_params() -> dict[str, Any]:
    d = request.args if request.method == "GET" else (request.get_json(silent=True) or {})
    out: dict[str, Any] = {}
    try:
        out["min_score"] = float(d.get("minScore"))
    except (TypeError, ValueError):
        out["min_score"] = None
    decision = str(d.get("decision") or "").strip().upper()
    out["decision"] = decision if decision in {"TRADE", "WATCH", "NO_SIGNAL"} else None
    symbols = d.get("symbols")
    out["symbols"] = (
        [str(s).strip().upper() for s in symbols if str(s).strip()]
        if isinstance(symbols, (list, tuple))
        else None
    )
    try:
        out["limit"] = max(0, int(d.get("limit")))
    except (TypeError, ValueError):
        out["limit"] = 0
    return out


def _apply_filters(envelope: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    signals = envelope.get("signals") or []
    if params.get("decision"):
        signals = [s for s in signals if s.get("decision") == params["decision"]]
    if params.get("min_score") is not None:
        floor = params["min_score"]
        signals = [s for s in signals if float(s.get("score") or 0) >= floor]
    if params.get("symbols"):
        want = set(params["symbols"])
        signals = [
            s for s in signals
            if str(s.get("display") or "").upper() in want
            or str(s.get("symbol") or "").upper() in want
        ]
    total = len(signals)
    if params.get("limit"):
        signals = signals[: params["limit"]]
    out = dict(envelope)
    out["signals"] = signals
    out["totalSignalsBeforeFilters"] = len(envelope.get("signals") or [])
    out["signalsFiltered"] = total != len(envelope.get("signals") or [])
    return out


def api_ox_alpha_status():
    try:
        envelope = runtime.status()
        return jsonify({"success": True, **_json_safe(_apply_filters(envelope, _filter_params()))})
    except Exception as exc:
        log.exception("OX Alpha status failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def api_ox_alpha_scan():
    d = request.get_json(silent=True) or {}
    pairs = None
    symbols = d.get("symbols")
    if isinstance(symbols, (list, tuple)) and symbols:
        found = [runtime.find_pair(str(s)) for s in symbols]
        missing = [str(s) for s, p in zip(symbols, found) if p is None]
        pairs = [p for p in found if p is not None]
        if missing:
            return jsonify({"success": False, "error": f"unknown_symbols:{','.join(missing)}"}), 400
    try:
        envelope = runtime.scan_universe(pairs)
        return jsonify({"success": True, **_json_safe(envelope)})
    except Exception as exc:
        log.exception("OX Alpha scan failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def api_ox_alpha_execute():
    d = request.get_json(force=True, silent=True) or {}
    symbol = str(d.get("symbol") or "").strip()
    direction = str(d.get("direction") or "").strip().upper()
    if not symbol:
        return jsonify({"success": False, "error": "missing_symbol"}), 400
    if direction not in {"LONG", "SHORT"}:
        return jsonify({"success": False, "error": "direction_must_be_LONG_or_SHORT"}), 400
    try:
        result = runtime.execute_signal(symbol, direction)
        return jsonify({"success": True, **_json_safe(result)})
    except Exception as exc:
        log.exception("OX Alpha execute failed for %s", symbol)
        return jsonify({"success": False, "error": str(exc)}), 500


def register_ox_alpha_routes(app) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    if "/api/ox-alpha-status" not in rules:
        app.add_url_rule(
            "/api/ox-alpha-status", "api_ox_alpha_status", api_ox_alpha_status, methods=["GET"]
        )
    if "/api/ox-alpha-scan" not in rules:
        app.add_url_rule(
            "/api/ox-alpha-scan", "api_ox_alpha_scan", api_ox_alpha_scan, methods=["POST"]
        )
    if "/api/ox-alpha-execute" not in rules:
        app.add_url_rule(
            "/api/ox-alpha-execute", "api_ox_alpha_execute", api_ox_alpha_execute, methods=["POST"]
        )
