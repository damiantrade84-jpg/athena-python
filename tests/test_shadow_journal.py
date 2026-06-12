"""Shadow journal tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from athena_ase.contracts import ASESignal
from athena_ase.shadow.journal import append_shadow_signals, load_shadow_journal


def _signal() -> ASESignal:
    return ASESignal(
        engineVersion="2.1.0",
        modelFamily="forex",
        modelVersion="v0",
        horizon="swing",
        decisionStatus="WATCH",
        direction="LONG",
        expectedNetR=0.05,
        expectedNetBps=5.0,
        probabilityPositive=0.56,
        decisionMargin=0.06,
        signalStrength=10,
        returnQ={},
        maeQ={},
        mfeQ={},
        holdQ={},
        entryReference=1.0,
        entryZone=(0.99, 1.0),
        sl=0.98,
        tp1=1.02,
        tp2=1.04,
        maxHoldBars=10,
        primarySignals=[],
        predictionDiagnostics={},
        dataQuality={"coreOk": True, "route": "core", "missingFeeds": []},
        modelHealth={"artifactHash": "", "trainedAt": "", "brier": None, "driftScore": 0.0, "gateResult": {}},
        instrument="EURUSD",
    )


def test_shadow_journal_append_and_read(tmp_path: Path):
    path = tmp_path / "shadow.parquet"
    append_shadow_signals([_signal()], path=path)
    df = load_shadow_journal(path=path)
    assert len(df) == 1
    assert df.iloc[0]["modelFamily"] == "forex"
