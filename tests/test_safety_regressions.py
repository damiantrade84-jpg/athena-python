from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

import config
import execution_lifecycle
from ai_reconciliation import arbitrate_ai
from engine_c import compute_consensus
from risk_engine import RiskApproval, risk_check
from timed_exit_monitor import _match_audit_row_for_position


def _fresh_meta() -> dict:
    return {
        "D1": {"stalenessSeverity": "fresh"},
        "H4": {"stalenessSeverity": "fresh"},
        "H1": {"stalenessSeverity": "fresh"},
    }


def _risk_signal(**overrides) -> dict:
    signal = {
        "pair": "BTCUSDT",
        "type": "crypto",
        "direction": "LONG",
        "price": 60000.0,
        "sl": 59000.0,
        "tp1": 62000.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confluenceScore": 3.0,
        "candleFetchMeta": _fresh_meta(),
    }
    signal.update(overrides)
    return signal


def test_lifecycle_paper_soak_blocks_before_broker(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "PAPER_SOAK",
        {"ENABLED": True, "REAL_ORDERS_ALLOWED": False},
    )

    fake = types.ModuleType("mt5_executor")

    def _called(*_args, **_kwargs):
        raise AssertionError("broker executor must not be called in paper soak")

    fake.mt5_cancel_pending_athena_orders = _called
    fake.mt5_execute = _called
    fake.mt5_reconcile_after_open = _called
    monkeypatch.setitem(sys.modules, "mt5_executor", fake)

    approval = RiskApproval(True, 0.01, 10.0, 0.001, 0.001, 0.0, "OK")
    result = execution_lifecycle.run_managed_execution(
        "mt5", {"pair": "EUR/USD", "direction": "LONG"}, approval
    )

    assert result["success"] is False
    assert result["error"] == "PAPER_SOAK_BLOCKED_REAL_ORDER"


def test_lifecycle_blocks_nested_vision_rejection(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "PAPER_SOAK",
        {"ENABLED": False, "REAL_ORDERS_ALLOWED": True},
    )

    fake = types.ModuleType("mt5_executor")
    fake.mt5_cancel_pending_athena_orders = lambda _pair: {"error": False, "cancelled": 0}
    fake.mt5_execute = lambda *_args, **_kwargs: {
        "success": True,
        "ticket": "should-not-fill",
        "volume": 0.01,
    }
    fake.mt5_reconcile_after_open = lambda *_args, **_kwargs: {
        "error": False,
        "allProtectionsPresent": True,
    }
    monkeypatch.setitem(sys.modules, "mt5_executor", fake)

    approval = RiskApproval(True, 0.01, 10.0, 0.001, 0.001, 0.0, "OK")
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "vision_output": {"structured": {"confirms_direction": False}},
    }
    result = execution_lifecycle.run_managed_execution("mt5", signal, approval)

    assert result["success"] is False
    assert result["error_tag"] == "vision_veto"


def test_risk_rejects_missing_freshness_and_missing_timestamp():
    missing_fresh = _risk_signal()
    missing_fresh.pop("candleFetchMeta")
    assert risk_check(missing_fresh, 10000, 10000, []).reason == "MISSING_CANDLE_FRESHNESS"

    missing_ts = _risk_signal(timestamp=None)
    assert risk_check(missing_ts, 10000, 10000, []).reason == "MISSING_SIGNAL_TIMESTAMP"


def test_risk_rejects_b_verdict_missing_checklist_proof():
    signal = _risk_signal(verdict="B_ONLY_SCORED", tier="MEDIUM", decision_state="execute")
    result = risk_check(signal, 10000, 10000, [])

    assert result.approved is False
    assert result.reason == "ENGINE_B_CHECKLIST_MISSING"


def test_engine_c_b_only_missing_levels_does_not_execute():
    result = compute_consensus(
        {},
        {"direction": "LONG", "structural_verdict": "CLEAR"},
        confidence_b={
            "passed": True,
            "pct": 85,
            "score": 85,
            "max_possible": 100,
            "zone_ok": True,
            "trigger_ok": True,
            "structure_ok": True,
        },
        atr=0.0015,
        entry_price=1.1,
    )

    assert result["trade"] is False
    assert result["verdict"] == "B_ONLY_LEVELS_MISSING"


def test_bybit_execute_rejects_missing_or_zero_fill(monkeypatch):
    import bybit_executor

    class _Exchange:
        def fetch_ticker(self, _symbol):
            return {"ask": 1000.0, "bid": 999.0, "last": 1000.0}

        def create_market_order(self, *_args, **_kwargs):
            return {"id": "order-1", "status": "closed", "average": 1000.0, "filled": 0}

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _Exchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bybit_executor, "_set_trading_stop", lambda *_a, **_k: None)
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")

    approval = RiskApproval(True, 0.01, 10.0, 0.001, 0.001, 0.0, "OK")
    result = bybit_executor.bybit_execute(
        {"pair": "BTC/USDT", "direction": "LONG", "sl": 950.0, "tp1": 1100.0},
        approval,
    )

    assert result["success"] is False
    assert "ORDER_NOT_FILLED" in result["error"]


def test_bybit_execute_accepts_cumexecqty_without_normalized_status(monkeypatch):
    """Bybit/CCXT occasionally returns cumulative exec qty without a unified ``status``."""
    import bybit_executor

    class _Exchange:
        def fetch_ticker(self, _symbol):
            return {"ask": 1000.0, "bid": 999.0, "last": 1000.0}

        def create_market_order(self, *_args, **_kwargs):
            # Shape seen on some Bybit v5 submit paths — empty ``filled`` / no CCXT ``status``.
            return {"id": "order-sparse", "info": {"cumExecQty": "0.01"}}

        def fetch_order(self, *_a, **_kwargs):
            raise AssertionError("fill should resolve from submit payload without polling")

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _Exchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bybit_executor, "_set_trading_stop", lambda *_a, **_k: None)
    monkeypatch.setattr(bybit_executor.telegram_notify, "notify_trade_opened", lambda **_k: None)
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")

    approval = RiskApproval(True, 0.01, 10.0, 0.001, 0.001, 0.0, "OK")
    result = bybit_executor.bybit_execute(
        {"pair": "BTC/USDT", "direction": "LONG", "sl": 950.0, "tp1": 1100.0},
        approval,
    )

    assert result["success"] is True
    assert result["volume"] == 0.01
    assert result["ticket"] == "order-sparse"


def test_bybit_execute_poll_uses_fetch_closed_order_when_submit_sparse(monkeypatch):
    import time

    import bybit_executor

    monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)

    calls = {"closed": 0}

    class _Ex:
        def fetch_ticker(self, _symbol):
            return {"ask": 2.5, "bid": 2.49, "last": 2.5}

        def create_market_order(self, *_args, **_kwargs):
            return {"id": "oid-xyz", "info": {}}

        def fetch_closed_order(self, oid, *_a, **_kw):
            calls["closed"] += 1
            return {
                "id": oid,
                "status": "closed",
                "filled": 0.02,
                "average": 2.5,
                "info": {"cumExecQty": "0.02", "orderStatus": "Filled"},
            }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _Ex())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bybit_executor, "_set_trading_stop", lambda *_a, **_k: None)
    monkeypatch.setattr(bybit_executor.telegram_notify, "notify_trade_opened", lambda **_k: None)
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "ALGO/USDT:USDT")

    approval = RiskApproval(True, 0.02, 10.0, 0.001, 0.001, 0.0, "OK")
    # Keep SL within MAX_SL_PCT crypto cap vs ~2.5 ticker (e.g. 4% floor = 2.4)
    result = bybit_executor.bybit_execute(
        {"pair": "ALGO/USDT", "direction": "LONG", "sl": 2.4, "tp1": 2.7},
        approval,
    )

    assert calls["closed"] >= 1
    assert result["success"] is True
    assert result["volume"] == 0.02
def test_monitor_never_matches_closed_audit_row_for_live_position():
    row = {
        "id": 1,
        "ticket": "old",
        "pair": "BTC/USDT:USDT",
        "direction": "LONG",
        "entry_price": 100.0,
        "ts": datetime.now(timezone.utc).isoformat(),
        "exit_time": datetime.now(timezone.utc).isoformat(),
    }
    pos = {
        "ticket": "0",
        "pair": "BTC/USDT:USDT",
        "direction": "LONG",
        "entry": 100.5,
    }

    assert _match_audit_row_for_position(pos, [row]) is None


def test_mt5_split_leg_failure_reports_incomplete_rollback(monkeypatch):
    import mt5_executor

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 10
        ORDER_TIME_GTC = 20
        ORDER_FILLING_IOC = 30
        TRADE_RETCODE_DONE = 10009

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                volume_step=0.01,
                volume_min=0.01,
            )

    calls = {"send": 0}

    def fake_send(request):
        calls["send"] += 1
        if calls["send"] == 1:
            return SimpleNamespace(
                retcode=_FakeMT5.TRADE_RETCODE_DONE,
                order=111,
                volume=request["volume"],
                price=request["price"],
                comment="filled",
            )
        return SimpleNamespace(retcode=10030, order=0, volume=0, price=0, comment="reject")

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)
    monkeypatch.setattr(mt5_executor, "_mt5_resolve_position_ticket", lambda *_args, **_kwargs: 222)
    monkeypatch.setattr(mt5_executor, "mt5_close_position", lambda _ticket: {"success": False, "error": "close failed"})
    monkeypatch.setitem(mt5_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "fixed_tp"})

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "tp2": 1.1250,
        "type": "forex",
        "engine": "engine_a",
        "style": "intraday",
    }
    approval = RiskApproval(True, 0.04, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is False
    assert result["rollbackComplete"] is False
    assert result["partialLegsRolledBack"] == 0
    assert result["rollbackResults"][0]["success"] is False


def test_required_ai_sources_missing_blocks_execution():
    decision = arbitrate_ai(
        {"trace_id": "trace-1"},
        require_trace_id=True,
        required_sources=("debate",),
    )

    assert decision["execution_allowed"] is False
    assert decision["reason"] == "AI_REQUIRED_SOURCES_MISSING"


def test_generic_mt5_backtest_branch_has_no_price_vendor_fallbacks():
    source = (Path(__file__).resolve().parents[1] / "backtest_runner.py").read_text()
    start = source.index('elif pair["source"] == "mt5":')
    end = source.index('elif _ptype in ("stock", "commodity", "index"):', start)
    mt5_branch = source[start:end]

    assert "fetch_eodhd" not in mt5_branch
    assert "_bt_cached_eodhd_intraday" not in mt5_branch
    assert "fetch_yfinance" not in mt5_branch
