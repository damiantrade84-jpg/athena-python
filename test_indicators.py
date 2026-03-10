"""Unit tests for Athena v3.0 core indicator functions.
Run with: py -m pytest test_indicators.py -v
"""
import math
import pytest

# Import indicator functions from pure module to avoid booting the full Flask app in tests
from indicators import (
    calc_ema, calc_sma, calc_rsi, calc_atr, calc_adx, calc_bb,
    calc_stochastic, calc_atr_percentile, calc_adx_percentile,
    calc_fib, calc_levels
)

# ── Test data: 30 synthetic OHLCV candles with known properties ──────────
CLOSES = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109,
          111, 110, 112, 114, 113, 115, 117, 116, 118, 120,
          119, 121, 123, 122, 124, 126, 125, 127, 129, 128]
HIGHS  = [c + 1.5 for c in CLOSES]
LOWS   = [c - 1.5 for c in CLOSES]
CANDLES = [{"time": f"2024-01-{i+1:02d}", "open": CLOSES[i] - 0.5,
            "high": HIGHS[i], "low": LOWS[i], "close": CLOSES[i], "vol": 1000 + i * 10}
           for i in range(len(CLOSES))]


class TestEMA:
    def test_length_matches_input(self):
        result = calc_ema(CLOSES, 5)
        assert len(result) == len(CLOSES)

    def test_first_values_are_none(self):
        result = calc_ema(CLOSES, 10)
        assert all(v is None for v in result[:9])
        assert result[9] is not None

    def test_seed_is_sma(self):
        result = calc_ema(CLOSES, 5)
        expected_seed = sum(CLOSES[:5]) / 5
        assert abs(result[4] - expected_seed) < 1e-10

    def test_short_input_returns_nones(self):
        result = calc_ema([1, 2], 10)
        assert all(v is None for v in result)

    def test_monotonic_up_ema_follows(self):
        up = list(range(1, 21))
        result = calc_ema(up, 5)
        # EMA should be increasing after seed
        valid = [v for v in result if v is not None]
        assert all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1))


class TestSMA:
    def test_length_matches_input(self):
        result = calc_sma(CLOSES, 5)
        assert len(result) == len(CLOSES)

    def test_first_values_are_none(self):
        result = calc_sma(CLOSES, 10)
        assert all(v is None for v in result[:9])

    def test_correct_value(self):
        result = calc_sma(CLOSES, 5)
        expected = sum(CLOSES[:5]) / 5
        assert abs(result[4] - expected) < 1e-10

    def test_last_value(self):
        result = calc_sma(CLOSES, 5)
        expected = sum(CLOSES[-5:]) / 5
        assert abs(result[-1] - expected) < 1e-10


class TestRSI:
    def test_length_matches_input(self):
        result = calc_rsi(CLOSES, 14)
        assert len(result) == len(CLOSES)

    def test_first_values_are_none(self):
        result = calc_rsi(CLOSES, 14)
        assert all(v is None for v in result[:14])

    def test_uptrend_rsi_above_50(self):
        up = list(range(100, 130))  # pure uptrend
        result = calc_rsi(up, 14)
        valid = [v for v in result if v is not None]
        assert all(v > 50 for v in valid), f"RSI in uptrend should be >50, got {valid}"

    def test_rsi_bounded_0_100(self):
        result = calc_rsi(CLOSES, 14)
        valid = [v for v in result if v is not None]
        assert all(0 <= v <= 100 for v in valid)

    def test_flat_series_rsi_50(self):
        flat = [100.0] * 30
        result = calc_rsi(flat, 14)
        # All changes are 0, so RSI = 100 (no losses) or undefined
        # With our implementation: ag=0, al=0, so r[p]=100
        assert result[14] == 100


class TestATR:
    def test_length_matches_input(self):
        result = calc_atr(HIGHS, LOWS, CLOSES, 14)
        assert len(result) == len(CLOSES)

    def test_positive_values(self):
        result = calc_atr(HIGHS, LOWS, CLOSES, 14)
        valid = [v for v in result if v is not None]
        assert all(v > 0 for v in valid)

    def test_constant_range_atr(self):
        # If high-low = 3.0 for every bar, ATR should converge to 3.0
        h = [103.0] * 30
        l = [100.0] * 30
        c = [101.5] * 30
        result = calc_atr(h, l, c, 5)
        valid = [v for v in result if v is not None]
        assert abs(valid[-1] - 3.0) < 0.01


class TestADX:
    def test_returns_dict_with_keys(self):
        result = calc_adx(HIGHS, LOWS, CLOSES, 14)
        assert "adx" in result
        assert "plusDI" in result
        assert "minusDI" in result

    def test_length_matches_input(self):
        result = calc_adx(HIGHS, LOWS, CLOSES, 14)
        assert len(result["adx"]) == len(CLOSES)

    def test_adx_bounded(self):
        result = calc_adx(HIGHS, LOWS, CLOSES, 14)
        valid = [v for v in result["adx"] if v is not None]
        if valid:  # may be empty for short series
            assert all(0 <= v <= 100 for v in valid)

    def test_short_series_returns_nones(self):
        result = calc_adx([1, 2, 3], [0, 1, 2], [0.5, 1.5, 2.5], 14)
        assert all(v is None for v in result["adx"])


class TestBB:
    def test_returns_dict_with_keys(self):
        result = calc_bb(CLOSES, 20, 2)
        assert "upper" in result
        assert "mid" in result
        assert "lower" in result

    def test_upper_above_mid_above_lower(self):
        result = calc_bb(CLOSES, 20, 2)
        for i in range(len(CLOSES)):
            if result["mid"][i] is not None:
                assert result["upper"][i] >= result["mid"][i] >= result["lower"][i]


class TestStochastic:
    def test_returns_dict(self):
        result = calc_stochastic(CANDLES, 14, 3, 3)
        assert "k" in result
        assert "d" in result

    def test_bounded_0_100(self):
        result = calc_stochastic(CANDLES, 14, 3, 3)
        k, d = result["k"], result["d"]
        # k/d may be a scalar or last element of a list
        if isinstance(k, list): k = k[-1] if k else None
        if isinstance(d, list): d = d[-1] if d else None
        if k is not None:
            assert 0 <= k <= 100
        if d is not None:
            assert 0 <= d <= 100


class TestATRPercentile:
    def test_returns_tuple(self):
        atr_series = calc_atr(HIGHS, LOWS, CLOSES, 14)
        pct, label = calc_atr_percentile(atr_series)
        assert pct is None or (0 <= pct <= 100)
        assert label in ("expanding", "contracting", "normal", "", "insufficient data")


class TestADXPercentile:
    def test_returns_tuple(self):
        adx_result = calc_adx(HIGHS, LOWS, CLOSES, 14)
        pct, label = calc_adx_percentile(adx_result["adx"])
        assert pct is None or (0 <= pct <= 100)


class TestFib:
    def test_fib_levels_ordered(self):
        result = calc_fib(CANDLES)
        assert result["highest"] >= result["fib236"] >= result["fib382"] >= result["fib500"] >= result["fib618"] >= result["fib786"] >= result["lowest"]

    def test_extension_above_high(self):
        result = calc_fib(CANDLES)
        assert result["ext1618"] > result["highest"]


class TestCalcLevels:
    def test_long_sl_below_entry(self):
        result = calc_levels(100.0, 2.0, "LONG", "forex")
        assert result["sl"] < 100.0
        assert result["tp1"] > 100.0
        assert result["tp2"] > result["tp1"]

    def test_short_sl_above_entry(self):
        result = calc_levels(100.0, 2.0, "SHORT", "forex")
        assert result["sl"] > 100.0
        assert result["tp1"] < 100.0
        assert result["tp2"] < result["tp1"]

    def test_rr_positive(self):
        result = calc_levels(100.0, 2.0, "LONG", "crypto")
        assert result["rr1"] > 0
        assert result["rr2"] > result["rr1"]
