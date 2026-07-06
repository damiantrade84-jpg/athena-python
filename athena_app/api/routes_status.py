"""Status and support API route handlers and registration.

Behavior-neutral extraction from athena.py for read-only operator/status
surfaces. This module does not own scoring, risk, freshness, or execution logic.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import jsonify, request, send_from_directory

from athena_app.services.paper_mode import get_paper_mode
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
    asset_class_filter = str(request.args.get("asset_class") or "").strip().lower()
    if asset_class_filter and isinstance(out.get("signals"), list):
        stored_ac = str(out.get("assetClass") or out.get("asset_class") or "").strip().lower()
        if stored_ac and stored_ac != asset_class_filter:
            out["available"] = False
            out["reason"] = "asset_class_mismatch"
            out["requestedAssetClass"] = asset_class_filter
            out["storedAssetClass"] = stored_ac
            return jsonify(_json_safe(out)), 200
        if not stored_ac:
            filtered = [
                s
                for s in out["signals"]
                if isinstance(s, dict)
                and str(s.get("type") or "").strip().lower() == asset_class_filter
            ]
            out["signals"] = filtered
            if isinstance(out.get("watchlist"), list):
                out["watchlist"] = [
                    s
                    for s in out["watchlist"]
                    if isinstance(s, dict)
                    and str(s.get("type") or "").strip().lower() == asset_class_filter
                ]
            out["assetClassFiltered"] = asset_class_filter
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


def _paper_mode_snapshot() -> tuple[bool, bool]:
    """Return (paper_mode, real_orders_allowed) from the runtime enforcer or CONFIG."""
    enforcer = get_paper_mode()
    if enforcer is not None:
        return enforcer.is_paper_mode(), enforcer.allow_real_orders()
    paper_soak = CONFIG.get("PAPER_SOAK") or {}
    paper_mode = bool(paper_soak.get("ENABLED", False))
    if paper_mode:
        return True, False
    real_orders_allowed = paper_soak.get("REAL_ORDERS_ALLOWED", True)
    return False, bool(real_orders_allowed if real_orders_allowed is not None else True)


def _scalp_execute_requires_ai_review() -> bool:
    ai_cfg = CONFIG.get("AI_SCALP_CHART_REVIEW") or {}
    if not isinstance(ai_cfg, dict):
        return True
    return bool(ai_cfg.get("EXECUTE_REQUIRES_AI_REVIEW", True))


def _scalp_demo_broker_requested() -> bool:
    try:
        if str(CONFIG.get("EXECUTOR_MODE") or "").strip().lower() == "demo":
            return True
    except Exception:
        pass
    if os.environ.get("BYBIT_DEMO", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return "demo" in os.environ.get("MT5_SERVER", "").lower()


def _scalp_execution_mode(paper_mode: bool, real_orders_allowed: bool) -> str:
    if paper_mode:
        return "paper"
    if _scalp_demo_broker_requested():
        return "demo"
    if not real_orders_allowed:
        return "live_disabled"
    return "live"


def health():
    all_pairs = _all_pairs_getter()
    active_pairs = _active_pairs_getter()
    kill_switch = bool(_kill_switch_getter())
    data_sources = _configured_data_sources(all_pairs)
    mt5_status = _mt5_connection_health_getter()
    paper_mode, real_orders_allowed = _paper_mode_snapshot()

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
            "paper_mode": paper_mode,
            "real_orders_allowed": real_orders_allowed,
            "scalp_execute_requires_ai_review": _scalp_execute_requires_ai_review(),
            "scalp_execution_mode": _scalp_execution_mode(paper_mode, real_orders_allowed),
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
        "api_kimi_conductor_last",
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
