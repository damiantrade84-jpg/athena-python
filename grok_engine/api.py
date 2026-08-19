"""Flask routes for the GROK dashboard and execution workflow."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request

from .execution import GrokExecutionError
from .service import ScanAlreadyRunning, GrokService


def _error(code: str, status: int, *, detail: str | None = None):
    payload: dict[str, Any] = {"success": False, "error": code}
    if detail:
        payload["detail"] = detail
    return jsonify(payload), status


def _csv_set(value: str | None) -> set[str] | None:
    if not value:
        return None
    rows = {part.strip().lower() for part in value.split(",") if part.strip()}
    return rows or None


def register_grok_routes(app, runtime) -> GrokService:
    service: GrokService = runtime.service
    existing = {rule.rule for rule in app.url_map.iter_rules()}

    def add(rule, endpoint, view, methods=("GET",)):
        if rule not in existing:
            app.add_url_rule(rule, endpoint, view, methods=list(methods))
            existing.add(rule)

    def health():
        return jsonify(service.health())

    def config():
        return jsonify(service.config_dict())

    def accounts():
        return jsonify(service.accounts())

    def scan():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return _error("json_object_required", 400)
        asset_types_raw = payload.get("assetTypes")
        symbols_raw = payload.get("symbols")
        if asset_types_raw is not None and not isinstance(asset_types_raw, list):
            return _error("assetTypes_must_be_array", 400)
        if symbols_raw is not None and not isinstance(symbols_raw, list):
            return _error("symbols_must_be_array", 400)
        asset_types = {str(value).strip().lower() for value in asset_types_raw or [] if str(value).strip()}
        symbols = {str(value).strip() for value in symbols_raw or [] if str(value).strip()}
        try:
            state = service.start_scan(asset_types=asset_types or None, symbols=symbols or None)
            return jsonify({"success": True, **state}), 202
        except ScanAlreadyRunning:
            return _error("scan_already_running", 409)
        except PermissionError as exc:
            return _error(str(exc), 403)
        except ValueError as exc:
            return _error(str(exc), 400)
        except Exception as exc:
            return _error("scan_start_failed", 503, detail=str(exc))

    def current_scan():
        state = service.current_scan()
        return jsonify({"success": True, **state}) if state else _error("scan_not_found", 404)

    def signals():
        decisions = _csv_set(request.args.get("decisions"))
        if decisions:
            decisions = {value.upper() for value in decisions}
            if not decisions.issubset({"READY", "WATCH", "BLOCKED"}):
                return _error("invalid_decision_filter", 400)
        asset_types = _csv_set(request.args.get("asset_types"))
        try:
            limit = int(request.args.get("limit", 250))
        except (TypeError, ValueError):
            return _error("invalid_limit", 400)
        rows = service.signals(decisions=decisions, asset_types=asset_types, limit=limit)
        return jsonify({"success": True, "signals": rows, "count": len(rows)})

    def signal_detail(signal_id: str):
        try:
            return jsonify({"success": True, "signal": service.signal(signal_id)})
        except LookupError:
            return _error("signal_not_found", 404)

    def preview(signal_id: str):
        try:
            result = service.preview_execution(signal_id)
            return jsonify({"success": bool(result.get("executable")), **result})
        except LookupError:
            return _error("signal_not_found", 404)

    def execute(signal_id: str):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return _error("json_object_required", 400)
        mode = str(payload.get("mode") or service.config.execution["default_mode"]).strip().lower()
        key = str(payload.get("idempotencyKey") or "").strip()
        try:
            result = service.execute_signal(
                signal_id,
                mode=mode,
                idempotency_key=key,
                confirm_live=payload.get("confirmLive") is True,
            )
        except LookupError:
            return _error("signal_not_found", 404)
        except GrokExecutionError as exc:
            return _error(exc.code, 403, detail=exc.detail)
        status = str(result.get("status") or "")
        if status == "SUCCESS":
            return jsonify({"success": True, **result})
        if result.get("idempotent") and status == "PENDING":
            return jsonify({"success": False, "error": "execution_in_progress", **result}), 409
        return jsonify({"success": False, **result}), (403 if status == "REJECTED" else 503)

    def executions():
        try:
            limit = int(request.args.get("limit", 100))
        except (TypeError, ValueError):
            return _error("invalid_limit", 400)
        rows = service.execution_history(limit=limit)
        return jsonify({"success": True, "executions": rows, "count": len(rows)})

    def replay():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return _error("json_object_required", 400)
        symbol = str(payload.get("symbol") or "").strip()
        if not symbol:
            return _error("symbol_required", 400)
        try:
            bars = int(payload["bars"]) if payload.get("bars") is not None else None
            result = service.replay_pair(symbol, bars=bars)
            return jsonify({"success": True, **result})
        except (TypeError, ValueError):
            return _error("invalid_bars", 400)
        except LookupError:
            return _error("pair_not_found", 404)
        except PermissionError as exc:
            return _error(str(exc), 403)
        except Exception as exc:
            return _error("replay_failed", 503, detail=str(exc))

    add("/api/grok/health", "grok_health", health)
    add("/api/grok/config", "grok_config", config)
    add("/api/grok/accounts", "grok_accounts", accounts)
    add("/api/grok/scan", "grok_scan", scan, ("POST",))
    add("/api/grok/scan/current", "grok_scan_current", current_scan)
    add("/api/grok/signals", "grok_signals", signals)
    add("/api/grok/signals/<signal_id>", "grok_signal_detail", signal_detail)
    add("/api/grok/signals/<signal_id>/preview", "grok_signal_preview", preview, ("POST",))
    add("/api/grok/signals/<signal_id>/execute", "grok_signal_execute", execute, ("POST",))
    add("/api/grok/executions", "grok_executions", executions)
    add("/api/grok/replay", "grok_replay", replay, ("POST",))
    return service
