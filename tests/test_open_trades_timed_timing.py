"""Read-only timing metadata on /api/open-trades-timed."""

from __future__ import annotations

import mt5_executor

from tests.test_scalp_execution import _load_athena_module


def test_open_trades_timed_includes_server_timing_ms(monkeypatch):
    athena_module = _load_athena_module()

    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_positions",
        lambda **_kwargs: {
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


def _open_trades_timed_client(monkeypatch):
    athena_module = _load_athena_module()

    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_positions",
        lambda **_kwargs: {
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
                    "currentPrice": 1.101,
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
        lambda _db: [
            {
                "ticket": "123",
                "style": "scalp",
                "engine": "scalp",
                "ts": "2026-04-14T10:00:00+00:00",
                "direction": "LONG",
                "entry_price": 1.1,
                "sl": 1.09,
                "tp": 1.11,
            }
        ],
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
    return athena_module


def test_open_trades_timed_skips_sl_recompute_by_default(monkeypatch):
    athena_module = _open_trades_timed_client(monkeypatch)
    recompute_calls = {"n": 0}

    def _track_recompute(*_args, **_kwargs):
        recompute_calls["n"] += 1
        return {"sl": 1.09, "atr": 0.001, "sl_mult": 1.5, "max_sl_pct": 0.025}

    monkeypatch.setattr(athena_module, "_recompute_sl_diagnostic_levels", _track_recompute)
    athena_module.CONFIG.setdefault("OPEN_TRADES", {})["SL_RECOMPUTE_ON_POLL"] = False

    client = athena_module.app.test_client()
    resp = client.get("/api/open-trades-timed")
    assert resp.status_code == 200
    payload = resp.get_json()
    timing = payload.get("_server_timing_ms") or {}

    assert recompute_calls["n"] == 0
    assert timing.get("enrich_ms", 999) < 500
    assert payload["positions"][0].get("sl_close_line")


def test_open_trades_timed_can_enable_sl_recompute_on_poll(monkeypatch):
    athena_module = _open_trades_timed_client(monkeypatch)
    recompute_calls = {"n": 0}

    def _track_recompute(*_args, **_kwargs):
        recompute_calls["n"] += 1
        return {"sl": 1.09, "atr": 0.001, "sl_mult": 1.5, "max_sl_pct": 0.025}

    monkeypatch.setattr(athena_module, "_recompute_sl_diagnostic_levels", _track_recompute)
    athena_module.CONFIG.setdefault("OPEN_TRADES", {})["SL_RECOMPUTE_ON_POLL"] = True

    client = athena_module.app.test_client()
    resp = client.get("/api/open-trades-timed")
    assert resp.status_code == 200
    assert recompute_calls["n"] == 1


def test_open_trades_timed_returns_bybit_when_mt5_errors(monkeypatch):
    athena_module = _open_trades_timed_client(monkeypatch)
    import bybit_executor

    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_positions",
        lambda **kwargs: {
            "error": True,
            "symbol": "MT5",
            "detail": "MT5 not connected",
            "positions": [],
        },
    )
    monkeypatch.setattr(
        bybit_executor,
        "bybit_get_positions",
        lambda: {
            "error": False,
            "positions": [
                {
                    "ticket": "bybit-1",
                    "pair": "BTC/USDT",
                    "direction": "LONG",
                    "profit": 12.5,
                    "entry": 100.0,
                    "sl": 99.0,
                    "tp": 104.0,
                    "volume": 0.1,
                    "markPrice": 101.0,
                }
            ],
        },
    )

    client = athena_module.app.test_client()
    resp = client.get("/api/open-trades-timed")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["count"] == 1
    assert payload["positions"][0]["exchange"] == "bybit"
    assert payload["positions"][0]["profit"] == 12.5
    assert payload.get("broker_errors", {}).get("mt5")
