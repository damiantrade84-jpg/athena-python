"""Executed-trades API + fill-notification hook (manual-mirror surfacing)."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from flask import Flask

from athena_ase.contracts import ASESignal
from athena_ase.execution.bridge import ASEExecutionDeps, execute_trade_signal
from athena_ase.execution.journal import (
    append_execution_outcome,
    append_trade_signals,
    load_trade_journal,
)
from athena_ase.registry.promotion import promote_family


@dataclass
class _Approval:
    approved: bool
    volume: float = 0.1
    reason: str = "OK"


@pytest.fixture(autouse=True)
def _promoted_forex_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path / "state"))
    promote_family(
        "forex",
        horizon="intraday",
        version="v1",
        artifact_hash="test-artifact",
        operator="test",
    )


def _trade_signal(**overrides) -> ASESignal:
    base = ASESignal(
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
    return replace(base, **overrides) if overrides else base


def _journal_with_fills(path: Path) -> None:
    s1 = _trade_signal(instrument="EURUSD", decisionTimeMs=1_700_000_000_000)
    s2 = _trade_signal(
        instrument="GBPUSD",
        direction="SHORT",
        entryReference=1.30,
        sl=1.31,
        tp1=1.28,
        tp2=1.27,
        decisionTimeMs=1_700_000_100_000,
    )
    s3 = _trade_signal(instrument="USDJPY", decisionTimeMs=1_700_000_200_000)
    append_trade_signals([s1, s2, s3], path=path)
    append_execution_outcome(
        instrument="EURUSD",
        decision_time_ms=1_700_000_000_000,
        status="filled",
        detail={
            "success": True,
            "orderId": "t-1",
            "volume": 0.2,
            "price": 1.1001,
            "approval_volume": 0.2,
            "pipeline": {
                "gate": "passed",
                "guardian": "passed",
                "risk": "passed",
                "fill": "filled",
            },
        },
        order_id="t-1",
        path=path,
    )
    append_execution_outcome(
        instrument="GBPUSD",
        decision_time_ms=1_700_000_100_000,
        status="filled",
        detail={"success": True, "ticket": "t-2"},
        order_id="t-2",
        path=path,
    )
    append_execution_outcome(
        instrument="USDJPY",
        decision_time_ms=1_700_000_200_000,
        status="rejected",
        detail={"stage": "risk", "reason": "risk_rejected"},
        path=path,
    )


@pytest.fixture()
def client(monkeypatch):
    import athena_app.api.routes_ase as routes_ase

    tmp = Path(tempfile.mkdtemp()) / "ase_trade_journal.parquet"
    _journal_with_fills(tmp)
    monkeypatch.setattr(routes_ase, "load_trade_journal", lambda: load_trade_journal(tmp))
    app = Flask(__name__)
    routes_ase.register_ase_routes(app)
    return app.test_client()


def test_executed_trades_filled_only_sorted_desc(client):
    resp = client.get("/api/ase-executed-trades")
    body = resp.get_json()
    assert body["success"] is True
    assert body["totalExecuted"] == 2
    trades = body["trades"]
    assert [t["instrument"] for t in trades] == ["GBPUSD", "EURUSD"]
    eur = trades[1]
    assert eur["direction"] == "LONG"
    assert eur["sl"] == pytest.approx(1.08)
    assert eur["tp1"] == pytest.approx(1.15)
    assert eur["rr1"] == pytest.approx(abs(1.15 - 1.1) / abs(1.1 - 1.08))
    assert eur["orderId"] == "t-1"
    assert eur["brokerFill"]["volume"] == pytest.approx(0.2)
    assert eur["brokerFill"]["fillPrice"] == pytest.approx(1.1001)
    assert eur["approvalVolume"] == pytest.approx(0.2)
    assert eur["pipeline"] == {
        "gate": "passed",
        "guardian": "passed",
        "risk": "passed",
        "fill": "filled",
    }
    assert trades[0]["brokerFill"]["ticket"] == "t-2"


def test_executed_trades_since_ms_and_limit(client):
    resp = client.get("/api/ase-executed-trades?sinceMs=1700000000000")
    body = resp.get_json()
    assert [t["instrument"] for t in body["trades"]] == ["GBPUSD"]

    resp = client.get("/api/ase-executed-trades?limit=1")
    body = resp.get_json()
    assert body["totalExecuted"] == 2
    assert len(body["trades"]) == 1
    assert body["trades"][0]["instrument"] == "GBPUSD"


def test_executed_trades_status_all_includes_rejections(client):
    resp = client.get("/api/ase-executed-trades?status=all")
    body = resp.get_json()
    assert body["totalExecuted"] == 3
    statuses = {t["instrument"]: t["executionStatus"] for t in body["trades"]}
    assert statuses["USDJPY"] == "rejected"


def test_executed_trades_empty_journal(monkeypatch):
    import athena_app.api.routes_ase as routes_ase

    tmp = Path(tempfile.mkdtemp()) / "missing.parquet"
    monkeypatch.setattr(routes_ase, "load_trade_journal", lambda: load_trade_journal(tmp))
    app = Flask(__name__)
    routes_ase.register_ase_routes(app)
    resp = app.test_client().get("/api/ase-executed-trades")
    body = resp.get_json()
    assert body["success"] is True
    assert body["trades"] == []
    assert body["totalExecuted"] == 0


def _demo_deps() -> ASEExecutionDeps:
    return ASEExecutionDeps(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        get_bybit_base_url=lambda: "https://api-testnet.bybit.com",
        get_kill_switch=lambda: False,
        risk_check=lambda **_k: _Approval(True, volume=0.2),
        guardian_check=lambda *_a, **_k: (True, "OK"),
        route_executor=lambda _s: "mt5",
        mt5_execute=lambda *_a, **_k: {"success": True, "orderId": "demo-1"},
        bybit_execute=lambda *_a, **_k: {"success": False},
    )


def test_fill_notification_fires_on_success(monkeypatch):
    import telegram_notify

    calls: list[dict] = []

    def fake_notify(pair, direction, entry_price, stop_loss, take_profit, **kwargs):
        calls.append(
            {
                "pair": pair,
                "direction": direction,
                "entry": entry_price,
                "sl": stop_loss,
                "tp": take_profit,
                **kwargs,
            }
        )

    monkeypatch.setattr(telegram_notify, "notify_trade_opened", fake_notify)
    result = execute_trade_signal(
        _trade_signal(),
        pair={"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
        deps=_demo_deps(),
        write_journal=False,
    )
    assert result["executed"] is True
    assert len(calls) == 1
    assert calls[0]["pair"] == "EUR/USD"
    assert calls[0]["direction"] == "LONG"
    assert calls[0]["entry"] == pytest.approx(1.1)
    assert calls[0]["sl"] == pytest.approx(1.08)
    assert calls[0]["tp"] == pytest.approx(1.15)
    assert calls[0]["engine"] == "ASE"
    assert calls[0]["exchange"] == "mt5"


def test_fill_notification_failure_does_not_affect_execution(monkeypatch):
    import telegram_notify

    def broken_notify(*_a, **_k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(telegram_notify, "notify_trade_opened", broken_notify)
    result = execute_trade_signal(
        _trade_signal(),
        deps=_demo_deps(),
        write_journal=False,
    )
    assert result["executed"] is True
    assert result["reason"] == "ok"


def test_execution_journal_proves_pipeline_and_risk_approved_volume(monkeypatch):
    import athena_ase.execution.bridge as bridge

    journal_calls: list[dict] = []
    monkeypatch.setattr(
        bridge,
        "append_execution_outcome",
        lambda **kwargs: journal_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(bridge, "_notify_fill_opened", lambda *_a, **_k: None)

    result = execute_trade_signal(
        _trade_signal(),
        deps=_demo_deps(),
        write_journal=False,
        journal_outcomes=True,
    )

    assert result["executed"] is True
    assert result["approval_volume"] == pytest.approx(0.2)
    assert len(journal_calls) == 1
    assert journal_calls[0]["detail"]["approval_volume"] == pytest.approx(0.2)
    assert journal_calls[0]["detail"]["pipeline"] == {
        "gate": "passed",
        "guardian": "passed",
        "risk": "passed",
        "fill": "filled",
    }


def test_no_notification_on_rejected_trade(monkeypatch):
    import telegram_notify

    calls: list[str] = []
    monkeypatch.setattr(
        telegram_notify,
        "notify_trade_opened",
        lambda *_a, **_k: calls.append("notified"),
    )
    deps = ASEExecutionDeps(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        get_bybit_base_url=lambda: "https://api-testnet.bybit.com",
        get_kill_switch=lambda: False,
        risk_check=lambda **_k: _Approval(False, reason="risk_rejected"),
        guardian_check=lambda *_a, **_k: (True, "OK"),
        route_executor=lambda _s: "mt5",
        mt5_execute=lambda *_a, **_k: {"success": True},
    )
    result = execute_trade_signal(_trade_signal(), deps=deps, write_journal=False)
    assert result["executed"] is False
    assert calls == []
