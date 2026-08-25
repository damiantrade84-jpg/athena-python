"""OX Book routes — evidence-certified membership over the TSMOM live bridge.

GET  /api/ox-book-status  read-only: certified members + live per-member snapshot
POST /api/ox-book-run     manual daily cycle for ONE certified member (gated demo)

OX Book owns membership only (which markets passed the evidence gates). Every
execution gate lives in tsmom_live.bridge (demo gate -> freshness -> guardian ->
risk_check -> broker); this route never takes or relaxes an execution decision.
A member that is not in OX_BOOK.LIVE_MEMBERS is refused before the runtime is
touched — certification is a precondition, not a display detail.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from flask import Flask, jsonify, request

from ox_book import settings as ox_settings

log = logging.getLogger("athena.api.ox_book")

# Static certification evidence from the 2026-08-25 deep-history run
# (38-market universe, EMA15/60 x 3ATR long-only, all gates applied).
CERTIFICATION = {
    "engine": "OX Book (evidence-certified daily trend book, demo-only)",
    "certifiedOn": "2026-08-25",
    "universe": 38,
    "trialsLogged": 419,
    "members": {
        "gold": {"edgeQuality": 0.290, "sqn100Full": 2.39, "nTrades": 68,
                 "plateauPassFrac": 0.78, "stressedSqn100": 2.32},
        "nasdaq": {"edgeQuality": 0.363, "sqn100Full": 2.79, "nTrades": 59,
                   "plateauPassFrac": 1.00, "stressedSqn100": 2.74},
    },
    "bookPooled": {"sqn100Full": 3.16, "tStat": 3.56, "expR": 0.415,
                   "sqn100Oos": 3.37, "maxDdR": 3.9},
}


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _book_members() -> list[str]:
    from tsmom_live.signal import INSTRUMENTS

    authorized = set(ox_settings.live_members())
    return [name for name in INSTRUMENTS if name in authorized]


def _member_cfg(instrument: str):
    from tsmom_live.signal import INSTRUMENTS

    key = str(instrument or "").strip().lower()
    cfg = INSTRUMENTS.get(key)
    if cfg is None or key not in set(ox_settings.live_members()):
        return None, key
    return cfg, key


def api_ox_book_status():
    from tsmom_live.runtime import status as tsmom_status
    from tsmom_live.signal import INSTRUMENTS

    members = _book_members()
    excluded = sorted(set(INSTRUMENTS) - set(members))
    snapshots = {}
    for name in members:
        try:
            snapshots[name] = _json_safe(tsmom_status(INSTRUMENTS[name]))
        except Exception as exc:
            log.exception("OX Book status failed for %s", name)
            snapshots[name] = {"instrument": name, "hasData": False,
                               "status": "error", "error": str(exc)}
    return jsonify({
        "success": True,
        "deployment": "DEMO_ONLY",
        "certification": CERTIFICATION,
        "members": members,
        "excludedInstruments": excluded,
        "snapshots": snapshots,
    })


def api_ox_book_run():
    from tsmom_live.runtime import run_once

    d = request.get_json(force=True, silent=True) or {}
    cfg, key = _member_cfg(str(d.get("instrument") or ""))
    if cfg is None:
        return jsonify({
            "success": False,
            "error": f"not_certified_member:{key}" if key else "missing_instrument",
            "certifiedMembers": _book_members(),
        }), 400
    try:
        result = run_once(cfg)
        st = str(result.get("status") or "")
        executed = st in {"opened", "closed", "hold"}
        return jsonify({"success": True, "executed": executed,
                        "result": _json_safe(result)})
    except Exception as exc:
        log.exception("OX Book run failed for %s", key)
        return jsonify({"success": False, "error": str(exc)}), 500


def register_ox_book_routes(app: Flask) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    if "/api/ox-book-status" not in rules:
        app.add_url_rule(
            "/api/ox-book-status", "api_ox_book_status", api_ox_book_status, methods=["GET"]
        )
    if "/api/ox-book-run" not in rules:
        app.add_url_rule(
            "/api/ox-book-run", "api_ox_book_run", api_ox_book_run, methods=["POST"]
        )
