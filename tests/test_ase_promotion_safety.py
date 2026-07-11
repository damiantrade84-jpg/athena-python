"""ASE deployment binding is an execution safety boundary."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from athena_ase.contracts import ASESignal
from athena_ase.execution.bridge import ASEExecutionDeps, execute_trade_signal
from athena_ase.registry.promotion import promote_family


@dataclass
class _Approval:
    approved: bool = True
    volume: float = 0.1
    reason: str = "OK"


def _trade_signal(
    *,
    horizon: str = "intraday",
    version: str = "v-safe",
    artifact_hash: str = "artifact-safe",
) -> ASESignal:
    return ASESignal(
        engineVersion="2.1.0",
        modelFamily="forex",
        modelVersion=version,
        horizon=horizon,  # type: ignore[arg-type]
        decisionStatus="TRADE",
        direction="LONG",
        expectedNetR=0.2,
        expectedNetBps=20.0,
        probabilityPositive=0.62,
        decisionMargin=0.12,
        signalStrength=40,
        returnQ={},
        maeQ={},
        mfeQ={},
        holdQ={},
        entryReference=1.1,
        entryZone=(1.095, 1.1),
        sl=1.08,
        tp1=1.15,
        tp2=1.2,
        maxHoldBars=16,
        primarySignals=[],
        predictionDiagnostics={},
        dataQuality={"coreOk": True, "route": "core", "missingFeeds": []},
        modelHealth={"artifactHash": artifact_hash, "gateResult": {"ok": True}},
        instrument="EURUSD",
        decisionTimeMs=1_700_000_000_000,
    )


def _deps(calls: list[str]) -> ASEExecutionDeps:
    def risk_check(**_kwargs):
        calls.append("risk")
        return _Approval()

    def execute(_signal, _approval):
        calls.append("broker")
        return {"success": True, "orderId": "demo-1"}

    return ASEExecutionDeps(
        risk_check=risk_check,
        mt5_execute=execute,
        guardian_check=lambda *_args: (calls.append("guardian") or True, "OK"),
    )


def test_shadow_family_is_rejected_before_guardian_risk_or_broker(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path))
    calls: list[str] = []

    result = execute_trade_signal(
        _trade_signal(), deps=_deps(calls), write_journal=False
    )

    assert result == {"executed": False, "reason": "promotion:shadow_not_promoted"}
    assert calls == []


@pytest.mark.parametrize(
    ("horizon", "version", "artifact_hash", "reason"),
    [
        ("swing", "v-safe", "artifact-safe", "horizon_not_promoted"),
        ("intraday", "v-other", "artifact-safe", "artifact_version_mismatch"),
        ("intraday", "v-safe", "artifact-other", "artifact_hash_mismatch"),
    ],
)
def test_execution_requires_exact_promoted_binding(
    tmp_path, monkeypatch, horizon, version, artifact_hash, reason
):
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path))
    promote_family(
        "forex",
        horizon="intraday",
        version="v-safe",
        artifact_hash="artifact-safe",
        operator="test",
    )
    calls: list[str] = []

    result = execute_trade_signal(
        _trade_signal(
            horizon=horizon,
            version=version,
            artifact_hash=artifact_hash,
        ),
        deps=_deps(calls),
        write_journal=False,
    )

    assert result == {"executed": False, "reason": f"promotion:{reason}"}
    assert calls == []


def test_exact_promoted_binding_can_reach_existing_safety_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path))
    promote_family(
        "forex",
        horizon="intraday",
        version="v-safe",
        artifact_hash="artifact-safe",
        operator="test",
    )
    calls: list[str] = []

    result = execute_trade_signal(
        _trade_signal(), deps=_deps(calls), write_journal=False
    )

    assert result["executed"] is True
    assert calls == ["guardian", "risk", "broker"]
