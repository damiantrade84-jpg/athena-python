from __future__ import annotations

import csv
import io
from typing import Any

from flask import Response, jsonify, request

from .models import RequestValidationError
from .service import BacktestingV2Service


def _page_arg(name: str, default: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(0, min(maximum, value))


def register_backtesting_v2_routes(app, service: BacktestingV2Service) -> None:
    @app.before_request
    def _enforce_v2_feature_flag():
        if request.path.startswith("/api/v2/backtest") and not service.enabled:
            return jsonify({
                "success": False,
                "code": "BACKTESTING_V2_DISABLED",
                "error": "Engine A/B backtesting v2 is disabled by feature flag",
            }), 404
        return None

    def capabilities():
        return jsonify({"success": True, "data": service.capabilities()})

    def datasets_collection():
        if request.method == "GET":
            limit = _page_arg("limit", 100, 500)
            offset = _page_arg("offset", 0, 10_000_000)
            return jsonify({"success": True, "data": service.list_datasets(limit=limit, offset=offset), "limit": limit, "offset": offset})
        payload = request.get_json(force=True, silent=True) or {}
        try:
            if bool(payload.get("prepare", False)):
                data = service.prepare_dataset(payload)
                return jsonify({"success": True, "data": data}), 201
            return jsonify({"success": True, "data": service.preflight(payload)}), 200
        except RequestValidationError as exc:
            return jsonify(exc.to_dict()), 422
        except Exception as exc:
            return jsonify({"success": False, "code": "DATASET_PREPARATION_FAILED", "error": str(exc)}), 422

    def backtests_collection():
        if request.method == "GET":
            limit = _page_arg("limit", 100, 500)
            offset = _page_arg("offset", 0, 10_000_000)
            return jsonify({"success": True, "data": service.list_runs(limit=limit, offset=offset), "limit": limit, "offset": offset})
        try:
            data, reused = service.submit(request.get_json(force=True, silent=True) or {})
            return jsonify({"success": True, "data": data}), 200 if reused else 202
        except RequestValidationError as exc:
            return jsonify(exc.to_dict()), 422

    def backtest_detail(run_id: str):
        run = service.get_run(run_id)
        if run is None:
            return jsonify({"success": False, "code": "RUN_NOT_FOUND", "error": "backtest run not found"}), 404
        return jsonify({"success": True, "data": run})

    def backtest_trades(run_id: str):
        if service.get_run(run_id) is None:
            return jsonify({"success": False, "code": "RUN_NOT_FOUND", "error": "backtest run not found"}), 404
        limit = _page_arg("limit", 100, 1_000)
        offset = _page_arg("offset", 0, 10_000_000)
        return jsonify({"success": True, "data": service.trades(run_id, limit=limit, offset=offset)})

    def cancel_backtest(run_id: str):
        if not service.cancel(run_id):
            return jsonify({"success": False, "code": "RUN_NOT_CANCELLABLE", "error": "run not found or already terminal"}), 409
        return jsonify({"success": True, "data": {"runId": run_id, "cancelRequested": True}}), 202

    def export_backtest(run_id: str):
        run = service.get_run(run_id)
        if run is None:
            return jsonify({"success": False, "code": "RUN_NOT_FOUND", "error": "backtest run not found"}), 404
        fmt = str(request.args.get("format", "json")).lower()
        trades = service.trades(run_id, limit=1_000_000, offset=0)["items"]
        if fmt == "csv":
            output = io.StringIO()
            fields = [
                "tradeId", "engine", "symbol", "direction", "style", "signal_time", "entry_time",
                "exit_time", "entry_price", "exit_price", "stop_price", "gross_r", "cost_r", "net_r",
                "exit_reason", "ambiguous_same_bar", "duration_bars", "split",
            ]
            writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(trades)
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{run_id}-trades.csv"'},
            )
        return Response(
            __import__("json").dumps({"run": run, "trades": trades}, ensure_ascii=False, indent=2, default=str),
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )

    def comparisons_collection():
        if request.method == "GET":
            return jsonify({"success": True, "data": service.list_comparisons(limit=_page_arg("limit", 100, 500))})
        try:
            result = service.create_comparison(request.get_json(force=True, silent=True) or {})
            return jsonify({"success": True, "data": result}), 201
        except RequestValidationError as exc:
            return jsonify(exc.to_dict()), 422

    app.add_url_rule("/api/v2/backtest-capabilities", "v2_backtest_capabilities", capabilities, methods=["GET"])
    app.add_url_rule("/api/v2/backtest-datasets", "v2_backtest_datasets", datasets_collection, methods=["GET", "POST"])
    app.add_url_rule("/api/v2/backtests", "v2_backtests", backtests_collection, methods=["GET", "POST"])
    app.add_url_rule("/api/v2/backtests/<run_id>", "v2_backtest_detail", backtest_detail, methods=["GET"])
    app.add_url_rule("/api/v2/backtests/<run_id>/trades", "v2_backtest_trades", backtest_trades, methods=["GET"])
    app.add_url_rule("/api/v2/backtests/<run_id>/cancel", "v2_backtest_cancel", cancel_backtest, methods=["POST"])
    app.add_url_rule("/api/v2/backtests/<run_id>/export", "v2_backtest_export", export_backtest, methods=["GET"])
    app.add_url_rule("/api/v2/backtest-comparisons", "v2_backtest_comparisons", comparisons_collection, methods=["GET", "POST"])
