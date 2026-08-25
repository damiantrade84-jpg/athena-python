"""OX Book API: explicit read-only scan, then explicit manual demo execution.

GET  /api/ox-book-status   metadata plus the latest cached scan (never scans)
POST /api/ox-book-scan     read-only scan of every certified member
POST /api/ox-book-execute  manually execute one action from the current scan
POST /api/ox-book-run      compatibility alias with the same scan-id requirement

There is deliberately no scheduler and no auto-execution path. Execution rechecks
the current closed D1 bar/action. Entries then use the existing demo/freshness/
guardian/risk/broker bridge; exits use the demo-gated owned-position close path.
Certified membership remains a hard precondition.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import logging
import math
import threading
from typing import Any
from uuid import uuid4

from flask import Flask, jsonify, request

from ox_book import settings as ox_settings

log = logging.getLogger("athena.api.ox_book")

# Static certification evidence from the 2026-08-25 deep-history run
# (38-market universe, EMA15/60 x 3ATR long-only, all gates applied).
CERTIFICATION = {
    "engine": "OX Book (evidence-certified TSMOM trend book, demo-only)",
    "certifiedOn": "2026-08-25",
    "universe": 38,
    "trialsLogged": 419,
    "members": {
        "gold": {
            "edgeQuality": 0.290,
            "sqn100Full": 2.39,
            "nTrades": 68,
            "plateauPassFrac": 0.78,
            "stressedSqn100": 2.32,
        },
        "nasdaq": {
            "edgeQuality": 0.363,
            "sqn100Full": 2.79,
            "nTrades": 59,
            "plateauPassFrac": 1.00,
            "stressedSqn100": 2.74,
        },
    },
    "bookPooled": {
        "sqn100Full": 3.16,
        "tStat": 3.56,
        "expR": 0.415,
        "sqn100Oos": 3.37,
        "maxDdR": 3.9,
    },
}

_latest_scan: dict[str, Any] | None = None
_scan_lock = threading.RLock()
_execution_lock = threading.Lock()


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(value) for value in obj]
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


def _base_payload() -> dict[str, Any]:
    from tsmom_live.signal import INSTRUMENTS

    members = _book_members()
    return {
        "success": True,
        "enabled": bool(ox_settings.enabled()),
        "deployment": "DEMO_ONLY",
        "executionMode": "MANUAL_DEMO_ONLY",
        "autoExecute": False,
        "schedulerEnabled": False,
        "certification": CERTIFICATION,
        "members": members,
        "excludedInstruments": sorted(set(INSTRUMENTS) - set(members)),
    }


def _manual_execution(snapshot: dict[str, Any]) -> dict[str, Any]:
    decision = snapshot.get("decision") if isinstance(snapshot.get("decision"), dict) else {}
    freshness = snapshot.get("freshness") if isinstance(snapshot.get("freshness"), dict) else {}
    action = str(decision.get("action") or "NONE").upper()

    if not snapshot.get("hasData"):
        reason = "data_unavailable"
    elif not freshness.get("ok"):
        reason = f"stale_data:{freshness.get('reason') or 'unknown'}"
    elif action not in {"OPEN_LONG", "CLOSE"}:
        reason = f"no_manual_action:{decision.get('reason') or action.lower()}"
    else:
        return {
            "eligible": True,
            "action": action,
            "reason": "manual_demo_action_available",
        }
    return {"eligible": False, "action": action, "reason": reason}


def api_ox_book_status():
    """Return metadata/latest scan only; polling this route performs no market read."""
    with _scan_lock:
        latest = copy.deepcopy(_latest_scan)
    return jsonify({
        **_base_payload(),
        "scanId": latest.get("scanId") if latest else None,
        "scannedAt": latest.get("scannedAt") if latest else None,
        "scannedCount": latest.get("scannedCount", 0) if latest else 0,
        "snapshots": latest.get("snapshots", {}) if latest else {},
        "lastExecution": latest.get("lastExecution") if latest else None,
    })


def api_ox_book_scan():
    """Scan all certified TSMOM members. This endpoint cannot execute."""
    global _latest_scan

    if not ox_settings.enabled():
        return jsonify({**_base_payload(), "success": False, "error": "ox_book_disabled"}), 503

    from tsmom_live.runtime import status as tsmom_status
    from tsmom_live.signal import INSTRUMENTS

    members = _book_members()
    snapshots: dict[str, Any] = {}
    for name in members:
        try:
            snapshot = _json_safe(tsmom_status(INSTRUMENTS[name]))
        except Exception as exc:
            log.exception("OX Book scan failed for %s", name)
            snapshot = {
                "instrument": name,
                "hasData": False,
                "status": "error",
                "error": str(exc),
                "dataSource": "mt5",
                "freshness": {"ok": False, "reason": "scan_error"},
            }
        snapshot["manualExecution"] = _manual_execution(snapshot)
        snapshots[name] = snapshot

    scan = {
        "scanId": uuid4().hex,
        "scannedAt": datetime.now(timezone.utc).isoformat(),
        "scannedCount": len(snapshots),
        "snapshots": snapshots,
        "lastExecution": None,
    }
    with _scan_lock:
        _latest_scan = scan
    return jsonify({**_base_payload(), **copy.deepcopy(scan)})


def _current_action(scan_id: str, key: str) -> tuple[dict[str, Any] | None, str | None]:
    with _scan_lock:
        current = copy.deepcopy(_latest_scan)
    if not current or not scan_id or scan_id != current.get("scanId"):
        return None, "current_scan_required"
    snapshot = (current.get("snapshots") or {}).get(key)
    if not isinstance(snapshot, dict):
        return None, f"instrument_not_in_scan:{key}"
    manual = snapshot.get("manualExecution")
    if not isinstance(manual, dict) or not manual.get("eligible"):
        reason = manual.get("reason") if isinstance(manual, dict) else "manual_action_unavailable"
        return None, str(reason)
    return snapshot, None


def api_ox_book_execute():
    """Execute only an eligible action from the current scan, on manual request."""
    global _latest_scan

    if not ox_settings.enabled():
        return jsonify({"success": False, "executed": False, "error": "ox_book_disabled"}), 503

    payload = request.get_json(force=True, silent=True) or {}
    cfg, key = _member_cfg(str(payload.get("instrument") or ""))
    if cfg is None:
        return jsonify({
            "success": False,
            "executed": False,
            "error": f"not_certified_member:{key}" if key else "missing_instrument",
            "certifiedMembers": _book_members(),
        }), 400

    scan_id = str(payload.get("scanId") or "").strip()
    snapshot, refusal = _current_action(scan_id, key)
    if refusal:
        return jsonify({"success": False, "executed": False, "error": refusal}), 409

    if not _execution_lock.acquire(blocking=False):
        return jsonify({
            "success": False,
            "executed": False,
            "error": "manual_execution_in_progress",
        }), 409
    try:
        # Recheck after obtaining the process-wide execution slot so a second click
        # cannot queue behind the first and reuse a consumed scan action.
        snapshot, refusal = _current_action(scan_id, key)
        if refusal:
            return jsonify({"success": False, "executed": False, "error": refusal}), 409

        manual = snapshot["manualExecution"]
        bar_time_ms = snapshot.get("lastBarTimeMs")
        if bar_time_ms is None:
            return jsonify({
                "success": False,
                "executed": False,
                "error": "scan_bar_missing",
            }), 409

        from tsmom_live.runtime import execute_manual

        result = execute_manual(
            cfg,
            expected_action=str(manual["action"]),
            expected_bar_time_ms=int(bar_time_ms),
        )
        result = _json_safe(result)
        if not result.get("executed"):
            reason = str(result.get("reason") or result.get("status") or "execution_rejected")
            return jsonify({
                "success": False,
                "executed": False,
                "error": reason,
                "result": result,
            }), 409

        with _scan_lock:
            if _latest_scan and _latest_scan.get("scanId") == scan_id:
                current_snapshot = (_latest_scan.get("snapshots") or {}).get(key)
                if isinstance(current_snapshot, dict):
                    current_snapshot["manualExecution"] = {
                        "eligible": False,
                        "action": str(manual["action"]),
                        "reason": "scan_action_consumed",
                    }
                _latest_scan["lastExecution"] = {
                    "instrument": key,
                    "scanId": scan_id,
                    "executedAt": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                }
        return jsonify({"success": True, "executed": True, "result": result})
    except Exception as exc:
        log.exception("OX Book manual execution failed for %s", key)
        return jsonify({
            "success": False,
            "executed": False,
            "error": f"manual_execution_error:{exc}",
        }), 500
    finally:
        _execution_lock.release()


def register_ox_book_routes(app: Flask) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    if "/api/ox-book-status" not in rules:
        app.add_url_rule(
            "/api/ox-book-status",
            "api_ox_book_status",
            api_ox_book_status,
            methods=["GET"],
        )
    if "/api/ox-book-scan" not in rules:
        app.add_url_rule(
            "/api/ox-book-scan",
            "api_ox_book_scan",
            api_ox_book_scan,
            methods=["POST"],
        )
    if "/api/ox-book-execute" not in rules:
        app.add_url_rule(
            "/api/ox-book-execute",
            "api_ox_book_execute",
            api_ox_book_execute,
            methods=["POST"],
        )
    if "/api/ox-book-run" not in rules:
        app.add_url_rule(
            "/api/ox-book-run",
            "api_ox_book_run_compat",
            api_ox_book_execute,
            methods=["POST"],
        )
