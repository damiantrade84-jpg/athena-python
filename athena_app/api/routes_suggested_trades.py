"""API routes for alert-only suggested trade watches."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from flask import jsonify, request

from suggested_trade_monitor import (
    DEFAULT_ACTIVE_PATH,
    DEFAULT_EVENTS_PATH,
    add_watch,
    cancel_watch,
    evaluate_watches,
    load_active_watches,
    monitor_config,
    validate_flag_payload,
)

log = logging.getLogger("sentinel.suggested_trades")

_runtime: SimpleNamespace | None = None


def register_suggested_trade_routes(app, runtime: SimpleNamespace) -> None:
    global _runtime
    _runtime = runtime

    @app.route("/api/suggested-trades/flag", methods=["POST"])
    def api_suggested_trades_flag():
        rt = _runtime
        cfg = getattr(rt, "CONFIG", {}) or {}
        mcfg = monitor_config(cfg)
        if not mcfg.get("ENABLED", True):
            return jsonify({"success": False, "error": "monitor disabled"}), 503

        data = request.get_json(silent=True) or {}
        validated, err = validate_flag_payload(data, cfg)
        if err:
            return jsonify({"success": False, "error": err}), 400

        watch, add_err = add_watch(validated, cfg=cfg)
        if add_err:
            return jsonify({"success": False, "error": add_err}), 400

        return jsonify(rt.json_safe({
            "success": True,
            "watch": watch.to_dict() if watch else None,
            "alert_only": True,
        }))

    @app.route("/api/suggested-trades", methods=["GET"])
    def api_suggested_trades_list():
        rt = _runtime
        watches = load_active_watches(DEFAULT_ACTIVE_PATH)
        status_filter = request.args.get("status")
        symbol_filter = (request.args.get("symbol") or "").upper().strip()
        out = watches
        if status_filter:
            out = [w for w in out if str(w.get("status")) == status_filter.upper()]
        if symbol_filter:
            out = [w for w in out if str(w.get("symbol", "")).upper() == symbol_filter]
        return jsonify(rt.json_safe({
            "watches": out,
            "count": len(out),
            "alert_only": True,
        }))

    @app.route("/api/suggested-trades/<watch_id>/cancel", methods=["POST"])
    def api_suggested_trades_cancel(watch_id: str):
        rt = _runtime
        watch, err = cancel_watch(watch_id)
        if err:
            return jsonify({"success": False, "error": err}), 404
        return jsonify(rt.json_safe({"success": True, "watch": watch, "alert_only": True}))

    @app.route("/api/suggested-trades/evaluate-now", methods=["POST"])
    def api_suggested_trades_evaluate_now():
        rt = _runtime
        cfg = getattr(rt, "CONFIG", {}) or {}
        result = evaluate_watches(
            cfg=cfg,
            active_path=DEFAULT_ACTIVE_PATH,
            events_path=DEFAULT_EVENTS_PATH,
            fetch_candles_fn=getattr(rt, "fetch_candles", None),
            live_prices=getattr(rt, "live_prices", None),
            live_prices_lock=getattr(rt, "live_prices_lock", None),
        )
        watches = load_active_watches(DEFAULT_ACTIVE_PATH)
        return jsonify(rt.json_safe({
            "success": True,
            "evaluation": result,
            "watches": watches,
            "alert_only": True,
        }))
