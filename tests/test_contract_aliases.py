"""ASE contract alias tests."""

from __future__ import annotations

from athena_ase.contracts import ASESignal


def _signal():
    return ASESignal(
        engineVersion="ase-2.1",
        modelFamily="forex",
        modelVersion="v0",
        horizon="swing",
        decisionStatus="TRADE",
        direction="LONG",
        expectedNetR=0.2,
        expectedNetBps=20.0,
        probabilityPositive=0.62,
        decisionMargin=0.12,
        signalStrength=40,
        returnQ={"q50": 0.2},
        maeQ={"q50": 0.3},
        mfeQ={"q50": 0.5},
        holdQ={"q50": 8},
        entryReference=1.1,
        entryZone=(1.09, 1.1),
        sl=1.08,
        tp1=1.12,
        tp2=1.14,
        maxHoldBars=10,
        primarySignals=[],
        predictionDiagnostics={},
        dataQuality={"coreOk": True, "route": "core", "missingFeeds": []},
        modelHealth={"artifactHash": "", "trainedAt": "", "brier": None, "driftScore": 0.0, "gateResult": {}},
    )


def test_alias_properties():
    sig = _signal()
    assert sig.confluenceScore == 40
    assert sig.maxScore == 100
    assert sig.scoreNorm == 0.4
    assert sig.confidence == 0.62


def test_execution_dict_marks_ase_engine():
    payload = _signal().to_execution_dict()
    assert payload["engine"] == "ASE"
    assert payload["aseEngine"] is True
    assert payload["decisionStatus"] == "TRADE"
