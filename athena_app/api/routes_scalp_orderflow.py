"""GET /api/scalp-orderflow — chart overlay order-flow events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import jsonify, request

from scalp_orderflow import build_scalp_orderflow_payload


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if value.isdigit():
            return float(value)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def register_scalp_orderflow_routes(app, ctx: Any) -> None:
    find_pair = getattr(ctx, "find_pair_fn", None)

    @app.route("/api/scalp-orderflow", methods=["GET"])
    def api_scalp_orderflow():
        symbol = request.args.get("symbol")
        timeframe = str(request.args.get("timeframe") or request.args.get("tf") or "M5").upper()
        from_ts = _parse_ts(request.args.get("from"))
        to_ts = _parse_ts(request.args.get("to"))
        if not symbol:
            return jsonify({"error": "Missing symbol parameter"}), 400

        pair = find_pair(symbol) if callable(find_pair) else None
        asset_type = pair.get("type") if isinstance(pair, dict) else None
        venue_mismatch = False
        if isinstance(pair, dict):
            data_venue = pair.get("data_venue") or pair.get("source")
            exec_venue = pair.get("execution_provider") or pair.get("execution_venue")
            if data_venue and exec_venue and str(data_venue).lower() != str(exec_venue).lower():
                venue_mismatch = True

        payload = build_scalp_orderflow_payload(
            symbol=str(symbol),
            timeframe=timeframe,
            from_ts=from_ts,
            to_ts=to_ts,
            asset_type=str(asset_type) if asset_type else None,
            venue_mismatch=venue_mismatch,
        )
        return jsonify(payload)
