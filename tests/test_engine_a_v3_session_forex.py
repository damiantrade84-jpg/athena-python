from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine_a_v3.diagnostics import reverse_direction_result_r, summarize_v3_trades, trade_path_diagnostics
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.levels import build_london_open_breakout_levels
from engine_a_v3.promotion import demo_unvalidated_registry
from engine_a_v3.session_forex import (
    asian_session_range,
    in_london_open_window,
    session_quality_score,
    volume_spike_ratio,
)
from engine_a_v3.session_scoring import session_context_score, session_score_passes
from engine_a_v3.setups import detect_setup
from engine_a_v3.routing import route_specialist


def _h1_bar(when: datetime, *, open_: float, high: float, low: float, close: float, vol: float) -> dict:
    return {
        "time": when.isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "vol": vol,
    }


def _london_open_candles() -> dict[str, list[dict]]:
    day = datetime(2025, 6, 2, tzinfo=timezone.utc)
    h1: list[dict] = []
    # Asian range 1.0800 - 1.0820
    for hour in range(0, 7):
        ts = day.replace(hour=hour)
        h1.append(_h1_bar(ts, open_=1.0810, high=1.0820, low=1.0800, close=1.0810, vol=1000))
    # London open breakout long at 07:00 UTC with volume spike
    h1.append(_h1_bar(day.replace(hour=7), open_=1.0820, high=1.0845, low=1.0818, close=1.0840, vol=2500))
    # Warmup history
    start = day - timedelta(days=30)
    warmup = []
    for idx in range(30 * 24):
        ts = start + timedelta(hours=idx)
        warmup.append(_h1_bar(ts, open_=1.0700, high=1.0710, low=1.0690, close=1.0705, vol=900))
    h1 = warmup + h1
    d1 = [{"time": (day - timedelta(days=idx)).isoformat(), "open": 1.07, "high": 1.08, "low": 1.06, "close": 1.075, "vol": 1} for idx in range(260, 0, -1)]
    h4 = [{"time": (day - timedelta(hours=idx * 4)).isoformat(), "open": 1.07, "high": 1.08, "low": 1.06, "close": 1.075, "vol": 1} for idx in range(300, 0, -1)]
    return {"D1": d1, "H4": h4, "H1": h1}


def test_session_quality_overlap_scores_highest():
    score, label = session_quality_score(datetime(2025, 6, 2, 14, tzinfo=timezone.utc))
    assert score == 1.0
    assert label == "london_ny_overlap"


def test_asian_session_range_extracts_high_low():
    candles = _london_open_candles()["H1"]
    as_of = datetime(2025, 6, 2, 7, tzinfo=timezone.utc)
    asian = asian_session_range(candles, as_of=as_of)
    assert asian is not None
    assert asian.high == pytest.approx(1.0820)
    assert asian.low == pytest.approx(1.0800)


def test_london_open_window():
    assert in_london_open_window(datetime(2025, 6, 2, 7, 30, tzinfo=timezone.utc))
    assert not in_london_open_window(datetime(2025, 6, 2, 9, tzinfo=timezone.utc))


def test_volume_spike_ratio_detects_spike():
    candles = _london_open_candles()["H1"]
    assert volume_spike_ratio(candles) >= 1.5


def test_london_open_setup_detects_long_breakout(monkeypatch):
    import config

    monkeypatch.setitem(config.CONFIG, "ENGINE_A_V3_FOREX_SETUP", "london_open")
    monkeypatch.setitem(config.CONFIG, "ENGINE_A_V3_FOREX_THESIS", "trend")
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    route = route_specialist(pair)
    candidate = detect_setup(route, "intraday", _london_open_candles())
    assert candidate.setup_id == "fx_london_open_breakout"
    assert candidate.decision == "TRADE"
    assert candidate.direction == "LONG"
    assert candidate.level_style == "london_open"


def test_london_open_levels_use_asian_range():
    candles = _london_open_candles()["H1"]
    levels = build_london_open_breakout_levels(candles, direction="LONG")
    assert levels is not None
    assert levels.invalidation < levels.price
    assert levels.targets[0].price > levels.price


def test_session_context_score_returns_components():
    candles = _london_open_candles()["H1"]
    score, components = session_context_score(candles, direction="LONG")
    assert score > 0
    assert components["session_quality"] >= 0.75


def test_diagnostics_reverse_and_mfe_summary():
    future = [
        {"high": 110, "low": 95, "close": 108},
        {"high": 112, "low": 99, "close": 111},
    ]
    path = trade_path_diagnostics(future, direction="LONG", entry=100, sl=95, tp1=105, tp2=110)
    assert path["max_favorable_excursion_r"] >= 1.0
    reverse_r = reverse_direction_result_r(
        future, direction="LONG", entry=100, sl=95, tp=110, cost_r=0.0
    )
    trades = [
        {
            "resultR": -1.0,
            "reverseResultR": reverse_r,
            "outcome": "SL",
            "max_favorable_excursion_r": path["max_favorable_excursion_r"],
            "regime": "trending",
        }
    ]
    summary = summarize_v3_trades(trades)
    assert summary["n"] == 1
    assert summary["reverse_gross_total_r"] is not None
    assert "trade_regime" in summary


def test_evaluator_london_open_trade_signal(monkeypatch):
    import config

    monkeypatch.setitem(config.CONFIG, "ENGINE_A_V3_FOREX_SETUP", "london_open")
    monkeypatch.setitem(config.CONFIG, "ENGINE_A_V3_FOREX_THESIS", "trend")
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    with tempfile.TemporaryDirectory() as tmp:
        registry = demo_unvalidated_registry(Path(tmp))
        signal = evaluate_engine_a_v3(
            pair,
            _london_open_candles(),
            horizon="intraday",
            registry=registry,
        )
    assert signal.setupId == "fx_london_open_breakout"
    assert signal.decision == "TRADE"
    assert signal.sl is not None
    assert signal.tp1 is not None


def test_session_scoring_gate_rejects_when_enabled(monkeypatch):
    import config

    monkeypatch.setitem(config.CONFIG, "ENGINE_A_V3_FOREX_SETUP", "london_open")
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_A_V3_SESSION_SCORING",
        {"ENABLED": True, "MIN_SCORE": 999.0},
    )
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    with tempfile.TemporaryDirectory() as tmp:
        registry = demo_unvalidated_registry(Path(tmp))
        signal = evaluate_engine_a_v3(
            pair,
            _london_open_candles(),
            horizon="intraday",
            registry=registry,
        )
    assert signal.decision == "NO_SIGNAL"
    assert "session_context_score_below_min" in signal.rejectionReasons


def test_session_score_passes_with_low_threshold():
    candles = _london_open_candles()["H1"]
    assert session_score_passes(candles, direction="LONG", min_score=0.01)
