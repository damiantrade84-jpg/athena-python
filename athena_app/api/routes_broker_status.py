"""Read-only broker status API route handlers and registration."""

from __future__ import annotations

from types import SimpleNamespace

from flask import jsonify

CONFIG: dict = {}


def api_mt5_status():
    """Get MT5 connection status and account info."""

    try:
        from mt5_executor import mt5_get_account, mt5_get_positions

        account = mt5_get_account()

        if not account or account.get("error"):
            return jsonify(
                {
                    "connected": False,
                    "error": account.get("detail", "MT5 not connected")
                    if isinstance(account, dict)
                    else "MT5 not connected",
                }
            )

        _pos_resp = mt5_get_positions()

        if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
            positions = []
        else:
            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

        return jsonify(
            {
                "connected": True,
                "account": account,
                "openPositions": len(positions),
                "positions": positions,
                "executionEnabled": CONFIG.get("EXECUTION_ENABLED", False),
            }
        )

    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})


def api_mt5_positions():
    """Get open MT5 positions."""

    try:
        from mt5_executor import mt5_get_positions

        _pos_resp = mt5_get_positions()

        if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
            return jsonify(
                {
                    "positions": [],
                    "error": _pos_resp.get("detail", "Positions unavailable"),
                }
            ), 503

        return jsonify(
            {
                "positions": _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            }
        )

    except Exception as e:
        return jsonify({"positions": [], "error": str(e)})


def api_bybit_status():
    """Get Bybit Futures connection status and account info."""

    try:
        from bybit_executor import bybit_get_account, bybit_get_positions

        account = bybit_get_account()

        if not account or account.get("error"):
            return jsonify(
                {
                    "connected": False,
                    "error": account.get("detail", "Bybit not connected")
                    if isinstance(account, dict)
                    else "Bybit not connected",
                }
            )

        _pos_resp = bybit_get_positions()

        if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
            positions = []
        else:
            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

        return jsonify(
            {
                "connected": True,
                "account": account,
                "openPositions": len(positions),
                "positions": positions,
            }
        )

    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})


def api_binance_status():
    """Legacy endpoint - redirects to Bybit status."""

    return api_bybit_status()


def register_broker_status_routes(app, runtime: SimpleNamespace) -> None:
    """Register read-only broker status routes using runtime state supplied by athena.py."""
    global CONFIG

    CONFIG = runtime.CONFIG

    app.add_url_rule("/api/mt5-status", "api_mt5_status", api_mt5_status)
    app.add_url_rule("/api/mt5-positions", "api_mt5_positions", api_mt5_positions)
    app.add_url_rule("/api/bybit-status", "api_bybit_status", api_bybit_status)
    app.add_url_rule("/api/binance-status", "api_binance_status", api_binance_status)
