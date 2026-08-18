"""Scan/backtest request orchestration extracted from athena.py."""

from __future__ import annotations

from typing import Callable


def handle_scan_request(payload: dict, *, run_full_scan: Callable) -> dict:
    """Build scan request contract and run scan."""
    return run_full_scan(
        style=payload.get("style", "auto"),
        asset_class=payload.get("asset_class"),
        refresh_market_data=bool(payload.get("refresh_market_data", False)),
        # Explicit operator symbol scope (Live Cockpit). Absent/empty keeps the
        # historical whole-universe / asset-class behaviour unchanged.
        symbols=payload.get("symbols"),
    )


# handle_backtest_request was retired with the legacy backtester: the v3
# rebuild's Flask blueprint (athena_backtest.api) owns request normalization
# for /api/backtest and /api/backtest-naked now.

