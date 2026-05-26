"""Read-only timing metadata on /api/open-trades-timed."""

from __future__ import annotations

import mt5_executor

from tests.test_scalp_execution import _load_athena_module


def test_open_trades_timed_includes_server_timing_ms(monkeypatch):
    athena_module = _load_athena_module()

    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_positions",
        lambda: {
            "error": False,
            "positions": [
                {
                    "ticket": "123",
                    "pair": "EUR/USD",
                    "direction": "LONG",
                    "profit": 5.0,
                    "entry": 1.1,
                    "sl": 1.09,
                    "tp": 1.11,
                    "volume": 0.01,
                    "open_time": 1710000000,
                }
            ],
        },
    )
    import bybit_executor
    import timed_exit_monitor

    monkeypatch.setattr(bybit_executor, "bybit_get_positions", lambda: {"error": False, "positions": []})
    monkeypatch.setattr(
        timed_exit_monitor,
        "_load_recent_audit_rows",
        lambda _db: [{"ticket": "123", "style": "scalp", "engine": "scalp", "ts": "2026-04-14T10:00:00+00:00"}],
    )
    monkeypatch.setattr(
        timed_exit_monitor,
        "_match_audit_row_for_position",
        lambda p, rows: rows[0],
    )
    monkeypatch.setattr(
        athena_module,
        "_unresolved_audit_rows_for_display",
        lambda *args, **kwargs: [],
    )

    client = athena_module.app.test_client()
    resp = client.get("/api/open-trades-timed")
    assert resp.status_code == 200
    payload = resp.get_json()
    timing = payload.get("_server_timing_ms") or {}
    for key in (
        "mt5_ms",
        "bybit_ms",
        "brokers_parallel_ms",
        "audit_load_ms",
        "enrich_ms",
        "unresolved_ms",
        "total_ms",
        "position_count",
    ):
        assert key in timing, f"missing timing key: {key}"
    assert timing["position_count"] == 1
    assert timing["total_ms"] >= 0
