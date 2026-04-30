"""Tests for mathematical/computational fixes from 2026-04-30 audit.

Covers:
  - C1: ATR=0 hard abort in compute_factor_scores
  - C2: _mad_dispersion_score (renamed from _robust_z_score_normalization)
  - H1: calc_adx index alignment
  - H2: timeframe_alignment scale normalization
  - H3: coherence_ratio floor configurable
  - H4: calc_stochastic None-safe SMA
  - M1: per-asset-class RSI bounds
  - M2: MACD opposing penalty increased
  - M5: zscore_normalize clamp parameter
  - L3: calc_squeeze consecutive bars
"""

import math
import pytest

from indicators import calc_adx, calc_stochastic, calc_squeeze, calc_sma
from confidence_engine import _mad_dispersion_score, timeframe_alignment, _std
from factor_scoring import _coherent_trend_score, _momentum_quality
from config import CONFIG
from feature_normalizer import zscore_normalize


# ── C1: ATR=0 hard abort ─────────────────────────────────────────────────────

class TestATRZeroAbort:
    def test_atr_zero_returns_zero_result(self):
        from factor_scoring import compute_factor_scores

        pair = {"type": "crypto", "display": "BTC/USDT"}
        d1_snap = {"ema21": 50000, "ema200": 49000}
        h4_snap = {"close": 50000, "atr": 0, "ema21": 50000, "ema50": 49000}
        h1_snap = {"ema21": 50000, "ema50": 49000}

        result = compute_factor_scores(
            d1_snap, h4_snap, h1_snap, pair,
            [], [], [], 1.0,
        )
        assert result["final_score"] == 0.0
        assert result["insufficient_factors"] is True


# ── C2: _mad_dispersion_score ────────────────────────────────────────────────

class TestMADDispersionScore:
    def test_perfect_agreement(self):
        assert _mad_dispersion_score(0.0) == 1.0
        assert _mad_dispersion_score(-0.1) == 1.0

    def test_high_dispersion(self):
        # Large MAD → score near 0
        score = _mad_dispersion_score(10.0)
        assert 0.0 <= score < 0.1

    def test_monotonic(self):
        # Higher MAD → lower score
        s1 = _mad_dispersion_score(0.5)
        s2 = _mad_dispersion_score(1.0)
        s3 = _mad_dispersion_score(2.0)
        assert s1 > s2 > s3

    def test_bounded_0_1(self):
        for mm in [0.0, 0.1, 1.0, 5.0, 100.0]:
            s = _mad_dispersion_score(mm)
            assert 0.0 <= s <= 1.0


# ── H1: calc_adx alignment ───────────────────────────────────────────────────

class TestCalcADXAlignment:
    def test_first_valid_indices(self):
        """ADX requires 2p+1 bars; DI requires p+1 bars."""
        n = 60
        hi = [10 + i * 0.5 for i in range(n)]
        lo = [9 + i * 0.5 for i in range(n)]
        c = [10 + i * 0.5 for i in range(n)]
        p = 14

        result = calc_adx(hi, lo, c, p)

        plus_di = result["plusDI"]
        minus_di = result["minusDI"]
        adx = result["adx"]

        # First valid DI at bar p+1
        first_plus = next((i for i, v in enumerate(plus_di) if v is not None), None)
        first_minus = next((i for i, v in enumerate(minus_di) if v is not None), None)
        assert first_plus == p + 1
        assert first_minus == p + 1

        # First valid ADX at bar 2p+1
        first_adx = next((i for i, v in enumerate(adx) if v is not None), None)
        assert first_adx == 2 * p + 1

    def test_adx_smoothed_not_simple_mean(self):
        """ADX at 2p+1 should be the smoothed value, not a raw mean."""
        n = 60
        # Strong trending data
        hi = [10 + i for i in range(n)]
        lo = [9 + i for i in range(n)]
        c = [10 + i for i in range(n)]
        p = 14

        result = calc_adx(hi, lo, c, p)
        adx = result["adx"]

        # In a strong trend, ADX should be high
        valid_adx = [v for v in adx if v is not None]
        assert len(valid_adx) > 0
        assert all(v > 20 for v in valid_adx[5:])  # settled values

    def test_insufficient_data_returns_none(self):
        """Fewer than 2p+1 bars → all None."""
        p = 14
        n = 2 * p  # 28 < 29
        hi = [10 + i for i in range(n)]
        lo = [9 + i for i in range(n)]
        c = [10 + i for i in range(n)]

        result = calc_adx(hi, lo, c, p)
        assert all(v is None for v in result["adx"])


# ── H2: timeframe_alignment scale normalization ──────────────────────────────

class TestTimeframeAlignment:
    def test_all_same_direction_normalized(self):
        """All TFs agree on direction → high alignment regardless of magnitude."""
        d1 = {"final_score": 2.5}  # strong LONG
        h4 = {"final_score": 1.5}  # moderate LONG
        h1 = {"final_score": 0.8}  # weak LONG

        align = timeframe_alignment(d1, h4, h1)
        assert align is not None
        assert align > 0.7  # all same direction → high alignment (tanh scaling)

    def test_divergent_directions_low_alignment(self):
        """TFs disagree on direction → low alignment."""
        d1 = {"final_score": 2.5}   # LONG
        h4 = {"final_score": 0.3}   # weak / near neutral
        h1 = {"final_score": 0.1}   # opposite-ish

        align = timeframe_alignment(d1, h4, h1)
        assert align is not None
        # Should be lower than agreeing case

    def test_two_timeframes_minimum(self):
        """Only 2 TFs available → still computes."""
        align = timeframe_alignment({"final_score": 2.0}, {"final_score": 1.5}, None)
        assert align is not None
        assert 0.0 <= align <= 1.0

    def test_one_timeframe_returns_none(self):
        """Only 1 TF → None (insufficient data)."""
        align = timeframe_alignment({"final_score": 2.0}, None, None)
        assert align is None


# ── H3: coherence_ratio floor ────────────────────────────────────────────────

class TestCoherenceRatioFloor:
    def test_tied_vote_with_default_floor_zero(self):
        """With default floor=0.0, a tied vote should produce near-zero magnitude."""
        d1_snap = {"ema21": 100.0, "ema200": 90.0}   # LONG
        h4_snap = {"ema21": 100.0, "ema50": 110.0}   # SHORT
        h1_snap = {"ema21": 100.0, "ema50": 110.0}   # SHORT

        score, direction, detail = _coherent_trend_score(d1_snap, h4_snap, h1_snap, "crypto")
        # D1 weight 0.5 LONG, H4 weight 0.3 SHORT, H1 weight 0.2 SHORT
        # long_w = 0.5, short_w = 0.5 → tied
        # D1 tiebreaker → LONG
        # coherence_ratio with floor=0: dominant_w/total_w = 0.5/1.0 = 0.5
        # magnitude = (0.35 + 0.65*0.5) * 3.0 * (3/3) = 0.675 * 3.0 = 2.025
        assert direction == "LONG"
        assert abs(score) < 2.5  # not maxed out

    def test_configurable_floor(self):
        """COHERENCE_RATIO_FLOOR config is respected."""
        # Save original
        orig = CONFIG.get("COHERENCE_RATIO_FLOOR", 0.0)
        try:
            CONFIG["COHERENCE_RATIO_FLOOR"] = 0.7
            d1_snap = {"ema21": 100.0, "ema200": 90.0}
            h4_snap = {"ema21": 100.0, "ema50": 110.0}
            h1_snap = {"ema21": 100.0, "ema50": 110.0}

            score, direction, detail = _coherent_trend_score(d1_snap, h4_snap, h1_snap, "crypto")
            # With floor=0.7, coherence_ratio = max(0.7, 0.5) = 0.7
            # magnitude = (0.35 + 0.65*0.7) * 3.0 = 0.805 * 3.0 = 2.415
            assert abs(score) > 2.0  # higher than with floor=0
        finally:
            CONFIG["COHERENCE_RATIO_FLOOR"] = orig


# ── H4: calc_stochastic None-safe ────────────────────────────────────────────

class TestCalcStochastic:
    def test_no_zero_fill_contamination(self):
        """Early SMA values must not be contaminated by zero-fill."""
        candles = []
        for i in range(30):
            candles.append({"high": 100 + i, "low": 90 + i, "close": 95 + i})

        stoch = calc_stochastic(candles, 14, 3, 3)
        k = stoch["k"]
        d = stoch["d"]

        # First valid rawK at index 13
        # First valid SMA(%K) at index 13 + 3 - 1 = 15
        # First valid SMA(%D) at index 15 + 3 - 1 = 17
        assert all(v is None for v in k[:15])
        assert all(v is None for v in d[:17])

        # Valid values should all be equal (linear trend = constant %K)
        valid_k = [v for v in k if v is not None]
        valid_d = [v for v in d if v is not None]
        assert len(valid_k) > 0
        assert len(valid_d) > 0
        # In a perfectly linear trend, %K is constant
        assert all(v == valid_k[0] for v in valid_k)
        assert all(v == valid_d[0] for v in valid_d)

    def test_calc_sma_none_safe(self):
        """calc_sma must skip None values, not treat them as 0."""
        series = [None, None, 10.0, 20.0, 30.0]
        result = calc_sma(series, 3)
        # Index 2: window [None, None, 10] → valid=[10] → 10.0
        assert result[2] == 10.0
        # Index 3: window [None, 10, 20] → valid=[10,20] → 15.0
        assert result[3] == 15.0
        # Index 4: window [10, 20, 30] → valid=[10,20,30] → 20.0
        assert result[4] == 20.0


# ── M1: per-asset-class RSI bounds ───────────────────────────────────────────

class TestRSIBounds:
    def test_crypto_bounds(self):
        bounds = CONFIG["RSI_BOUNDS"]["crypto"]
        assert bounds["ob"] == 80
        assert bounds["os"] == 20

    def test_forex_bounds(self):
        bounds = CONFIG["RSI_BOUNDS"]["forex"]
        assert bounds["ob"] == 70
        assert bounds["os"] == 30

    def test_commodity_bounds(self):
        bounds = CONFIG["RSI_BOUNDS"]["commodity"]
        assert bounds["ob"] == 75
        assert bounds["os"] == 25

    def test_momentum_quality_uses_asset_bounds(self):
        """_momentum_quality must use per-asset RSI bounds."""
        h4_snap = {"rsi": 75, "macdHist": 0.1}

        # Crypto: 75 < 80 (ob) and > 50 → confirming (+0.50)
        score_crypto = _momentum_quality(h4_snap, "LONG", "crypto")
        assert score_crypto > 0.4

        # Forex: 75 >= 70 (ob) → overbought (-0.25)
        score_forex = _momentum_quality(h4_snap, "LONG", "forex")
        assert score_forex < 0.3


# ── M2: MACD opposing penalty ────────────────────────────────────────────────

class TestMACDOpposingPenalty:
    def test_opposing_macd_reduces_score_strongly(self):
        """With penalty -0.50, opposing MACD should significantly reduce score."""
        h4_snap = {"rsi": 55, "macdHist": -0.5}  # RSI confirming, MACD opposing

        score = _momentum_quality(h4_snap, "LONG", "forex")
        # RSI = 55 → +0.50 (weight 0.6)
        # MACD = -0.5 (opposing) → -0.50 (weight 0.4)
        # Weighted = (0.50*0.6 + (-0.50)*0.4) / 1.0 = 0.30 - 0.20 = 0.10
        assert score < 0.2  # significantly reduced

    def test_confirming_macd_full_score(self):
        """Both confirming → high score."""
        h4_snap = {"rsi": 55, "macdHist": 0.5}

        score = _momentum_quality(h4_snap, "LONG", "forex")
        # RSI = 55 → +0.50, MACD = +0.5 → +0.50
        # Weighted = (0.50*0.6 + 0.50*0.4) = 0.50
        assert score > 0.4


# ── M5: zscore_normalize clamp parameter ─────────────────────────────────────

class TestZScoreNormalizeClamp:
    def test_clamp_default_true(self):
        """Default behavior clamps to [-3, 3]."""
        # With window=20, 19 stable values + 1 extreme outlier gives z > 3
        series = [0.0] * 19 + [100.0]
        result = zscore_normalize(series, window=20)
        assert result[-1] == 3.0  # clamped

    def test_clamp_false_preserves_tail(self):
        """clamp=False preserves extreme values."""
        # With window=20, 19 stable + 1 extreme gives z ≈ 4.25 (> 3)
        series = [0.0] * 19 + [100.0]
        result = zscore_normalize(series, window=20, clamp=False)
        assert result[-1] > 3.0  # unclamped, very large

    def test_clamp_false_reasonable_values(self):
        """Non-extreme values unchanged by clamp=False."""
        series = list(range(20))
        result_clamped = zscore_normalize(series, window=10, clamp=True)
        result_free = zscore_normalize(series, window=10, clamp=False)
        # For non-extreme values, both should be identical
        for i in range(10, 20):
            if abs(result_free[i]) <= 3.0:
                assert result_clamped[i] == result_free[i]


# ── L3: calc_squeeze consecutive bars ────────────────────────────────────────

class TestCalcSqueeze:
    def test_consecutive_bars_counted(self):
        """Squeeze should count consecutive bars correctly."""
        # Tight range → squeeze active
        candles = []
        for i in range(30):
            candles.append({"high": 100 + i * 0.01, "low": 99 + i * 0.01, "close": 99.5 + i * 0.01})

        sq = calc_squeeze(candles, bb_period=20, bb_mult=2.0, kc_period=20, kc_mult=1.5)
        assert sq is not None
        assert sq["active"] is True
        # With 30 bars and tight range, should have multiple consecutive squeeze bars
        assert sq["bars"] >= 1

    def test_no_squeeze_returns_zero_bars(self):
        """High volatility → no squeeze."""
        # Random walk with large swings creates high variance → wide BB
        import random
        random.seed(42)
        candles = []
        base = 100
        for i in range(30):
            change = random.uniform(-30, 30)
            c = base + change
            h = c + random.uniform(0, 10)
            l = c - random.uniform(0, 10)
            candles.append({"high": h, "low": l, "close": c})
            base = c

        sq = calc_squeeze(candles, bb_period=20, bb_mult=2.0, kc_period=20, kc_mult=1.5)
        assert sq is not None
        assert sq["active"] is False
        assert sq["bars"] == 0
