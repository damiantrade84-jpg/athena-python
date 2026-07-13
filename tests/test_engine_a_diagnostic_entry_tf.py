"""Diagnostic Engine A V3 entry-TF override (H1/M15/M30) — fail-closed."""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from athena_app.services.entry_tf_improvement import (
    pair_improved_overall,
    side_improved,
)
from engine_a_v3.quant_scorer import score_pair
from engine_a_v3.routing import route_specialist
from engine_a_v3.timeframes import (
    DIAGNOSTIC_V3_ENTRY_TIMEFRAMES,
    VALID_V3_ENTRY_TIMEFRAMES,
    engine_a_diagnostic_entry_kwargs,
    resolve_diagnostic_v3_entry_timeframe,
    resolve_live_v3_entry_timeframe,
    resolve_v3_entry_timeframe,
)


def _bar(close: float, i: int, *, tf_seconds: int = 3600) -> dict:
    ts = datetime(2024, 6, 3, 12, 0, tzinfo=timezone.utc) - timedelta(
        seconds=tf_seconds * (80 - i)
    )
    return {
        "time": ts.isoformat(),
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "vol": 1000.0 + i,
        "volume": 1000.0 + i,
    }


def _series(close: float, n: int = 80, *, tf_seconds: int = 3600) -> list[dict]:
    return [_bar(close, i, tf_seconds=tf_seconds) for i in range(n)]


def _candles(
    *,
    h1_close: float = 100.0,
    m15_close: float = 15.0,
    m30_close: float = 30.0,
    include_m15: bool = True,
    include_m30: bool = True,
) -> dict[str, list]:
    out = {
        "D1": _series(110.0, n=240, tf_seconds=86400),
        "H4": _series(105.0, n=80, tf_seconds=14400),
        "H1": _series(h1_close, n=80, tf_seconds=3600),
    }
    if include_m15:
        out["M15"] = _series(m15_close, n=80, tf_seconds=900)
    if include_m30:
        out["M30"] = _series(m30_close, n=80, tf_seconds=1800)
    return out


def test_production_valid_set_excludes_m15_m30():
    assert "M15" not in VALID_V3_ENTRY_TIMEFRAMES
    assert "M30" not in VALID_V3_ENTRY_TIMEFRAMES
    assert DIAGNOSTIC_V3_ENTRY_TIMEFRAMES == frozenset({"H1", "M15", "M30"})
    assert resolve_v3_entry_timeframe("forex_majors", "forex", "intraday") == "H1"
    assert resolve_diagnostic_v3_entry_timeframe("M15") == "M15"
    assert resolve_diagnostic_v3_entry_timeframe("H4") is None


def test_diagnostic_entry_kwargs_default_off(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_A_DIAGNOSTIC_ENTRY_TF_ENABLED", False)
    assert engine_a_diagnostic_entry_kwargs("M15") == {}


def test_diagnostic_entry_kwargs_enabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_A_DIAGNOSTIC_ENTRY_TF_ENABLED", True)
    assert engine_a_diagnostic_entry_kwargs("M15") == {"entry_tf_override": "M15"}
    assert engine_a_diagnostic_entry_kwargs("H4") == {}


def test_live_forex_intraday_entry_is_m30_only_on_mt5():
    assert resolve_live_v3_entry_timeframe(
        "forex", "intraday", source="mt5"
    ) == "M30"
    assert resolve_live_v3_entry_timeframe(
        "forex", "intraday", source="eodhd"
    ) is None
    assert resolve_live_v3_entry_timeframe("forex", "swing", source="mt5") is None


def test_score_pair_m15_override_uses_m15_series_not_h1():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    route = route_specialist(pair)
    candles = _candles(h1_close=100.0, m15_close=15.0, m30_close=30.0)

    baseline = score_pair(route, "intraday", candles)
    assert baseline.factor_diagnostics.get("entryTimeframe") == "H1"
    assert baseline.factor_diagnostics.get("entryLastClose") == pytest.approx(100.0)

    m15 = score_pair(route, "intraday", candles, entry_tf_override="M15")
    assert m15.factor_diagnostics.get("entryTimeframe") == "M15"
    assert m15.factor_diagnostics.get("entryTfOverride") == "M15"
    assert m15.factor_diagnostics.get("entryLastClose") == pytest.approx(15.0)
    assert m15.factor_diagnostics.get("entryBarCount") == 80

    m30 = score_pair(route, "intraday", candles, entry_tf_override="M30")
    assert m30.factor_diagnostics.get("entryTimeframe") == "M30"
    assert m30.factor_diagnostics.get("entryLastClose") == pytest.approx(30.0)


def test_score_pair_missing_m15_fails_closed_no_h1_fallback():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    route = route_specialist(pair)
    candles = _candles(include_m15=False)
    out = score_pair(route, "intraday", candles, entry_tf_override="M15")
    assert out.direction == "FLAT"
    assert out.factor_diagnostics.get("entryTimeframe") == "M15"
    assert out.factor_diagnostics.get("rejectionReason") == "missing_entry_candles"
    # Must not silently report H1 last close.
    assert out.factor_diagnostics.get("entryLastClose") is None


def test_score_pair_empty_m15_fails_closed():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    route = route_specialist(pair)
    candles = _candles()
    candles["M15"] = []
    out = score_pair(route, "intraday", candles, entry_tf_override="M15")
    assert out.factor_diagnostics.get("rejectionReason") == "missing_entry_candles"


def test_invalid_diagnostic_override_rejected():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    route = route_specialist(pair)
    candles = _candles()
    out = score_pair(route, "intraday", candles, entry_tf_override="M5")
    assert out.factor_diagnostics.get("rejectionReason") == "invalid_entry_timeframe"


def test_evaluate_stamps_entry_timeframe_override():
    from engine_a_v3 import evaluate_engine_a_v3

    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    candles = _candles(h1_close=100.0, m15_close=15.0)
    signal = evaluate_engine_a_v3(
        pair, candles, horizon="intraday", entry_tf_override="M15"
    )
    assert signal.entryTimeframe == "M15"
    diag = signal.factorDiagnostics or {}
    assert diag.get("entryTimeframe") == "M15"
    assert diag.get("entryLastClose") == pytest.approx(15.0)


def test_evaluate_active_entry_keeps_previous_bar_as_last_confirmed():
    from engine_a_v3 import evaluate_engine_a_v3

    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    candles = _candles()
    signal = evaluate_engine_a_v3(
        pair,
        candles,
        horizon="intraday",
        entry_tf_override="M30",
        entry_candle_is_forming=True,
    )
    assert signal.decisionTime == candles["M30"][-1]["time"]
    assert signal.lastConfirmedCandleTs == candles["M30"][-2]["time"]
    assert signal.dataFreshness.policy == "active_entry_confirmed_structure"
    assert signal.factorDiagnostics["entryUsesActiveCandle"] is True


def test_lower_entry_gate_requires_active_candle_for_execution_eligibility():
    from engine_a_v3 import evaluate_engine_a_v3

    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    signal = evaluate_engine_a_v3(
        pair,
        _candles(),
        horizon="intraday",
        entry_tf_override="M30",
        entry_candle_is_forming=False,
    )

    gate = signal.factorDiagnostics["activeEntryGate"]
    assert gate == {"required": True, "passed": False, "timeframe": "M30"}
    assert signal.dataFreshness.allowed is False
    assert signal.dataFreshness.reason == "active_entry_candle_required"


def test_engine_a_atr_provenance_uses_active_m30_entry_series():
    from atr_diagnostics import attach_engine_a_v3_atr_provenance

    candles = _candles()
    signal = {}
    attach_engine_a_v3_atr_provenance(
        signal,
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        "intraday",
        candles["D1"],
        candles["H4"],
        candles["H1"],
        entry_tf="M30",
        entry_candles=candles["M30"],
        config={},
    )
    assert signal["atrDiagnostics"]["atr_tf"] == "M30"
    assert signal["atrDiagnostics"]["atr_confirmed_only"] is False
    assert signal["atrDiagnostics"]["atr_candle_last_ts"] == candles["M30"][-1]["time"]


def test_side_improved_score_only_upgrade():
    assert side_improved(score_delta=0.06, gate_upgraded=False) is True
    assert side_improved(score_delta=0.04, gate_upgraded=False) is False


def test_side_improved_gate_upgrade_requires_non_negative_delta():
    assert side_improved(score_delta=0.0, gate_upgraded=True) is True
    assert side_improved(score_delta=0.01, gate_upgraded=True) is True
    assert side_improved(score_delta=-0.01, gate_upgraded=True) is False


def test_pair_improved_overall_requires_non_negative_delta_sum():
    # One side improves a lot, other regresses more → not overall improved.
    roll = pair_improved_overall(
        [
            {"score_delta": 0.1, "gate_upgraded": False},
            {"score_delta": -0.2, "gate_upgraded": False},
        ]
    )
    assert roll["any_side_improved"] is True
    assert roll["overall_improved"] is False

    roll_ok = pair_improved_overall(
        [
            {"score_delta": 0.1, "gate_upgraded": False},
            {"score_delta": -0.02, "gate_upgraded": False},
        ]
    )
    assert roll_ok["overall_improved"] is True
    assert math.isclose(roll_ok["score_delta_sum"], 0.08)
