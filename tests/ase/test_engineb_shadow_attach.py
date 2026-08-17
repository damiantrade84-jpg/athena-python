"""Phase 3 — Engine B forex cards gain a read-only aseShadow block."""

from __future__ import annotations

import copy
import tempfile
import time

import pytest

import athena_ase.context.engine_b_shadow as ebs
from athena_ase.contracts import ASESignal


def _trade_signal(
    instrument: str,
    direction: str,
    decision_ms: int,
    *,
    horizon: str = "intraday",
    status: str = "TRADE",
    expected_net_r: float = 0.14,
    probability_positive: float = 0.61,
    data_quality: dict | None = None,
    model_health: dict | None = None,
) -> ASESignal:
    return ASESignal(
        engineVersion="2.1.0",
        modelFamily="forex",
        modelVersion="v2-integrity",
        horizon=horizon,  # type: ignore[arg-type]
        decisionStatus=status,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        expectedNetR=expected_net_r,
        expectedNetBps=10.0,
        probabilityPositive=probability_positive,
        decisionMargin=0.1,
        signalStrength=50,
        returnQ={}, maeQ={}, mfeQ={}, holdQ={},
        entryReference=1.1, entryZone=(1.09, 1.11),
        sl=1.09, tp1=1.12, tp2=1.13, maxHoldBars=16,
        primarySignals=[], predictionDiagnostics={},
        dataQuality=data_quality or {"coreOk": True, "route": "core", "missingFeeds": []},
        modelHealth=model_health or {"gateResult": {"ok": True}},
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
        "symbol": "EURUSD=X",
        "display": "EUR/USD",
        "type": "forex",
        "direction": direction,
        "style": "intraday",
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
    assert shadow["supportState"] == "OPPOSED"
    assert shadow["supportEligible"] is False
    assert shadow["affectsEngineBScore"] is False
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
    assert card["aseShadow"]["reason"] == "ase_journal_empty"
    assert card["score_pct"] == 63.0


def test_stale_ase_row_is_flat(state_root):
    from athena_ase.execution.journal import append_trade_signals

    stale_ms = int(time.time() * 1000) - 3 * 3_600_000  # > 2× H1 bar
    append_trade_signals([_trade_signal("EURUSD", "LONG", stale_ms)])
    card = _card("LONG")
    ebs.attach_ase_shadow(card)
    assert card["aseShadow"]["alignment"] == "ASE_FLAT"
    assert card["aseShadow"]["reason"] == "ase_signal_stale"


def test_aligned_positive_trade_is_support_eligible(state_root):
    from athena_ase.execution.journal import append_trade_signals

    now_ms = int(time.time() * 1000)
    append_trade_signals([_trade_signal("EURUSD", "LONG", now_ms)])

    shadow = ebs.ase_shadow_for_card(
        "EURUSD=X",
        "LONG",
        style="intraday",
        now_ms=now_ms,
    )

    assert shadow is not None
    assert shadow["supportState"] == "SUPPORTED"
    assert shadow["supportEligible"] is True
    assert shadow["decisionStatus"] == "TRADE"
    assert shadow["horizon"] == "intraday"


def test_flat_decision_is_neutral_even_if_direction_and_metrics_are_present(state_root):
    from athena_ase.execution.journal import append_trade_signals

    now_ms = int(time.time() * 1000)
    append_trade_signals(
        [
            _trade_signal(
                "EURUSD",
                "LONG",
                now_ms,
                status="FLAT",
                expected_net_r=-0.05,
            )
        ]
    )

    shadow = ebs.ase_shadow_for_card(
        "EURUSD=X",
        "LONG",
        style="intraday",
        now_ms=now_ms,
    )

    assert shadow is not None
    assert shadow["direction"] == "NONE"
    assert shadow["alignment"] == "ASE_FLAT"
    assert shadow["supportState"] == "NEUTRAL"
    assert shadow["supportEligible"] is False


def test_fresh_no_candidate_flat_supersedes_an_old_trade(state_root):
    from athena_ase.execution.journal import append_trade_signals

    now_ms = int(time.time() * 1000)
    append_trade_signals(
        [
            _trade_signal("EURUSD", "LONG", now_ms - 3 * 3_600_000),
            _trade_signal(
                "EURUSD",
                "NONE",
                now_ms,
                status="FLAT",
                expected_net_r=0.0,
                probability_positive=0.0,
                data_quality={
                    "coreOk": True,
                    "route": "core",
                    "missingFeeds": [],
                    "blocker": "no_layer1_candidate",
                },
            ),
        ]
    )

    shadow = ebs.ase_shadow_for_card(
        "EURUSD",
        "LONG",
        style="intraday",
        now_ms=now_ms,
    )

    assert shadow is not None
    assert shadow["supportState"] == "NEUTRAL"
    assert shadow["reason"] == "ase_decision_flat"
    assert shadow["decisionStatus"] == "FLAT"


def test_watch_alignment_is_visible_but_not_support_eligible(state_root):
    from athena_ase.execution.journal import append_trade_signals

    now_ms = int(time.time() * 1000)
    append_trade_signals(
        [_trade_signal("EURUSD", "LONG", now_ms, status="WATCH")]
    )

    shadow = ebs.ase_shadow_for_card(
        "EURUSD",
        "LONG",
        style="intraday",
        now_ms=now_ms,
    )

    assert shadow is not None
    assert shadow["supportState"] == "WATCH_ALIGNED"
    assert shadow["alignment"] == "ALIGNED"
    assert shadow["supportEligible"] is False


def test_card_style_selects_matching_ase_horizon(state_root):
    from athena_ase.execution.journal import append_trade_signals

    now_ms = int(time.time() * 1000)
    append_trade_signals(
        [
            _trade_signal("EURUSD", "LONG", now_ms - 1, horizon="intraday"),
            _trade_signal("EURUSD", "SHORT", now_ms, horizon="swing"),
        ]
    )

    intraday = ebs.ase_shadow_for_card(
        "EURUSD=X",
        "LONG",
        style="intraday",
        now_ms=now_ms,
    )
    swing = ebs.ase_shadow_for_card(
        "EURUSD=X",
        "LONG",
        style="swing",
        now_ms=now_ms,
    )

    assert intraday is not None and intraday["supportState"] == "SUPPORTED"
    assert intraday["horizon"] == "intraday"
    assert swing is not None and swing["supportState"] == "OPPOSED"
    assert swing["horizon"] == "swing"


def test_unhealthy_model_cannot_display_support(state_root):
    from athena_ase.execution.journal import append_trade_signals

    now_ms = int(time.time() * 1000)
    append_trade_signals(
        [
            _trade_signal(
                "EURUSD",
                "LONG",
                now_ms,
                model_health={"gateResult": {"ok": False}},
            )
        ]
    )

    shadow = ebs.ase_shadow_for_card(
        "EURUSD",
        "LONG",
        style="intraday",
        now_ms=now_ms,
    )

    assert shadow is not None
    assert shadow["supportState"] == "UNAVAILABLE"
    assert shadow["supportEligible"] is False
    assert shadow["reason"] == "ase_data_or_model_unhealthy"


def test_non_forex_card_gets_no_shadow(state_root):
    card = {"id": "x", "symbol": "BTCUSDT", "type": "crypto", "direction": "LONG"}
    ebs.attach_ase_shadow(card)
    assert "aseShadow" not in card
