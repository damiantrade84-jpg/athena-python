"""Post-fill reconcile parity: Bybit must fail closed on reconcile errors like MT5."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk_engine import RiskApproval


def test_bybit_reconcile_error_marks_execution_failed():
    from execution_lifecycle import run_managed_execution

    signal = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 50000.0,
        "sl": 49000.0,
        "tp1": 52000.0,
        "type": "crypto",
    }
    approval = RiskApproval(True, 0.01, 100.0, 0.01, 0.01, 0.0, "OK")

    with (
        patch(
            "execution_lifecycle._paper_soak_blocks_real_orders",
            return_value=False,
        ),
        patch(
            "execution_lifecycle._vision_blocks_execution",
            return_value=False,
        ),
        patch(
            "bybit_executor.bybit_map_symbol",
            return_value="BTC/USDT:USDT",
        ),
        patch(
            "bybit_executor.bybit_cancel_stale_entry_orders",
            return_value={"error": False, "cancelled": 0},
        ),
        patch(
            "bybit_executor.bybit_execute",
            return_value={"success": True, "symbol": "BTC/USDT:USDT"},
        ),
        patch(
            "bybit_executor.bybit_reconcile_after_open",
            return_value={"error": True, "detail": "exchange_unreachable"},
        ),
    ):
        out = run_managed_execution("bybit", signal, approval)

    assert out.get("success") is False
    assert "BYBIT_PROTECTION_RECONCILE_FAILED" in (out.get("error") or "")


def test_bybit_sl_cap_failure_rejects_order(monkeypatch):
    from risk_engine import RiskApproval
    import bybit_executor

    class _FakeExchange:
        def fetch_ticker(self, symbol):
            return {"ask": 50000.0, "bid": 49999.0, "last": 50000.0}

    approval = RiskApproval(True, 0.01, 100.0, 0.01, 0.01, 0.0, "OK")
    signal = {
        "pair": "BTCUSDT",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "price": 50000.0,
        "sl": 49000.0,
        "tp1": 52000.0,
        "type": "crypto",
    }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "risk_engine.resolve_max_sl_pct",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cap check failed")),
    )

    out = bybit_executor.bybit_execute(signal, approval)
    assert out.get("success") is False
    assert "SL_CAP_CHECK_FAILED" in (out.get("error") or "")
