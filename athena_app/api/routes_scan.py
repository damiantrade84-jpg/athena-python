"""Route handler helpers for scan APIs."""

from __future__ import annotations


def api_scan_impl(payload: dict, *, scan_service_handle):
    return scan_service_handle(payload)

