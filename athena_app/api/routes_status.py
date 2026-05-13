"""Status and support API route handlers and registration.

Behavior-neutral extraction from athena.py for read-only operator/status
surfaces. This module does not own scoring, risk, freshness, or execution logic.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import jsonify, request, send_from_directory

from config import ai_key_configured

CONFIG: dict = {}
_AUDIT_DB = ""
_app = None
_all_pairs_getter = lambda: []
_active_pairs_getter = lambda: []
_kill_switch_getter = lambda: False
_last_scan_results_getter = lambda: {}
_mt5_connection_health_getter = lambda: {}
_micro_cache_getter = lambda: {}
_json_safe = lambda value: value
_signal_stability_index = None
log = logging.getLogger(__name__)


def index():
    resp = send_from_directory("static", "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def sentinel_prototype():
    resp = send_from_directory("static/sentinel-prototype", "Sentinel.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def sentinel_prototype_asset(filename):
    return send_from_directory("static/sentinel-prototype", filename)


def api_last_scan():
    """Latest full-universe scan kept in memory - survives dashboard tab refresh.

    Use this instead of relying only on ``localStorage`` (large payloads can exceed
    quota and fail to persist, leaving an older snapshot).
    """
    r = _last_scan_results_getter()
    if not isinstance(r, dict):
        return jsonify({"available": False, "reason": "invalid"}), 200
    if not r.get("success") or not r.get("scannedAt"):
        return jsonify({"available": False, "reason": "no_scan"}), 200
    out = dict(r)
    # UI dashboards often need only a preview. Keep the default full payload for
    # existing callers, but allow read-only panels to avoid repeatedly shipping
    # multi-MB scan snapshots while the app is idle.
    try:
        limit = int(request.args.get("limit", "0") or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit > 0 and isinstance(out.get("signals"), list):
        total_signals = len(out["signals"])
        out["signals"] = out["signals"][:limit]
        out["signalsTruncated"] = total_signals > limit
        out["totalSignals"] = total_signals
    out["available"] = True
    return jsonify(_json_safe(out))


def api_conductor_last():
    """Return conductor routing for a specific pair (?pair=) or the top scan signal."""
    try:
        import conductor as _cmod
    except Exception as _imp_err:
        log.debug(f"[CONDUCTOR] import failed: {_imp_err}")
        return jsonify({"conductor": None, "message": "Conductor module unavailable"}), 200

    _lk = getattr(_cmod, "_conductor_state_lock", None)
    pair_arg = request.args.get("pair", "").strip()
    if pair_arg:
        if _lk:
            with _lk:
                _all_snap = dict(_cmod._ALL_CONDUCTOR_RESULTS)
        else:
            _all_snap = dict(_cmod._ALL_CONDUCTOR_RESULTS)
        if _all_snap:
            _res = _all_snap.get(pair_arg)
            if _res is None:
                pair_norm = pair_arg.upper().replace("/", "")
                for k, v in _all_snap.items():
                    if k.upper() == pair_arg.upper() or k.upper().replace("/", "") == pair_norm:
                        _res = v
                        break
            if _res:
                return jsonify(_json_safe({"conductor": _res.get("routing", {}), "timestamp": datetime.now(timezone.utc).isoformat()}))
            return jsonify({"conductor": None, "message": f"{pair_arg} not in last scan"}), 200

    if _lk:
        with _lk:
            _last_only = _cmod._LAST_CONDUCTOR_RESULT
    else:
        _last_only = _cmod._LAST_CONDUCTOR_RESULT

    if _last_only is None:
        return jsonify({"conductor": None, "message": "No conductor data yet. Run a scan."}), 200

    _out = {
        "conductor": _last_only.get("routing", {}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return jsonify(_json_safe(_out))


def api_conductor_pairs():
    """Return the list of pairs that have conductor results from the last scan."""
    try:
        import conductor as _cmod
    except Exception:
        return jsonify({"pairs": []}), 200
    _lk = getattr(_cmod, "_conductor_state_lock", None)
    if _lk:
        with _lk:
            _snap = dict(_cmod._ALL_CONDUCTOR_RESULTS)
            _stype = getattr(_cmod, "_LAST_SCAN_TYPE", "")
    else:
        _snap = dict(_cmod._ALL_CONDUCTOR_RESULTS)
        _stype = getattr(_cmod, "_LAST_SCAN_TYPE", "")
    pairs = []
    for pair, res in _snap.items():
        r = res.get("routing", {})
        pairs.append({
            "pair": pair,
            "direction": r.get("direction", "?"),
            "score_pct": r.get("score_pct", 0),
            "skip_signal": r.get("skip_signal", False),
        })
    pairs.sort(key=lambda x: x["score_pct"], reverse=True)
    return jsonify({"pairs": pairs, "scan_type": _stype})


def health():
    all_pairs = _all_pairs_getter()
    active_pairs = _active_pairs_getter()
    kill_switch = bool(_kill_switch_getter())
    data_sources = _configured_data_sources(all_pairs)
    mt5_status = _mt5_connection_health_getter()

    return jsonify(
        {
            "status": "paused" if kill_switch else "ok",
            "killSwitch": kill_switch,
            "pairs": len(all_pairs),
            "activePairs": len(active_pairs),
            "dataSource": "+".join(data_sources),
            "dataSources": data_sources,
            "mt5": mt5_status,
            "microstructureFeedsEnabled": bool(
                CONFIG.get("MICROSTRUCTURE_FEEDS_ENABLED")
            ),
            "aiKey": ai_key_configured(CONFIG),
            "xaiKey": ai_key_configured(CONFIG),
        }
    )


def _configured_data_sources(all_pairs: list[dict]) -> list[str]:
    sources = set()
    for pair in all_pairs:
        source = str(pair.get("source") or "").strip().lower()
        if source:
            sources.add(source)
    return sorted(sources)


def api_signal_stability():
    engine = request.args.get("engine")
    return jsonify(_signal_stability_index(engine=engine, db_path=_AUDIT_DB))


def api_debug_routes():
    """Debug endpoint to list all registered API routes. Read-only."""
    routes = []
    for rule in _app.url_map.iter_rules():
        if rule.rule.startswith("/api/"):
            routes.append({
                "path": rule.rule,
                "methods": sorted(list(rule.methods - {"HEAD", "OPTIONS"})),
                "endpoint": rule.endpoint,
            })
    return jsonify({"routes": sorted(routes, key=lambda x: x["path"])})


def api_microstructure_health():
    """Feed freshness for crypto microstructure WS (operational dashboard)."""
    now = time.time()
    enabled = bool(CONFIG.get("MICROSTRUCTURE_FEEDS_ENABLED"))
    rows = []
    for sym, data in _micro_cache_getter().items():
        if not isinstance(data, dict):
            continue
        ts = data.get("_updated_ts")
        age = round(now - ts, 1) if ts is not None else None
        rows.append(
            {
                "symbol": sym,
                "age_sec": age,
                "stale": age is None or age > 45.0,
                "order_book_imbalance": data.get("order_book_imbalance"),
                "liquidity_pressure": data.get("liquidity_pressure"),
            }
        )
    rows.sort(key=lambda r: r["symbol"])
    return jsonify(
        _json_safe({"feeds_enabled": enabled, "symbol_count": len(rows), "symbols": rows})
    )


def register_status_routes(app, runtime: SimpleNamespace) -> None:
    """Register status/support routes using runtime state supplied by athena.py."""
    global CONFIG, _AUDIT_DB, _app, log, _json_safe, _signal_stability_index
    global _all_pairs_getter, _active_pairs_getter, _kill_switch_getter
    global _last_scan_results_getter, _mt5_connection_health_getter, _micro_cache_getter

    CONFIG = runtime.CONFIG
    _AUDIT_DB = runtime.AUDIT_DB
    _app = app
    _all_pairs_getter = runtime.all_pairs
    _active_pairs_getter = runtime.active_pairs
    _kill_switch_getter = runtime.kill_switch
    _last_scan_results_getter = runtime.last_scan_results
    _mt5_connection_health_getter = runtime.mt5_connection_health
    _micro_cache_getter = runtime.micro_cache
    _json_safe = runtime.json_safe
    _signal_stability_index = runtime.signal_stability_index
    log = runtime.log

    app.add_url_rule("/", "index", index)
    app.add_url_rule("/sentinel-prototype/", "sentinel_prototype", sentinel_prototype)
    app.add_url_rule(
        "/sentinel-prototype/<path:filename>",
        "sentinel_prototype_asset",
        sentinel_prototype_asset,
    )
    app.add_url_rule("/api/last-scan", "api_last_scan", api_last_scan, methods=["GET"])
    app.add_url_rule(
        "/api/conductor/last",
        "api_conductor_last",
        api_conductor_last,
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/kimi/conductor/last",
        "api_conductor_last",
        api_conductor_last,
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/conductor/pairs",
        "api_conductor_pairs",
        api_conductor_pairs,
        methods=["GET"],
    )
    app.add_url_rule("/api/health", "health", health)
    app.add_url_rule("/api/signal-stability", "api_signal_stability", api_signal_stability)
    app.add_url_rule("/api/debug/routes", "api_debug_routes", api_debug_routes)
    app.add_url_rule(
        "/api/microstructure-health",
        "api_microstructure_health",
        api_microstructure_health,
    )
