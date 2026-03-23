"""Route helper for backtest API."""

from __future__ import annotations


def api_backtest_impl(payload: dict, *, service_handle):
    return service_handle(payload)

