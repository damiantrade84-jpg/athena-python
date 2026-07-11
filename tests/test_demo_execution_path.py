"""Fixture ASE TRADE signal through standalone execution bridge."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from athena_ase.contracts import ASESignal
from athena_ase.execution.bridge import ASEExecutionDeps, execute_trade_signal
from athena_ase.registry.promotion import promote_family


@dataclass
class _Approval:
    approved: bool
    volume: float = 0.1
    reason: str = "OK"


@pytest.fixture(autouse=True)
def _promoted_forex_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path))
    promote_family(
        "forex",
        horizon="intraday",
        version="v1",
        artifact_hash="test-artifact",
        operator="test",
    )


def _trade_signal() -> ASESignal:
    return ASESignal(
        engineVersion="2.1.0",
        modelFamily="forex",
        modelVersion="v1",
        horizon="intraday",
        decisionStatus="TRADE",
        direction="LONG",
        expectedNetR=0.2,
        expectedNetBps=2000.0,
        probabilityPositive=0.62,
        decisionMargin=0.12,
        signalStrength=40,
        returnQ={"q50": 0.3},
        maeQ={"q50": 0.4, "q90": 0.8},
        mfeQ={"q50": 0.9, "q75": 1.2},
        holdQ={"q50": 8},
        entryReference=1.1,
        entryZone=(1.095, 1.1),
        sl=1.08,
        tp1=1.15,
        tp2=1.2,
        maxHoldBars=16,
        primarySignals=[{"name": "tsmom", "direction": 1, "rawStrength": 0.7}],
        predictionDiagnostics={},
        dataQuality={"coreOk": True, "route": "core", "missingFeeds": []},
        modelHealth={"artifactHash": "test-artifact", "gateResult": {"ok": True}},
        instrument="EURUSD",
        decisionTimeMs=1_700_000_000_000,
    )


def test_demo_execution_order_demo_gate_guardian_risk_executor():
    calls: list[str] = []
    executed: dict = {}

    def risk_check(**kwargs):
        calls.append("risk")
        assert kwargs["signal"]["aseExecution"] is True
        assert kwargs["kill_switch"] is False
        return _Approval(True, volume=0.2)

    def mt5_execute(signal, approval):
        calls.append("mt5")
        executed["tp2"] = signal.get("tp2")
        executed["volume"] = approval.volume
        return {"success": True, "orderId": "demo-1"}

    deps = ASEExecutionDeps(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        get_bybit_base_url=lambda: "https://api-testnet.bybit.com",
        get_kill_switch=lambda: False,
        risk_check=risk_check,
        guardian_check=lambda *_a, **_k: (True, "OK"),
        route_executor=lambda _s: "mt5",
        mt5_execute=mt5_execute,
        bybit_execute=lambda *_a, **_k: {"success": False},
    )

    result = execute_trade_signal(
        _trade_signal(),
        pair={"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
        deps=deps,
        write_journal=False,
    )

    assert result["executed"] is True
    assert calls == ["risk", "mt5"]
    assert executed["tp2"] == 1.2
    assert executed["volume"] == 0.2


def test_kill_switch_halts_submission():
    deps = ASEExecutionDeps(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        get_bybit_base_url=lambda: "https://api-testnet.bybit.com",
        get_kill_switch=lambda: True,
        risk_check=lambda **_k: _Approval(False, reason="KILL_SWITCH_ACTIVE"),
        guardian_check=lambda *_a, **_k: (True, "OK"),
        route_executor=lambda _s: "mt5",
        mt5_execute=lambda *_a, **_k: {"success": True},
    )
    result = execute_trade_signal(_trade_signal(), deps=deps, write_journal=False)
    assert result["executed"] is False
    assert "risk" in result["reason"]


def test_non_demo_gate_rejects_execution():
    deps = ASEExecutionDeps(
        get_executor_mode=lambda: "live",
        get_mt5_trade_mode=lambda: 2,
        get_bybit_base_url=lambda: "https://api.bybit.com",
    )
    result = execute_trade_signal(_trade_signal(), deps=deps, write_journal=False)
    assert result["executed"] is False
    assert "demo_gate" in result["reason"]


def test_time_stop_closes_stale_ase_position():
    closed: list[dict] = []

    def close_position(pos):
        closed.append(pos)
        return {"success": True}

    deps = ASEExecutionDeps(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        get_bybit_base_url=lambda: "https://api-testnet.bybit.com",
        close_position=close_position,
    )
    from athena_ase.execution.bridge import enforce_time_stops

    now_ms = 1_700_000_000_000
    opened = now_ms - (17 * 3_600_000)
    positions = [
        {
            "engine": "ASE",
            "aseExecution": True,
            "horizon": "intraday",
            "maxHoldBars": 16,
            "openedAtMs": opened,
            "pair": "EUR/USD",
        }
    ]
    result = enforce_time_stops(positions, deps=deps, now_ms=now_ms)
    assert len(result) == 1
    assert closed[0]["pair"] == "EUR/USD"
