"""TSMOM live demo bridge — server-authoritative routes.

`/api/tsmom-run`   POST  manual daily cycle (gated demo execution; fails closed).
`/api/tsmom-status` GET  read-only snapshot for the panel (computes signal, executes nothing).

The run route routes through the standalone gated bridge (demo gate -> guardian ->
risk_check -> broker); it can only place demo orders and only when a fresh confirmed
D1 bar + MT5 demo are present. No execution decision is taken in the route itself."""
from __future__ import annotations

import logging
import math
from typing import Any

from flask import jsonify, request

from tsmom_live.runtime import run_once, status
from tsmom_live.signal import INSTRUMENTS

log = logging.getLogger("athena.api.tsmom")

# Static validated evidence shown on the panel (deep 25yr futures study).
SCORECARD = {
    "engine": "TSMOM (standalone, demo-only)",
    "validated": "gold long-only, EMA 15/60 x 3ATR Chandelier trail",
    "goldAlone": {"sqn100Full": 2.39, "sqn100Oos": 2.60},
    "book": {"members": ["gold", "brent", "nasdaq"], "sqn100Full": 3.14, "sqn100Oos": 3.47},
    "note": "Demo deployment is gold-only (XAU/USD); brent/nasdaq pending broker-symbol verification.",
}


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _resolve(instrument: str | None):
    key = str(instrument or "gold").strip().lower()
    return INSTRUMENTS.get(key), key


def api_tsmom_status():
    d = request.args
    cfg, key = _resolve(d.get("instrument"))
    if cfg is None:
        return jsonify({"success": False, "error": f"unknown_instrument:{key}",
                        "available": list(INSTRUMENTS)}), 400
    try:
        snap = status(cfg)
        return jsonify({"success": True, "deployment": "DEMO_ONLY",
                        "scorecard": SCORECARD, "state": _json_safe(snap)})
    except Exception as exc:
        log.exception("TSMOM status failed for %s", key)
        return jsonify({"success": False, "error": str(exc)}), 500


def api_tsmom_run():
    d = request.get_json(force=True, silent=True) or {}
    cfg, key = _resolve(d.get("instrument"))
    if cfg is None:
        return jsonify({"success": False, "error": f"unknown_instrument:{key}",
                        "available": list(INSTRUMENTS)}), 400
    try:
        result = run_once(cfg)
        st = str(result.get("status") or "")
        executed = st in {"opened", "closed", "hold"}
        return jsonify({"success": True, "executed": executed, "result": _json_safe(result)})
    except Exception as exc:
        log.exception("TSMOM run failed for %s", key)
        return jsonify({"success": False, "error": str(exc)}), 500


def register_tsmom_routes(app) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    if "/api/tsmom-status" not in rules:
        app.add_url_rule("/api/tsmom-status", "api_tsmom_status", api_tsmom_status, methods=["GET"])
    if "/api/tsmom-run" not in rules:
        app.add_url_rule("/api/tsmom-run", "api_tsmom_run", api_tsmom_run, methods=["POST"])
