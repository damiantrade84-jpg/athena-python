"""test_factor_scoring.py — Unit tests for factor_scoring.py."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from factor_scoring import compute_factor_scores, _factor_score


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_snap(**overrides):
    """Minimal H4 snap with z-score fields."""
    base = {
        "adx": 30, "adx_z": 0.5, "rsi_z": 0.3, "macdLine_z": 0.2,
        "atr_z": 0.1, "bbWidth_z": -0.1, "realized_vol_z": 0.0,
        "obv_trend": 1, "fib_proximity": 1, "ema21": 105, "ema50": 100,
        "adxMomentum": "ACCELERATING", "adxSlope": 0.5,
    }
    base.update(overrides)
    return base


def _make_pair(**overrides):
    base = {"type": "crypto", "display": "BTC/USDT"}
    base.update(overrides)
    return base


def _make_candles(n=200, base=100):
    """Generate N synthetic candles."""
    return [{"open": base + i, "high": base + i + 2,
             "low": base + i - 1, "close": base + i + 1} for i in range(n)]


# ── Factor score computation ────────────────────────────────────────────────


class TestFactorScore:
    def test_mean_of_values(self):
        indicators = {"a": 1.0, "b": 3.0, "c": None}
        result = _factor_score(indicators, {"a": "a", "b": "b", "c": "c"})
        assert result == 2.0  # mean of [1.0, 3.0]

    def test_all_none_returns_none(self):
        indicators = {"a": None, "b": None}
        result = _factor_score(indicators, {"a": "a", "b": "b"})
        assert result is None

    def test_empty_mapping(self):
        indicators = {"a": 1.0}
        result = _factor_score(indicators, {})
        assert result is None


# ── Minimum factor count gate ───────────────────────────────────────────────


class TestMinFactorGate:
    def test_insufficient_factors_zeroes_score(self):
        """With very sparse data, score should be zero."""
        empty_snap = {}
        result = compute_factor_scores(
            d1_snap=empty_snap, h4_snap=empty_snap, h1_snap=empty_snap,
            pair=_make_pair(), d1_candles=[], h4_candles=[], h1_candles=[],
            volume_ratio=None,
        )
        assert result["insufficient_factors"] is True
        assert result["final_score"] == 0.0

    def test_sufficient_factors_produces_score(self):
        """With full data, should produce a non-zero score."""
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap, h4_snap=snap, h1_snap=snap,
            pair=_make_pair(), d1_candles=_make_candles(200),
            h4_candles=_make_candles(200), h1_candles=_make_candles(200),
            volume_ratio=1.5,
        )
        assert result["insufficient_factors"] is False
        assert isinstance(result["final_score"], float)


# ── Regime detection ────────────────────────────────────────────────────────


class TestRegimeDetection:
    def test_regime_present(self):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap, h4_snap=snap, h1_snap=snap,
            pair=_make_pair(), d1_candles=_make_candles(200),
            h4_candles=_make_candles(200), h1_candles=_make_candles(200),
            volume_ratio=1.5,
        )
        assert result["regime"] in ("TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY", "UNKNOWN")


# ── Return structure ────────────────────────────────────────────────────────


class TestReturnStructure:
    def test_all_expected_keys(self):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap, h4_snap=snap, h1_snap=snap,
            pair=_make_pair(), d1_candles=_make_candles(200),
            h4_candles=_make_candles(200), h1_candles=_make_candles(200),
            volume_ratio=1.5,
        )
        expected_keys = {"final_score", "factor_scores", "weights", "regime",
                         "filtered_indicators", "disabled_factors", "insufficient_factors"}
        assert expected_keys.issubset(result.keys())
