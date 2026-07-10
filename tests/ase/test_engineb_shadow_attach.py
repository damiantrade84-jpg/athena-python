"""Phase 3 — Engine B forex cards gain a read-only aseShadow block."""

from __future__ import annotations

import copy
import tempfile
import time

import pytest

import athena_ase.context.engine_b_shadow as ebs
from athena_ase.contracts import ASESignal


def _trade_signal(instrument: str, direction: str, decision_ms: int) -> ASESignal:
    return ASESignal(
        engineVersion="2.1.0",
        modelFamily="forex",
        modelVersion="v2-integrity",
        horizon="intraday",
        decisionStatus="TRADE",
        direction=direction,  # type: ignore[arg-type]
        expectedNetR=0.14,
        expectedNetBps=10.0,
        probabilityPositive=0.61,
        decisionMargin=0.1,
        signalStrength=50,
        returnQ={}, maeQ={}, mfeQ={}, holdQ={},
        entryReference=1.1, entryZone=(1.09, 1.11),
        sl=1.09, tp1=1.12, tp2=1.13, maxHoldBars=16,
        primarySignals=[], predictionDiagnostics={},
        dataQuality={"coreOk": True, "route": "core", "missingFeeds": []},
        modelHealth={"gateResult": {"ok": True}},
        instrument=instrument,
        decisionTimeMs=decision_ms,
        fxContext={"bias": -0.42, "carry_diff": -1.0, "trend_diff": -0.3, "freshness": "ok"},
        triangular={"label": "CONSISTENT", "legs": {}},
    )


@pytest.fixture()
def state_root(monkeypatch):
    root = tempfile.mkdtemp(prefix="ase_state_")
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", root)
    ebs._JOURNAL_CACHE.update({"mtime": None, "df": None})
    yield root
    ebs._JOURNAL_CACHE.update({"mtime": None, "df": None})


def _card(direction: str = "LONG") -> dict:
    return {
        "id": "NKD_EUR/USD_LONG_1",
        "symbol": "EURUSD",
        "display": "EUR/USD",
        "type": "forex",
        "direction": direction,
        "confluenceScore": 7,
        "confluencePct": 63.0,
        "score_pct": 63.0,
        "sl": 1.09,
        "tp1": 1.12,
    }


def test_opposed_alignment_and_card_untouched(state_root):
    from athena_ase.execution.journal import append_trade_signals

    now_ms = int(time.time() * 1000)
    append_trade_signals([_trade_signal("EURUSD", "SHORT", now_ms)])

    card = _card("LONG")
    before = copy.deepcopy(card)
    ebs.attach_ase_shadow(card)

    shadow = card.pop("aseShadow")
    assert card == before  # every pre-existing field byte-identical
    assert shadow["direction"] == "SHORT"
    assert shadow["alignment"] == "OPPOSED"
    assert shadow["expectedNetR"] == pytest.approx(0.14)
    assert shadow["probabilityPositive"] == pytest.approx(0.61)
    assert shadow["fxContextBias"] == pytest.approx(-0.42)
    assert shadow["triangular"] == "CONSISTENT"
    assert ebs.alignment_journal_path().exists()


def test_missing_ase_data_yields_flat_and_card_survives(state_root):
    card = _card("LONG")
    ebs.attach_ase_shadow(card)
    assert card["aseShadow"]["direction"] == "NONE"
    assert card["aseShadow"]["alignment"] == "ASE_FLAT"
    assert card["score_pct"] == 63.0


def test_stale_ase_row_is_flat(state_root):
    from athena_ase.execution.journal import append_trade_signals

    stale_ms = int(time.time() * 1000) - 3 * 3_600_000  # > 2× H1 bar
    append_trade_signals([_trade_signal("EURUSD", "LONG", stale_ms)])
    card = _card("LONG")
    ebs.attach_ase_shadow(card)
    assert card["aseShadow"]["alignment"] == "ASE_FLAT"


def test_non_forex_card_gets_no_shadow(state_root):
    card = {"id": "x", "symbol": "BTCUSDT", "type": "crypto", "direction": "LONG"}
    ebs.attach_ase_shadow(card)
    assert "aseShadow" not in card
