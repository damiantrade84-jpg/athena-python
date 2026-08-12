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
    clear_terminal_watches,
    count_watches_by_status,
    evaluate_all_watches_once,
    get_runner_status,
    load_active_watches,
    monitor_config,
    start_suggested_trade_runner_once,
    validate_flag_payload,
)

log = logging.getLogger("sentinel.suggested_trades")

_runtime: SimpleNamespace | None = None


def start_suggested_trade_monitor(runtime: SimpleNamespace) -> bool:
    """Start the alert-only monitor during explicit application startup."""
    cfg = getattr(runtime, "CONFIG", {}) or {}
    return start_suggested_trade_runner_once(
        cfg=cfg,
        fetch_candles_fn=_runtime_fetch_candles(runtime),
        live_prices=getattr(runtime, "live_prices", None),
        live_prices_lock=getattr(runtime, "live_prices_lock", None),
        active_path=DEFAULT_ACTIVE_PATH,
        events_path=DEFAULT_EVENTS_PATH,
    )


def _runtime_cfg() -> dict:
    rt = _runtime
    return getattr(rt, "CONFIG", {}) or {}


def _runtime_fetch_candles(runtime: SimpleNamespace | None):
    """Adapt the monitor's fetch_candles(symbol, tf, limit) calls to the runtime.

    The monitor passes a symbol string; production fetch_candles expects a pair
    dict. Resolve via the injected resolve_pair callable and fail soft to a
    minimal {"symbol": ...} dict instead of crashing every evaluation tick.
    """
    fetch = getattr(runtime, "fetch_candles", None)
    if fetch is None:
        return None
    resolve = getattr(runtime, "resolve_pair", None)

    def _fetch(symbol: str, tf: str, limit: int = 5):
        pair = None
        if callable(resolve):
            try:
                pair = resolve(symbol)
            except Exception:
                pair = None
        if not isinstance(pair, dict):
            pair = {"symbol": symbol}
        return fetch(pair, tf, limit)

    return _fetch


def _filter_watches(watches: list[dict], *, status_filter: str | None, symbol_filter: str) -> list[dict]:
    out = watches
    if status_filter:
        out = [w for w in out if str(w.get("status")) == status_filter.upper()]
    if symbol_filter:
        out = [w for w in out if str(w.get("symbol", "")).upper() == symbol_filter]
    return out


def _build_list_payload(*, watches: list[dict] | None = None) -> dict:
    rt = _runtime
    cfg = _runtime_cfg()
    all_watches = watches if watches is not None else load_active_watches(DEFAULT_ACTIVE_PATH)
    return {
        "ok": True,
        "watches": all_watches,
        "count": len(all_watches),
        "counts": count_watches_by_status(all_watches),
        "runner": get_runner_status(cfg),
        "alert_only": True,
    }


def register_suggested_trade_routes(app, runtime: SimpleNamespace) -> None:
    global _runtime
    _runtime = runtime

    @app.route("/api/suggested-trades/flag", methods=["POST"])
    def api_suggested_trades_flag():
        rt = _runtime
        cfg = _runtime_cfg()
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
            "watch_id": watch.watch_id if watch else None,
            "alert_only": True,
        }))

    @app.route("/api/suggested-trades", methods=["GET"])
    def api_suggested_trades_list():
        rt = _runtime
        watches = load_active_watches(DEFAULT_ACTIVE_PATH)
        status_filter = request.args.get("status")
        symbol_filter = (request.args.get("symbol") or "").upper().strip()
        filtered = _filter_watches(watches, status_filter=status_filter, symbol_filter=symbol_filter)
        payload = _build_list_payload(watches=filtered)
        return jsonify(rt.json_safe(payload))

    @app.route("/api/suggested-trades/status", methods=["GET"])
    def api_suggested_trades_status():
        rt = _runtime
        cfg = _runtime_cfg()
        watches = load_active_watches(DEFAULT_ACTIVE_PATH)
        return jsonify(rt.json_safe({
            "ok": True,
            "counts": count_watches_by_status(watches),
            "runner": get_runner_status(cfg),
            "alert_only": True,
        }))

    @app.route("/api/suggested-trades/<watch_id>/cancel", methods=["POST"])
    def api_suggested_trades_cancel(watch_id: str):
        rt = _runtime
        watch, err = cancel_watch(watch_id)
        if err:
            return jsonify({"success": False, "error": err}), 404
        return jsonify(rt.json_safe({"success": True, "watch": watch, "alert_only": True}))

    @app.route("/api/suggested-trades/clear-terminal", methods=["POST"])
    def api_suggested_trades_clear_terminal():
        rt = _runtime
        result = clear_terminal_watches(
            active_path=DEFAULT_ACTIVE_PATH,
            events_path=DEFAULT_EVENTS_PATH,
        )
        return jsonify(rt.json_safe({"success": True, **result, "alert_only": True}))

    @app.route("/api/suggested-trades/evaluate-now", methods=["POST"])
    def api_suggested_trades_evaluate_now():
        rt = _runtime
        cfg = _runtime_cfg()
        try:
            result = evaluate_all_watches_once(
                cfg=cfg,
                active_path=DEFAULT_ACTIVE_PATH,
                events_path=DEFAULT_EVENTS_PATH,
                fetch_candles_fn=_runtime_fetch_candles(rt),
                live_prices=getattr(rt, "live_prices", None),
                live_prices_lock=getattr(rt, "live_prices_lock", None),
            )
        except Exception as exc:
            log.exception("suggested trade evaluate-now failed")
            return jsonify({
                "success": False,
                "error": str(exc),
                "alert_only": True,
            }), 500

        watches = load_active_watches(DEFAULT_ACTIVE_PATH)
        payload = _build_list_payload(watches=watches)
        payload["success"] = True
        payload["evaluation"] = result
        return jsonify(rt.json_safe(payload))
