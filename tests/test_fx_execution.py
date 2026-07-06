"""Tests for athena_fx execution bridge (demo MT5 path, no live broker)."""

from __future__ import annotations

from dataclasses import dataclass

from athena_fx.execution import (
    FxExecutionDeps,
    assert_fx_execution_allowed,
    execute_fx_decision,
    fx_decision_to_execution_dict,
    sl_tp_from_atr,
)
from athena_fx.models import ACTION_BUY


@dataclass
class _Approval:
    approved: bool
    volume: float = 0.1
    reason: str = "OK"


def _buy_decision(**overrides) -> dict:
    base = {
        "symbol": "EURUSD",
        "as_of_date": "2026-01-15",
        "action": ACTION_BUY,
        "direction": "LONG",
        "total_score": 42.0,
        "size_multiplier": 1.0,
        "aligned_families": ["carry", "value"],
        "gate_results": [],
    }
    base.update(overrides)
    return base


def test_sl_tp_from_atr_long():
    sl, tp1, tp2 = sl_tp_from_atr(1.1000, "LONG", 10.0, "EURUSD", sl_mult=2.0, tp_mult=4.0)
    assert sl < 1.1000
    assert tp1 > 1.1000
    assert tp2 > tp1


def test_fx_decision_to_execution_dict_flags():
    sig = fx_decision_to_execution_dict(
        _buy_decision(),
        entry_price=1.1,
        sl=1.08,
        tp1=1.15,
        tp2=1.18,
    )
    assert sig["fxFactorExecution"] is True
    assert sig["engine"] == "FX_FACTOR"
    assert sig["pair"] == "EUR/USD"
    assert sig["direction"] == "LONG"


def test_assert_fx_execution_blocked_when_disabled():
    deps = FxExecutionDeps(
        fx_execution_enabled=lambda: False,
        execution_enabled=lambda: True,
    )
    ok, reason = assert_fx_execution_allowed(deps)
    assert ok is False
    assert reason == "FX_FACTOR_EXECUTION_DISABLED"


def test_fx_execution_dict_missing_candle_freshness_rejected():
    from risk_engine import risk_check

    sig = fx_decision_to_execution_dict(
        _buy_decision(),
        entry_price=1.1,
        sl=1.08,
        tp1=1.15,
        tp2=1.18,
    )
    approval = risk_check(
        sig,
        42000.0,
        42000.0,
        [],
        symbol_info={"volume_min": 0.01, "volume_step": 0.01},
    )
    assert approval.approved is False
    assert approval.reason == "MISSING_CANDLE_FRESHNESS"


def test_execute_fx_decision_hydrates_before_risk(monkeypatch):
    order: list[str] = []

    def fake_hydrate(sig, *, _r):
        order.append("hydrate")
        sig["candleFetchMeta"] = {
            "H1": {"stalenessSeverity": "fresh"},
            "H4": {"stalenessSeverity": "fresh"},
            "D1": {"stalenessSeverity": "fresh"},
        }

    monkeypatch.setattr(
        "athena_fx.execution._hydrate_fx_execution_candle_quality",
        lambda exec_dict: fake_hydrate(exec_dict, _r=None),
    )

    def risk_check(**kwargs):
        order.append("risk")
        assert kwargs["signal"].get("candleFetchMeta")
        return _Approval(True, volume=0.12)

    deps = FxExecutionDeps(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        execution_enabled=lambda: True,
        fx_execution_enabled=lambda: True,
        mt5_execution_enabled=lambda: True,
        get_kill_switch=lambda: False,
        get_live_price=lambda _s: 1.1005,
        load_bars=lambda _s, _d: [
            {"date": f"2026-01-{d:02d}", "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105}
            for d in range(1, 25)
        ],
        get_account=lambda: {"balance": 42000.0, "equity": 42000.0},
        get_positions=lambda: ([], None),
        get_symbol_info=lambda _s, _p: {"volume_min": 0.01, "volume_step": 0.01},
        guardian_check=lambda *_a, **_k: (True, "OK"),
        risk_check=risk_check,
        mt5_execute=lambda *_a, **_k: {"success": True, "ticket": 1},
    )

    result = execute_fx_decision(_buy_decision(), deps=deps)
    assert result["executed"] is True
    assert order == ["hydrate", "risk"]


def test_execute_fx_decision_demo_path(monkeypatch):
    calls: list[str] = []

    def _stub_hydrate(exec_dict):
        exec_dict["candleFetchMeta"] = {
            "H1": {"stalenessSeverity": "fresh"},
            "H4": {"stalenessSeverity": "fresh"},
            "D1": {"stalenessSeverity": "fresh"},
        }

    monkeypatch.setattr(
        "athena_fx.execution._hydrate_fx_execution_candle_quality",
        _stub_hydrate,
    )

    def risk_check(**kwargs):
        calls.append("risk")
        assert kwargs["signal"]["fxFactorExecution"] is True
        assert kwargs["execution_context"] == "fx_factor_bridge"
        assert kwargs["signal"].get("candleFetchMeta")
        return _Approval(True, volume=0.12)

    def mt5_execute(signal, approval):
        calls.append("mt5")
        assert signal["symbol"] == "EURUSD"
        assert approval.volume == 0.12
        return {"success": True, "ticket": 999}

    deps = FxExecutionDeps(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        execution_enabled=lambda: True,
        fx_execution_enabled=lambda: True,
        mt5_execution_enabled=lambda: True,
        get_kill_switch=lambda: False,
        get_live_price=lambda _s: 1.1005,
        load_bars=lambda _s, _d: [
            {"date": f"2026-01-{d:02d}", "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105}
            for d in range(1, 25)
        ],
        get_account=lambda: {"balance": 42000.0, "equity": 42000.0},
        get_positions=lambda: ([], None),
        get_symbol_info=lambda _s, _p: {"volume_min": 0.01, "volume_step": 0.01},
        guardian_check=lambda *_a, **_k: (True, "OK"),
        risk_check=risk_check,
        mt5_execute=mt5_execute,
    )

    result = execute_fx_decision(_buy_decision(), deps=deps)
    assert result["executed"] is True
    assert calls == ["risk", "mt5"]


def test_execute_fx_decision_dry_run():
    deps = FxExecutionDeps(
        execution_enabled=lambda: True,
        fx_execution_enabled=lambda: True,
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        get_live_price=lambda _s: 1.1,
        load_bars=lambda _s, _d: [
            {
                "date": f"2026-01-{d:02d}",
                "open": 1.1,
                "high": 1.11,
                "low": 1.09,
                "close": 1.105,
            }
            for d in range(1, 20)
        ],
    )
    result = execute_fx_decision(_buy_decision(), deps=deps, dry_run=True)
    assert result.get("dry_run") is True
    assert result["reason"] == "dry_run"
