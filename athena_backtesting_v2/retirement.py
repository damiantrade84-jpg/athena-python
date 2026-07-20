from __future__ import annotations

from flask import jsonify, request

from .constants import LEGACY_RETIREMENT_CODE


_EXACT_LEGACY_PATHS = frozenset(
    {
        "/api/backtest",
        "/api/backtest-history",
        "/api/backtest-best",
        "/api/advisory-thresholds",
    }
)


def is_legacy_backtest_path(path: str) -> bool:
    normalized = str(path or "").rstrip("/") or "/"
    return bool(
        normalized in _EXACT_LEGACY_PATHS
        or normalized.startswith("/api/backtest")
        or normalized.startswith("/api/backtest-history/")
        or normalized.startswith("/api/advisory-threshold")
        or normalized.startswith("/api/forex/backtest/")
    )


def install_legacy_retirement_guard(app) -> None:
    @app.before_request
    def _retire_legacy_backtests():
        if not is_legacy_backtest_path(request.path):
            return None
        return (
            jsonify(
                {
                    "success": False,
                    "code": LEGACY_RETIREMENT_CODE,
                    "error": "The legacy backtest harness is retired. Use the isolated Engine A/B v2 platform.",
                    "replacement": "/api/v2/backtests",
                }
            ),
            410,
        )
