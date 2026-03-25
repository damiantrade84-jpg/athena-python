"""test_factor_scoring.py — Unit tests for factor_scoring.py."""

import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from factor_scoring import compute_factor_scores, _factor_score
from config import CONFIG


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_snap(**overrides):
    """Minimal H4 snap with z-score fields."""
    base = {
        "adx": 30,
        "adx_z": 0.5,
        "rsi_z": 0.3,
        "macdLine_z": 0.2,
        "atr_z": 0.1,
        "bbWidth_z": -0.1,
        "realized_vol_z": 0.0,
        "obv_trend": 1,
        "fib_proximity": 1,
        "ema21": 105,
        "ema50": 100,
        "adxMomentum": "ACCELERATING",
        "adxSlope": 0.5,
    }
    base.update(overrides)
    return base


def _make_pair(**overrides):
    base = {"type": "crypto", "display": "BTC/USDT"}
    base.update(overrides)
    return base


def _make_candles(n=200, base=100):
    """Generate N synthetic candles."""
    return [
        {
            "open": base + i,
            "high": base + i + 2,
            "low": base + i - 1,
            "close": base + i + 1,
        }
        for i in range(n)
    ]


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
        """With very sparse data (unknown pair, no candles), score should be zero."""
        empty_snap = {}
        result = compute_factor_scores(
            d1_snap=empty_snap,
            h4_snap=empty_snap,
            h1_snap=empty_snap,
            pair={
                "type": "forex",
                "display": "XYZ/ABC",
            },  # unknown — no COT/carry cache
            d1_candles=[],
            h4_candles=[],
            h1_candles=[],
            volume_ratio=None,
        )
        assert result["insufficient_factors"] is True
        assert result["final_score"] == 0.0

    def test_sufficient_factors_produces_score(self):
        """With full data, should produce a non-zero score."""
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
        )
        assert result["insufficient_factors"] is False
        assert isinstance(result["final_score"], float)


# ── Regime detection ────────────────────────────────────────────────────────


class TestRegimeDetection:
    def test_regime_present(self):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
        )
        assert result["regime"] in (
            "TRENDING",
            "RANGING",
            "HIGH_VOLATILITY",
            "LOW_VOLATILITY",
            "UNKNOWN",
        )


# ── Return structure ────────────────────────────────────────────────────────


class TestReturnStructure:
    def test_all_expected_keys(self):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
        )
        expected_keys = {
            "final_score",
            "factor_scores",
            "weights",
            "regime",
            "filtered_indicators",
            "disabled_factors",
            "insufficient_factors",
        }
        assert expected_keys.issubset(result.keys())


class TestCryptoDirectionalOverrides:
    def test_crypto_uses_asset_specific_directional_settings(self):
        snap = _make_snap(
            ema21=None,
            ema50=None,
            rsi_z=0.2,
            macdLine_z=0.2,
            order_book_imbalance=None,
            orderflow_delta=None,
            liquidity_pressure=None,
            fib_proximity=None,
            obv_trend=None,
        )
        original_min = CONFIG.get("FACTOR_MIN_DIRECTIONAL_CRYPTO")
        original_span = CONFIG.get("FACTOR_DIRECTIONAL_SOFT_SPAN_CRYPTO")
        try:
            CONFIG["FACTOR_MIN_DIRECTIONAL_CRYPTO"] = 0.15
            CONFIG["FACTOR_DIRECTIONAL_SOFT_SPAN_CRYPTO"] = 0.30
            result = compute_factor_scores(
                d1_snap=snap,
                h4_snap=snap,
                h1_snap=snap,
                pair=_make_pair(display="SOL/USDT"),
                d1_candles=_make_candles(200),
                h4_candles=_make_candles(200),
                h1_candles=_make_candles(200),
                volume_ratio=1.5,
            )
        finally:
            CONFIG["FACTOR_MIN_DIRECTIONAL_CRYPTO"] = original_min
            CONFIG["FACTOR_DIRECTIONAL_SOFT_SPAN_CRYPTO"] = original_span

        assert result["min_directional_threshold"] == 0.15
        assert result["effective_min_directional"] == pytest.approx(0.1125)
        assert result["directional_confidence_multiplier"] > 0.5

    def test_crypto_optional_coverage_uses_live_funding_only_for_alts(self):
        snap = _make_snap(
            order_book_imbalance=None,
            orderflow_delta=None,
            liquidity_pressure=None,
            fib_proximity=None,
        )
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(display="SOL/USDT"),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
            funding_rate=0.0001,
        )
        assert result["optional_factor_coverage"] == 1.0
        assert result["missing_directional_optional_count"] == 0

    def test_crypto_disables_candle_proxy_microstructure(self):
        snap = _make_snap(
            order_book_imbalance=None,
            orderflow_delta=None,
            liquidity_pressure=None,
            liquidity_wall_detection=None,
        )
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(display="SOL/USDT"),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
        )
        assert result["factor_scores"]["microstructure"] is None
        assert "microstructure" in result["disabled_factors"]

    @patch("carry_feed.get_carry_z", return_value=2.0)
    @patch("cot_feed.get_cot_z", return_value=1.2)
    def test_crypto_cot_only_applies_to_btc_eth_and_carry_is_removed(self, *_mocks):
        snap = _make_snap()
        btc_result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(display="BTC/USDT"),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
            funding_rate=0.0001,
        )
        alt_result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(display="SOL/USDT"),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
            funding_rate=0.0001,
        )
        assert btc_result["filtered_indicators"]["cot_z"] == 1.2
        assert alt_result["filtered_indicators"]["cot_z"] is None
        assert btc_result["filtered_indicators"]["carry_z"] is None
        assert "carry" not in btc_result["factor_scores"]

    def test_crypto_short_horizon_weights_are_capped(self):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(display="BTC/USDT", score_group="crypto_btc"),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
            funding_rate=0.0001,
        )
        assert result["weights"]["derivatives"] <= CONFIG["CRYPTO_FACTOR_WEIGHT_CAPS"]["derivatives"]
        assert result["weights"]["microstructure"] <= CONFIG["CRYPTO_FACTOR_WEIGHT_CAPS"]["microstructure"]


# ── Crypto simplification tests ──────────────────────────────────────────────


class TestCryptoOptionalCoverage:
    """optional_directional_keys for crypto should only include funding_rate,
    not cot_z/carry_z which are permanently absent for most alts."""

    def test_crypto_alt_optional_coverage_funding_only(self):
        from factor_scoring import _optional_directional_keys

        keys = _optional_directional_keys("crypto", {"display": "SOL/USDT"})
        assert keys == ("funding_rate",)

    def test_crypto_btc_includes_cot(self):
        from factor_scoring import _optional_directional_keys

        keys = _optional_directional_keys("crypto", {"display": "BTC/USDT"})
        assert "funding_rate" in keys
        assert "cot_z" in keys
        assert "carry_z" not in keys

    def test_forex_keeps_all_optional_keys(self):
        from factor_scoring import _optional_directional_keys

        keys = _optional_directional_keys("forex", {"display": "EUR/USD"})
        assert set(keys) == {"funding_rate", "cot_z", "carry_z"}

    def test_crypto_alt_full_coverage_when_funding_present(self):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap, h4_snap=snap, h1_snap=snap,
            pair=_make_pair(display="DOGE/USDT"),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
            funding_rate=0.0002,
        )
        assert result["optional_factor_coverage"] == 1.0
        assert result["missing_directional_optional_count"] == 0


class TestCryptoMicrostructureZeroWeight:
    """With microstructure weight=0 in config, scores should still aggregate and resolve direction."""

    def test_score_nonzero_with_zero_micro_weight(self):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap, h4_snap=snap, h1_snap=snap,
            pair=_make_pair(display="SOL/USDT"),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
            funding_rate=0.0001,
        )
        assert result["weights"].get("microstructure", 0) == 0
        assert result["final_score"] > 0
        assert result["direction"] in ("LONG", "SHORT")

    def test_carry_not_in_crypto_factors(self):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap, h4_snap=snap, h1_snap=snap,
            pair=_make_pair(display="ETH/USDT"),
            d1_candles=_make_candles(200),
            h4_candles=_make_candles(200),
            h1_candles=_make_candles(200),
            volume_ratio=1.5,
        )
        assert "carry" not in result["factor_scores"]
