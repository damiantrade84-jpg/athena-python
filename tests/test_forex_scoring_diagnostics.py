import os
import sys
from datetime import datetime, timedelta, timezone

import indicators

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import forex_scoring


def _make_h1_candles(count: int = 80, base: float = 1.1000, step: float = 0.0002) -> list[dict]:
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    bars = []
    for idx in range(count):
        px = base + (idx * step)
        bars.append(
            {
                "time": (start + timedelta(hours=idx)).isoformat(),
                "open": px,
                "high": px + 0.0005,
                "low": px - 0.0005,
                "close": px,
                "vol": 1000.0,
            }
        )
    return bars


def _make_h4_candles(count: int = 80, base: float = 1.1000, step: float = 0.0005) -> list[dict]:
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    bars = []
    for idx in range(count):
        px = base + (idx * step)
        bars.append(
            {
                "time": (start + timedelta(hours=4 * idx)).isoformat(),
                "open": px,
                "high": px + 0.0010,
                "low": px - 0.0010,
                "close": px,
                "vol": 1000.0,
            }
        )
    return bars


def _base_inputs() -> tuple[dict, dict, dict, list[dict], list[dict], dict]:
    d1_snap = {
        "adx": 30.0,
        "close": 1.1200,
        "ema200": 1.1000,
        "ema200Slope10": 0.01,
    }
    h4_snap = {
        "ema50": 1.1150,
        "ema200": 1.1050,
        "adx": 30.0,
        "atr": 0.0050,
        "close": 1.1180,
        "macdHist": 0.10,
        "macdHistPrev": 0.05,
    }
    h1_snap = {
        "rsi": 40.0,
        "ema21": 1.1170,
        "ema50": 1.1120,
        "close": 1.1190,
    }
    h1_candles = _make_h1_candles()
    h4_candles = _make_h4_candles()
    pair = {"display": "EUR/USD", "type": "forex"}
    return d1_snap, h4_snap, h1_snap, h1_candles, h4_candles, pair


def _patch_nonessential_inputs(monkeypatch) -> None:
    monkeypatch.setattr(forex_scoring, "_in_session", lambda _hour, _pair="": True)
    monkeypatch.setattr(forex_scoring, "_local_to_utc_hour", lambda: 20)
    monkeypatch.setattr(forex_scoring, "_cot_boost", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(forex_scoring, "_carry_tilt", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(indicators, "detect_fvg", lambda _candles: [])
    monkeypatch.setattr(indicators, "detect_liquidity_sweep", lambda _candles, _atr: False)
    monkeypatch.setattr(indicators, "volume_strength_at_level", lambda _candles, _level: 0.0)
    monkeypatch.setattr(indicators, "calc_fib", lambda _candles: {"fib618": 1.0, "fib382": 1.0})


def test_forex_zero_score_reports_adx_gate_reason(monkeypatch):
    _patch_nonessential_inputs(monkeypatch)
    monkeypatch.setattr(forex_scoring, "_hurst_exponent", lambda *_args, **_kwargs: 0.60)

    d1_snap, h4_snap, h1_snap, h1_candles, h4_candles, pair = _base_inputs()
    d1_snap["adx"] = 18.0

    result = forex_scoring.compute_forex_score(
        d1_snap=d1_snap,
        h4_snap=h4_snap,
        h1_snap=h1_snap,
        h1_candles=h1_candles,
        pair=pair,
        h4_candles=h4_candles,
    )

    assert result.final_score == 0.0
    assert result.components["trend_gate"] is False
    assert result.components["trend_gate_reason"] == "adx_below_min"
    assert "trend_gate_adx_below_min" in result.components["zero_score_reasons"]
    assert "trend_path_inactive" in result.components["zero_score_reasons"]
    assert "breakout_inactive" in result.components["zero_score_reasons"]


def test_forex_zero_score_reports_hurst_veto_reason(monkeypatch):
    _patch_nonessential_inputs(monkeypatch)
    monkeypatch.setattr(forex_scoring, "_hurst_exponent", lambda *_args, **_kwargs: 0.40)

    d1_snap, h4_snap, h1_snap, h1_candles, h4_candles, pair = _base_inputs()

    result = forex_scoring.compute_forex_score(
        d1_snap=d1_snap,
        h4_snap=h4_snap,
        h1_snap=h1_snap,
        h1_candles=h1_candles,
        pair=pair,
        h4_candles=h4_candles,
    )

    assert result.final_score == 0.0
    assert result.components["trend_gate_raw"] is True
    assert result.components["trend_gate"] is False
    assert result.components["trend_gate_reason_raw"] == "passed_long"
    assert result.components["trend_gate_reason"] == "hurst_veto_trend"
    assert "hurst_veto_trend" in result.components["zero_score_reasons"]
    assert "trend_path_inactive" in result.components["zero_score_reasons"]
    assert "breakout_inactive" in result.components["zero_score_reasons"]


def test_forex_positive_score_has_no_zero_reason_codes(monkeypatch):
    _patch_nonessential_inputs(monkeypatch)
    monkeypatch.setattr(forex_scoring, "_hurst_exponent", lambda *_args, **_kwargs: 0.60)

    d1_snap, h4_snap, h1_snap, h1_candles, h4_candles, pair = _base_inputs()

    result = forex_scoring.compute_forex_score(
        d1_snap=d1_snap,
        h4_snap=h4_snap,
        h1_snap=h1_snap,
        h1_candles=h1_candles,
        pair=pair,
        h4_candles=h4_candles,
    )

    assert result.final_score > 0.0
    assert result.components["trend_gate"] is True
    assert result.components["trend_gate_reason"] == "passed_long"
    assert result.components["zero_score_reasons"] == []
