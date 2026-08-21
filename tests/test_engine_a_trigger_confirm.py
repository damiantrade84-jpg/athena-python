"""Engine A blocking trigger confirmation (Engine B parity).

An OPPOSED policy-trigger evidence demotes TRADE -> WATCH. Missing or
unavailable evidence stays advisory. Reversible via
ENGINE_A_V3_TRIGGER_CONFIRM_REQUIRED=false.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine_a_v3.evaluator import _evaluate_trigger_confirmation


def _evidence(signal: float, quality: float = 0.7) -> dict:
    return {"timeframe": "M15", "available": True, "signal": signal, "quality": quality}


def test_opposed_trigger_fails_with_opposed_reason():
    confirmed, diag = _evaluate_trigger_confirmation(
        {"triggerEvidence": _evidence(-0.6)}, "LONG", "M15"
    )
    assert confirmed is False
    assert diag["reason"] == "trigger_direction_opposed"


def test_aligned_trigger_passes():
    confirmed, diag = _evaluate_trigger_confirmation(
        {"triggerEvidence": _evidence(0.6)}, "LONG", "M15"
    )
    assert confirmed is True
    assert diag["entryPath"] == "m15_aligned"


def test_missing_evidence_is_advisory_not_opposed():
    confirmed, diag = _evaluate_trigger_confirmation({}, "LONG", "M15")
    assert confirmed is False
    assert diag["reason"] == "trigger_evidence_missing"
    # The blocking gate keys on the opposed reason only.
    assert diag["reason"] != "trigger_direction_opposed"


def test_unavailable_evidence_is_advisory_not_opposed():
    evidence = {"timeframe": "M15", "available": False, "signal": -0.6, "quality": 0.7}
    confirmed, diag = _evaluate_trigger_confirmation(
        {"triggerEvidence": evidence}, "SHORT", "M15"
    )
    assert confirmed is False
    assert diag["reason"] == "trigger_evidence_unavailable"


def test_m5_pullback_rescue_overrides_weak_opposition():
    diagnostics = {
        "triggerEvidence": _evidence(-0.2, quality=0.3),
        "m5Policy": "conditional",
        "m5RefinementEvidence": {"available": True, "signal": 0.5, "quality": 0.6},
        "components": {"trend": {"signal": 0.4}},
    }
    confirmed, diag = _evaluate_trigger_confirmation(diagnostics, "LONG", "M15")
    assert confirmed is True
    assert diag["entryPath"] == "m5_pullback_confirm"


def test_strong_m15_opposition_blocks_m5_rescue():
    diagnostics = {
        "triggerEvidence": _evidence(-0.5, quality=0.6),
        "m5Policy": "conditional",
        "m5RefinementEvidence": {"available": True, "signal": 0.5, "quality": 0.6},
        "components": {"trend": {"signal": 0.4}},
    }
    confirmed, diag = _evaluate_trigger_confirmation(diagnostics, "LONG", "M15")
    assert confirmed is False
    assert diag["reason"] == "trigger_direction_opposed"


# ── evaluator integration ───────────────────────────────────────────────────

def _history_bars(*, count: int, tf_hours: float, base: float = 100.0, step: float = 0.05):
    start = datetime(2025, 3, 1, 0, 0, tzinfo=timezone.utc)
    rows = []
    for idx in range(count):
        close = base + step * idx
        rows.append(
            {
                "time": (start + timedelta(hours=tf_hours) * idx).isoformat(),
                "open": close - step / 2,
                "high": close + step,
                "low": close - step,
                "close": close,
                "vol": 1000 + idx,
            }
        )
    return rows


def _valid_candles() -> dict[str, list[dict]]:
    # Role minimums mirror the scorer's normalized-indicator lookback
    # (_minimum_normalized_indicator_bars): keep every role TF comfortably
    # above the largest configured period (ema_long).
    return {
        "D1": _history_bars(count=460, tf_hours=24.0),
        "H4": _history_bars(count=460, tf_hours=4.0),
        "H1": _history_bars(count=460, tf_hours=1.0),
        "M15": _history_bars(count=460, tf_hours=0.25),
    }


class _StubQuant:
    """Minimal QuantScore stand-in with a fixed TRADE decision."""

    def __init__(self, factor_diagnostics: dict):
        from engine_a_v3.quant_scorer import Component

        self.direction = "LONG"
        self.confluence_score = 2.0
        self.max_score = 3.0
        self.score_norm = 2.0 / 3.0
        self.conviction = 2.0 / 3.0
        self.decision = "TRADE"
        self.threshold = 1.35
        self.level_style = "trend"
        self.factor_scores = {}
        self.factor_diagnostics = factor_diagnostics
        self.components = {
            name: Component(signal=0.5, quality=0.8)
            for name in ("trend", "momentum", "location", "volume")
        }


def _evaluate_with_stub(monkeypatch, factor_diagnostics: dict, *, required: bool):
    import engine_a_v3.evaluator as evaluator_module
    from config import CONFIG

    monkeypatch.setattr(
        evaluator_module,
        "score_pair",
        lambda *args, **kwargs: _StubQuant(factor_diagnostics),
    )
    monkeypatch.setitem(
        CONFIG, "ENGINE_A_V3_TRIGGER_CONFIRM_REQUIRED", required
    )
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    policy = {"setup": "H1", "setupTf": "H1", "trigger": "M15", "structure": "H4"}
    signal = evaluator_module.evaluate_engine_a_v3(
        pair,
        _valid_candles(),
        horizon="intraday",
        policy_timeframes=policy,
    )
    return signal.to_dict()


def test_opposed_trigger_demotes_trade_to_watch(monkeypatch):
    payload = _evaluate_with_stub(
        monkeypatch, {"triggerEvidence": _evidence(-0.6)}, required=True
    )
    assert payload["decision"] == "WATCH"
    assert "trigger_direction_opposed" in payload["rejectionReasons"]
    assert payload["factorDiagnostics"]["setupOverlay"]["triggerConfirmBlocked"] is True


def test_gate_disabled_keeps_trade(monkeypatch):
    payload = _evaluate_with_stub(
        monkeypatch, {"triggerEvidence": _evidence(-0.6)}, required=False
    )
    assert payload["decision"] == "TRADE"


def test_missing_evidence_does_not_block(monkeypatch):
    payload = _evaluate_with_stub(monkeypatch, {}, required=True)
    assert payload["decision"] == "TRADE"
    assert "trigger_direction_opposed" not in payload["rejectionReasons"]


def test_aligned_evidence_keeps_trade(monkeypatch):
    payload = _evaluate_with_stub(
        monkeypatch, {"triggerEvidence": _evidence(0.6)}, required=True
    )
    assert payload["decision"] == "TRADE"
