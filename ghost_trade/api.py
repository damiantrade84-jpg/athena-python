"""Authenticated Flask route registration for standalone Ghost Trade."""

from __future__ import annotations

import json
from typing import Any

from flask import jsonify, request

from .config import GhostConfigError
from .models import AssetGroup, Direction, GhostMode, Style
from .service import GhostService, ScanAlreadyRunning


def _error(code: str, status: int, *, detail: str | None = None):
    payload = {"success": False, "error": code}
    if detail:
        payload["detail"] = detail
    return jsonify(payload), status


def _position_dict(position) -> dict[str, Any]:
    return {
        "positionId": position.position_id,
        "signalId": position.signal_id,
        "source": position.venue.value,
        "canonicalSymbol": position.canonical_symbol,
        "assetGroup": position.asset_group.value,
        "mode": position.mode,
        "status": position.status,
        "direction": position.direction.value,
        "entry": position.entry,
        "stop": position.stop,
        "target": position.target,
        "initialRisk": position.initial_risk,
        "quantity": position.quantity,
        "openedAt": position.opened_at.isoformat() if position.opened_at else None,
        "closedAt": position.closed_at.isoformat() if position.closed_at else None,
        "volatilityRegime": position.volatility_regime.value,
        "entryQuality": position.entry_quality,
        "signalScore": position.signal_score,
    }


def _execution_dict(result) -> dict[str, Any]:
    return {
        "success": result.success,
        "status": result.status,
        "executionId": result.execution_id,
        "error": result.error_code,
        "brokerOrderId": result.broker_order_id,
        "brokerPositionId": result.broker_position_id,
    }


def register_ghost_trade_routes(app, runtime) -> GhostService:
    service: GhostService = runtime.service
    existing = {rule.rule for rule in app.url_map.iter_rules()}

    def add(rule, endpoint, view, methods=("GET",)):
        if rule not in existing:
            app.add_url_rule(rule, endpoint, view, methods=list(methods))
            existing.add(rule)

    def health():
        return jsonify(service.health())

    def get_config():
        return jsonify(service.config_dict())

    def put_config():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("json_object_required", 400)
        try:
            updates = dict(payload)
            if "demoAutoEnabled" in updates:
                service.set_demo_auto_enabled(
                    bool(updates.pop("demoAutoEnabled")),
                    operator_context={
                        "source": "api",
                        "remoteAddress": request.remote_addr or "unknown",
                    },
                )
            if updates:
                service.update_config(updates)
            return jsonify(service.config_dict())
        except PermissionError as exc:
            return _error(str(exc), 403)
        except (ValueError, GhostConfigError) as exc:
            code = str(exc)
            return _error(code if code else "invalid_config", 400)

    def config_route():
        return put_config() if request.method == "PUT" else get_config()

    def universe():
        rows = [item.to_dict() for item in service.repository.list_instruments()]
        return jsonify({"instruments": rows, "count": len(rows)})

    def scan():
        payload = request.get_json(silent=True) or {}
        try:
            style = Style(str(payload.get("style") or "intraday").lower())
        except ValueError:
            return _error("invalid_style", 400)
        try:
            return jsonify(service.start_scan(style=style)), 202
        except ScanAlreadyRunning:
            return _error("scan_already_running", 409)
        except Exception as exc:
            return _error("scan_failed", 503, detail=str(exc))

    def current_scan():
        row = service.repository.latest_scan()
        if row is None:
            return _error("scan_not_found", 404)
        return jsonify(
            {
                "scanId": row["scan_id"],
                "startedAt": row["started_at"],
                "completedAt": row["completed_at"],
                "status": row["status"],
                "sources": json.loads(row["sources_json"]),
                "discoveredCount": row["discovered_count"],
                "scoredCount": row["scored_count"],
                "skippedCount": row["skipped_count"],
                "errors": json.loads(row["errors_json"]),
                "durationMs": row["duration_ms"],
            }
        )

    def signals():
        try:
            group = AssetGroup(request.args["asset_group"].lower()) if request.args.get("asset_group") else None
            direction = Direction(request.args["direction"].upper()) if request.args.get("direction") else None
            minimum = float(request.args["minimum_score"]) if request.args.get("minimum_score") else None
            limit = int(request.args.get("limit", 200))
            offset = int(request.args.get("offset", 0))
        except (ValueError, TypeError):
            return _error("invalid_signal_filter", 400)
        rows = service.repository.list_signals(
            asset_group=group, direction=direction, minimum_score=minimum,
            limit=limit, offset=offset,
        )
        return jsonify(
            {"signals": [item.to_dict() for item in rows], "count": len(rows), "limit": limit, "offset": offset}
        )

    def signal_detail(signal_id: str):
        signal = service.repository.get_signal(signal_id)
        return jsonify(signal.to_dict()) if signal else _error("signal_not_found", 404)

    def execute_demo(signal_id: str):
        signal = service.repository.get_signal(signal_id)
        if signal is None:
            return _error("signal_not_found", 404)
        if service.config.mode is GhostMode.SHADOW:
            return _error("shadow_mode_execution_prohibited", 403)
        payload = request.get_json(silent=True) or {}
        try:
            result = service.execute_demo(
                signal_id, idempotency_key=payload.get("idempotencyKey")
            )
        except PermissionError as exc:
            return _error(str(exc), 403)
        except LookupError as exc:
            return _error(str(exc), 404)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        body = _execution_dict(result)
        if result.success:
            return jsonify(body)
        error = str(result.error_code or "demo_execution_rejected")
        status = 403 if any(token in error for token in ("not_verified", "risk_rejected", "guardian_rejected", "sizing_rejected")) else 409
        return jsonify(body), status

    def dismiss(signal_id: str):
        return jsonify({"success": True, "signalId": signal_id}) if service.repository.dismiss_signal(signal_id) else _error("signal_not_found", 404)

    def positions():
        rows = service.repository.list_positions(status=request.args.get("status"))
        return jsonify({"positions": [_position_dict(item) for item in rows], "count": len(rows)})

    def position_history():
        rows = service.repository.list_closed_trades()
        return jsonify({"trades": rows, "count": len(rows)})

    def close_position(position_id: str):
        position = service.repository.get_position(position_id)
        if position is None:
            return _error("position_not_found", 404)
        if position.mode == "SHADOW":
            return _error("shadow_manual_close_requires_price", 400)
        try:
            result = service.close_demo_position(position_id)
        except PermissionError as exc:
            return _error(str(exc), 403)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        return jsonify(_execution_dict(result)), (200 if result.success else 409)

    def close_all():
        if service.config.mode is GhostMode.SHADOW:
            return _error("shadow_manual_close_requires_price", 400)
        try:
            results = service.close_all_demo_positions()
        except PermissionError as exc:
            return _error(str(exc), 403)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        payload = [_execution_dict(result) for result in results]
        status = 200 if all(result.success for result in results) else 409
        return jsonify({"success": status == 200, "results": payload}), status

    def performance():
        return jsonify(service.performance())

    def groups():
        return jsonify({"groups": service.groups()})

    add("/api/ghost-trade/health", "ghost_trade_health", health)
    add("/api/ghost-trade/config", "ghost_trade_config", config_route, ("GET", "PUT"))
    add("/api/ghost-trade/universe", "ghost_trade_universe", universe)
    add("/api/ghost-trade/scan", "ghost_trade_scan", scan, ("POST",))
    add("/api/ghost-trade/scans/current", "ghost_trade_current_scan", current_scan)
    add("/api/ghost-trade/signals", "ghost_trade_signals", signals)
    add("/api/ghost-trade/signals/<signal_id>", "ghost_trade_signal_detail", signal_detail)
    add("/api/ghost-trade/signals/<signal_id>/execute-demo", "ghost_trade_execute_demo", execute_demo, ("POST",))
    add("/api/ghost-trade/signals/<signal_id>/dismiss", "ghost_trade_dismiss", dismiss, ("POST",))
    add("/api/ghost-trade/positions", "ghost_trade_positions", positions)
    add("/api/ghost-trade/positions/history", "ghost_trade_position_history", position_history)
    add("/api/ghost-trade/positions/<position_id>/close", "ghost_trade_close_position", close_position, ("POST",))
    add("/api/ghost-trade/positions/close-all", "ghost_trade_close_all", close_all, ("POST",))
    add("/api/ghost-trade/performance", "ghost_trade_performance", performance)
    add("/api/ghost-trade/groups", "ghost_trade_groups", groups)
    return service
