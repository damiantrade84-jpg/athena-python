"""Phase 1.4–1.7: intermarket re-tier, direction conflict, classify parity, fail-closed gates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from athena_app.services.engine_a_v3_classify import (
    classify_engine_a_v3_signal,
    retier_v3_after_score_adjust,
)
from config import CONFIG
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.setups import SetupCandidate
import engine_a_v3.evaluator as evaluator_module
from scoring import _classify_signal as scoring_classify
from scanner import _classify_signal as scanner_classify


def _v3_trade_signal(**overrides):
    base = {
        "engine": "ENGINE_A_V3",
        "contractVersion": "3.1.0",
        "decision": "TRADE",
        "qualified": True,
        "engineATradeEnabled": True,
        "direction": "LONG",
        "confluenceScore": 2.2,
        "confluenceThreshold": 2.1,
        "maxScore": 3.0,
        "scoreNorm": 0.73,
        "rejectionReasons": [],
    }
    base.update(overrides)
    return base


def test_intermarket_negative_delta_demotes_v3_trade():
    signal = _v3_trade_signal(confluenceScore=2.2, confluenceThreshold=2.1)
    signal["confluenceScore"] = 1.9  # post-intermarket adjust below threshold
    signal["score"] = 1.9
    retier_v3_after_score_adjust(signal)
    assert signal["decision"] == "WATCH"
    assert signal["qualified"] is False
    assert "intermarket_score_below_trade_threshold" in signal["rejectionReasons"]


def test_intermarket_retier_never_upgrades_watch():
    signal = _v3_trade_signal(decision="WATCH", qualified=False, confluenceScore=2.5)
    retier_v3_after_score_adjust(signal)
    assert signal["decision"] == "WATCH"


def test_direction_conflict_demotes_via_classify(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", False)
    signal = _v3_trade_signal(directionConflicted=True)
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}
    tier, reason = classify_engine_a_v3_signal(signal, pair)
    assert tier == "watchlist"
    assert "conflict" in reason.lower() or "direction" in reason.lower()


def test_pending_entry_readiness_demotes_v3_trade_to_watchlist(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", False)
    monkeypatch.setitem(CONFIG, "ENGINE_A_ENTRY_TIMING_GATES_ENABLED", True)
    signal = _v3_trade_signal(
        entryReadiness="PENDING",
        entryReadinessReason="confirmed trigger condition not met",
        triggerConfirmed=False,
    )
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}

    tier, reason = classify_engine_a_v3_signal(signal, pair)

    assert tier == "watchlist"
    assert reason == "Engine A entry pending: confirmed trigger condition not met"


def test_pending_entry_readiness_ignored_when_timing_gates_disabled(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", False)
    monkeypatch.setitem(CONFIG, "ENGINE_A_ENTRY_TIMING_GATES_ENABLED", False)
    signal = _v3_trade_signal(
        entryReadiness="PENDING",
        entryReadinessReason="confirmed trigger condition not met",
        triggerConfirmed=False,
    )
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}

    tier, reason = classify_engine_a_v3_signal(signal, pair)

    assert tier == "trade"
    assert "pending" not in reason.lower()


@pytest.mark.parametrize(
    ("risk_field", "risk_payload", "expected_reason"),
    [
        (
            "eventRisk",
            {"hardBlock": True, "reason": "earnings_within_one_day"},
            "earnings_within_one_day",
        ),
        (
            "macroEventRisk",
            {"blocked": True, "reason": "high_impact_macro"},
            "high_impact_macro",
        ),
    ],
)
def test_v3_hard_risk_annotations_demote_trade(
    monkeypatch, risk_field, risk_payload, expected_reason
):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", False)
    signal = _v3_trade_signal(**{risk_field: risk_payload})
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}

    tier, reason = classify_engine_a_v3_signal(signal, pair)

    assert tier == "watchlist"
    assert expected_reason in reason


def test_scanner_v3_min_confidence_matches_scoring(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE", 0.60)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE",
        {**(CONFIG.get("ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE") or {}), "forex_majors": 0.60},
    )
    signal = _v3_trade_signal(scoreNorm=0.40, confidence=0.40)
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "score_group": "forex_majors",
        "enabled": True,
    }
    scoring_tier, scoring_reason = scoring_classify(signal, pair)
    scanner_tier, scanner_reason = scanner_classify(signal, pair)
    assert scoring_tier == "watchlist"
    assert scanner_tier == "watchlist"
    assert "confidence" in scoring_reason.lower()
    assert "confidence" in scanner_reason.lower()


def test_v3_score_norm_is_not_reused_as_confidence_for_scoring_or_scanner(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE", 0.60)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE",
        {
            **(CONFIG.get("ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE") or {}),
            "us_indices_trackers": 0.54,
        },
    )
    signal = _v3_trade_signal(
        confluenceScore=1.50,
        confluenceThreshold=1.50,
        maxScore=3.0,
        scoreNorm=0.50,
    )
    pair = {
        "display": "S&P 500",
        "symbol": "US500",
        "type": "index",
        "score_group": "us_indices_trackers",
        "enabled": True,
    }

    assert scoring_classify(signal, pair)[0] == "trade"
    assert scanner_classify(signal, pair)[0] == "trade"


@pytest.mark.parametrize("confidence", ["not-a-number", float("nan"), float("inf")])
def test_v3_malformed_independent_confidence_fails_closed(monkeypatch, confidence):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", True)
    signal = _v3_trade_signal(confidence=confidence)
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "score_group": "forex_majors",
        "enabled": True,
    }

    scoring_tier, scoring_reason = scoring_classify(signal, pair)
    scanner_tier, scanner_reason = scanner_classify(signal, pair)

    assert scoring_tier == "watchlist"
    assert scanner_tier == "watchlist"
    assert "confidence invalid" in scoring_reason.lower()
    assert "confidence invalid" in scanner_reason.lower()


def test_scanner_and_scoring_agree_on_trade(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", False)
    signal = _v3_trade_signal()
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True, "score_group": "forex_majors"}
    assert scoring_classify(signal, pair)[0] == "trade"
    assert scanner_classify(signal, pair)[0] == "trade"


def _candles() -> dict[str, list[dict]]:
    start = datetime(2025, 6, 16, 10, 0, tzinfo=timezone.utc)
    rows = []
    for idx in range(260):
        close = 100 + 0.05 * idx
        rows.append(
            {
                "time": (start + timedelta(hours=1) * idx).isoformat(),
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "vol": 1000,
            }
        )
    return {"D1": rows, "H4": rows, "H1": rows}


def test_session_gate_exception_blocks_setup_upgrade(monkeypatch):
    from engine_a_v3.quant_scorer import Component, QuantScore

    def _fake_score_pair(*_args, **_kwargs):
        # Above setup-upgrade quality floor (0.5 * 2.1) but still WATCH so
        # only the session gate can block the upgrade path.
        return QuantScore(
            direction="LONG",
            confluence_score=1.2,
            max_score=3.0,
            score_norm=0.4,
            conviction=0.4,
            decision="WATCH",
            threshold=2.1,
            level_style="trend",
            factor_scores={},
            factor_diagnostics={},
            components={
                "trend": Component(signal=1.0, quality=0.5, available=True),
                "momentum": Component(signal=1.0, quality=0.5, available=True),
                "location": Component(signal=1.0, quality=0.5, available=True),
                "volume": Component(signal=0.0, quality=0.0, available=False),
            },
        )

    def _fake_detect_setup(route, horizon, candles, *, display=None, indicator_periods=None):
        return SetupCandidate("fx_trend_pullback", "TRADE", "LONG", (), (), "trend")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("session_feed_down")

    monkeypatch.setattr(evaluator_module, "score_pair", _fake_score_pair)
    monkeypatch.setattr(evaluator_module, "detect_setup", _fake_detect_setup)
    monkeypatch.setattr(evaluator_module, "session_score_passes", _boom)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_SESSION_SCORING",
        {"ENABLED": True, "MIN_SCORE": 0.35},
    )
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_SETUP_UPGRADE",
        {"ENABLED": True, "MIN_CONFLUENCE_FRAC": 0.5},
    )

    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    signal = evaluate_engine_a_v3(pair, _candles(), horizon="intraday")
    assert signal.decision == "WATCH"
    assert "session_gate_error_fail_closed" in signal.rejectionReasons
    overlay = (signal.factorDiagnostics or {}).get("setupOverlay") or {}
    assert overlay.get("sessionGateBlocked") is True
    assert overlay.get("sessionGateError") == "RuntimeError"


def test_apply_direction_conflict_on_v3_result(monkeypatch):
    from athena_app.services.engine_a_direction import apply_direction_conflict_to_result

    monkeypatch.setitem(
        CONFIG, "ENGINE_A_DIRECTION_INTERMARKET_CONFLICT_GUARD_ENABLED", True
    )
    result = apply_direction_conflict_to_result(
        {
            "engine": "ENGINE_A_V3",
            "direction": "LONG",
            "decision": "TRADE",
            "qualified": True,
            "signalExecutable": True,
        },
        intermarket_confirmation={"verdict": "contradictory", "score": -0.3},
    )
    assert result["directionConflicted"] is True
    assert result["signalExecutable"] is False
