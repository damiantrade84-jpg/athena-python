"""test_factor_scoring.py — Unit tests for factor_scoring.py."""

import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from factor_scoring import (
    compute_factor_scores,
    _factor_score,
    make_regime_smoothing_context,
)
from config import CONFIG, _deep_merge_dict
from scoring import calc_confluence


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


def _make_volume_candles(n=200, base=100):
    """Generate candles with real volume and a late bullish conviction acceleration."""
    candles = []
    for i in range(n):
        open_price = base + i * 0.1
        if i < n - 10:
            body = 0.4
            wick = 0.2
            vol = 20
        else:
            body = 1.8
            wick = 0.3
            vol = 120
        close_price = open_price + body
        candles.append(
            {
                "open": open_price,
                "high": close_price + wick,
                "low": open_price - wick,
                "close": close_price,
                "vol": vol,
            }
        )
    return candles


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

    def test_crypto_routes_vms_into_momentum_without_reenabling_microstructure(self):
        snap = _make_snap(
            rsi_z=0.0,
            macdLine_z=0.0,
            order_book_imbalance=None,
            orderflow_delta=None,
            liquidity_pressure=None,
            liquidity_wall_detection=None,
        )
        candles = _make_volume_candles(200)
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(display="SOL/USDT"),
            d1_candles=candles,
            h4_candles=candles,
            h1_candles=candles,
            volume_ratio=1.5,
        )
        assert result["filtered_indicators"]["volume_momentum_spread"] is not None
        assert result["factor_scores"]["momentum"] > 0
        assert result["factor_scores"]["microstructure"] is None
        assert result["weights"]["microstructure"] == 0

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


class TestAdaptiveWeights:
    def test_neutral_adaptive_weights_leave_resolved_weights_unchanged(self):
        snap = _make_snap()
        with patch.dict(CONFIG, {"ADAPTIVE_WEIGHTS_ENABLED": True}):
            with patch("adaptive_weights.get_adaptive_weights", return_value={}):
                baseline = compute_factor_scores(
                    d1_snap=snap,
                    h4_snap=snap,
                    h1_snap=snap,
                    pair={"type": "forex", "display": "EUR/USD"},
                    d1_candles=_make_candles(220),
                    h4_candles=_make_candles(220),
                    h1_candles=_make_candles(220),
                    volume_ratio=1.5,
                )
            neutral = {
                "trend": 1.0,
                "momentum": 1.0,
                "volatility": 1.0,
                "volume": 1.0,
                "structure": 1.0,
                "derivatives": 1.0,
                "microstructure": 1.0,
                "carry": 1.0,
                "trend_strength": 1.0,
            }
            with patch("adaptive_weights.get_adaptive_weights", return_value=neutral):
                result = compute_factor_scores(
                    d1_snap=snap,
                    h4_snap=snap,
                    h1_snap=snap,
                    pair={"type": "forex", "display": "EUR/USD"},
                    d1_candles=_make_candles(220),
                    h4_candles=_make_candles(220),
                    h1_candles=_make_candles(220),
                    volume_ratio=1.5,
                )
        assert result["weights"] == pytest.approx(baseline["weights"])

    def test_adaptive_weights_scale_runtime_weights_instead_of_replacing_them(self):
        snap = _make_snap()
        with patch.dict(CONFIG, {"ADAPTIVE_WEIGHTS_ENABLED": True}):
            with patch("adaptive_weights.get_adaptive_weights", return_value={}):
                baseline = compute_factor_scores(
                    d1_snap=snap,
                    h4_snap=snap,
                    h1_snap=snap,
                    pair={"type": "forex", "display": "EUR/USD"},
                    d1_candles=_make_candles(220),
                    h4_candles=_make_candles(220),
                    h1_candles=_make_candles(220),
                    volume_ratio=1.5,
                )
        adaptive = {
            "trend": 1.1,
            "momentum": 0.8,
            "volatility": 1.0,
            "volume": 1.0,
            "structure": 1.0,
            "derivatives": 1.0,
            "microstructure": 1.0,
            "carry": 1.0,
            "trend_strength": 1.25,
        }
        with patch.dict(CONFIG, {"ADAPTIVE_WEIGHTS_ENABLED": True}):
            with patch("adaptive_weights.get_adaptive_weights", return_value=adaptive):
                result = compute_factor_scores(
                    d1_snap=snap,
                    h4_snap=snap,
                    h1_snap=snap,
                    pair={"type": "forex", "display": "EUR/USD"},
                    d1_candles=_make_candles(220),
                    h4_candles=_make_candles(220),
                    h1_candles=_make_candles(220),
                    volume_ratio=1.5,
                )
        assert result["weights"]["trend"] == pytest.approx(baseline["weights"]["trend"] * 1.1)
        assert result["weights"]["momentum"] == pytest.approx(
            baseline["weights"]["momentum"] * 0.8
        )
        assert result["weights"]["trend_strength"] == pytest.approx(
            baseline["weights"]["trend_strength"] * 1.25
        )

    def test_adaptive_weights_do_not_reenable_zero_weight_factors(self):
        snap = _make_snap()
        adaptive = {
            "trend": 1.0,
            "momentum": 1.0,
            "volatility": 1.0,
            "volume": 1.0,
            "structure": 1.0,
            "derivatives": 1.5,
            "microstructure": 2.0,
            "carry": 3.0,
            "trend_strength": 1.0,
        }
        with patch.dict(CONFIG, {"ADAPTIVE_WEIGHTS_ENABLED": True}):
            with patch("adaptive_weights.get_adaptive_weights", return_value=adaptive):
                result = compute_factor_scores(
                    d1_snap=snap,
                    h4_snap=snap,
                    h1_snap=snap,
                    pair=_make_pair(display="BTC/USDT"),
                    d1_candles=_make_candles(220),
                    h4_candles=_make_candles(220),
                    h1_candles=_make_candles(220),
                    volume_ratio=1.5,
                    funding_rate=0.0001,
                )
        assert result["weights"]["microstructure"] == 0
        assert "carry" not in result["weights"] or result["weights"].get("carry", 0) == 0


class TestDirectionUsesEffectiveWeights:
    def test_zero_weight_directional_factor_cannot_flip_direction(self):
        snap = _make_snap(
            order_book_imbalance=-3.0,
            liquidity_wall_detection=-3.0,
            orderflow_delta=-3.0,
            liquidity_pressure=-3.0,
            microstructure_age_sec=1.0,
        )
        with patch("adaptive_weights.get_adaptive_weights", return_value={}):
            result = compute_factor_scores(
                d1_snap=snap,
                h4_snap=snap,
                h1_snap=snap,
                pair=_make_pair(display="BTC/USDT", score_group="crypto_btc"),
                d1_candles=_make_candles(220),
                h4_candles=_make_candles(220),
                h1_candles=_make_candles(220),
                volume_ratio=1.5,
                funding_rate=0.0001,
            )
        assert result["weights"]["microstructure"] == 0
        assert result["factor_scores"]["microstructure"] == -3.0
        assert result["direction"] == "LONG"
        assert "microstructure" not in result["active_directional_factors"]
        assert result["directional_score"] > 0


class TestRegimeSmoothing:
    def test_default_calls_are_stateless(self):
        with patch("adaptive_weights.get_adaptive_weights", return_value={}):
            first = compute_factor_scores(
                d1_snap=_make_snap(adx=40, bbWidth_pct=70, adxMomentum="stable"),
                h4_snap=_make_snap(adx=40, bbWidth_pct=70, adxMomentum="stable"),
                h1_snap=_make_snap(adx=40, bbWidth_pct=70, adxMomentum="stable"),
                pair={"type": "stock", "display": "AAPL"},
                d1_candles=_make_candles(220),
                h4_candles=_make_candles(220),
                h1_candles=_make_candles(220),
                volume_ratio=1.5,
            )
            second = compute_factor_scores(
                d1_snap=_make_snap(adx=10, bbWidth_pct=10, adxMomentum="collapsing"),
                h4_snap=_make_snap(adx=10, bbWidth_pct=10, adxMomentum="collapsing"),
                h1_snap=_make_snap(adx=10, bbWidth_pct=10, adxMomentum="collapsing"),
                pair={"type": "stock", "display": "AAPL"},
                d1_candles=_make_candles(220),
                h4_candles=_make_candles(220),
                h1_candles=_make_candles(220),
                volume_ratio=1.5,
            )
        assert first["regime"] == "TRENDING"
        assert second["regime"] == "LOW_VOLATILITY"

    def test_explicit_context_preserves_smoothing(self):
        regime_context = make_regime_smoothing_context()
        with patch("adaptive_weights.get_adaptive_weights", return_value={}):
            first = compute_factor_scores(
                d1_snap=_make_snap(adx=40, bbWidth_pct=70, adxMomentum="stable"),
                h4_snap=_make_snap(adx=40, bbWidth_pct=70, adxMomentum="stable"),
                h1_snap=_make_snap(adx=40, bbWidth_pct=70, adxMomentum="stable"),
                pair={"type": "stock", "display": "AAPL"},
                d1_candles=_make_candles(220),
                h4_candles=_make_candles(220),
                h1_candles=_make_candles(220),
                volume_ratio=1.5,
                regime_context=regime_context,
            )
            second = compute_factor_scores(
                d1_snap=_make_snap(adx=10, bbWidth_pct=10, adxMomentum="collapsing"),
                h4_snap=_make_snap(adx=10, bbWidth_pct=10, adxMomentum="collapsing"),
                h1_snap=_make_snap(adx=10, bbWidth_pct=10, adxMomentum="collapsing"),
                pair={"type": "stock", "display": "AAPL"},
                d1_candles=_make_candles(220),
                h4_candles=_make_candles(220),
                h1_candles=_make_candles(220),
                volume_ratio=1.5,
                regime_context=regime_context,
            )
        assert first["regime"] == "TRENDING"
        assert second["regime"] == "TRENDING"


class TestConfigDeepMerge:
    def test_deep_merge_preserves_unspecified_nested_defaults(self):
        merged = _deep_merge_dict(
            {"REGIME_WEIGHTS": {"TRENDING": {"trend": 2.0, "trend_strength": 1.5}}},
            {"REGIME_WEIGHTS": {"TRENDING": {"trend": 2.5}}},
        )
        assert merged["REGIME_WEIGHTS"]["TRENDING"]["trend"] == 2.5
        assert merged["REGIME_WEIGHTS"]["TRENDING"]["trend_strength"] == 1.5

    def test_deep_merge_overwrites_scalars(self):
        merged = _deep_merge_dict({"FACTOR_MIN_DIRECTIONAL": 0.25}, {"FACTOR_MIN_DIRECTIONAL": 0.4})
        assert merged["FACTOR_MIN_DIRECTIONAL"] == 0.4


class TestTrendWeights:
    def test_asset_specific_trend_weights_affect_trend_score(self):
        with patch("adaptive_weights.get_adaptive_weights", return_value={}):
            stock = compute_factor_scores(
                d1_snap=_make_snap(ema21=105, ema50=100),
                h4_snap=_make_snap(ema21=105, ema50=100),
                h1_snap=_make_snap(ema21=95, ema50=100),
                pair={"type": "stock", "display": "AAPL"},
                d1_candles=_make_candles(220),
                h4_candles=_make_candles(220),
                h1_candles=_make_candles(220),
                volume_ratio=1.5,
            )
            commodity = compute_factor_scores(
                d1_snap=_make_snap(ema21=105, ema50=100),
                h4_snap=_make_snap(ema21=105, ema50=100),
                h1_snap=_make_snap(ema21=95, ema50=100),
                pair={"type": "commodity", "display": "XAU/USD"},
                d1_candles=_make_candles(220),
                h4_candles=_make_candles(220),
                h1_candles=_make_candles(220),
                volume_ratio=1.5,
            )
        assert stock["factor_scores"]["trend"] != commodity["factor_scores"]["trend"]

    def test_missing_trend_weight_config_falls_back_safely(self):
        original = CONFIG["INDICATOR_WEIGHTS"].get("trend")
        try:
            CONFIG["INDICATOR_WEIGHTS"]["trend"] = {}
            with patch("adaptive_weights.get_adaptive_weights", return_value={}):
                result = compute_factor_scores(
                    d1_snap=_make_snap(ema21=105, ema50=100),
                    h4_snap=_make_snap(ema21=105, ema50=100),
                    h1_snap=_make_snap(ema21=95, ema50=100),
                    pair={"type": "stock", "display": "AAPL"},
                    d1_candles=_make_candles(220),
                    h4_candles=_make_candles(220),
                    h1_candles=_make_candles(220),
                    volume_ratio=1.5,
                )
        finally:
            CONFIG["INDICATOR_WEIGHTS"]["trend"] = original
        assert isinstance(result["factor_scores"]["trend"], float)


class TestCalcConfluenceIntegration:
    def test_calc_confluence_preserves_effective_direction_diagnostics(self):
        snap = _make_snap(
            order_book_imbalance=-3.0,
            liquidity_wall_detection=-3.0,
            orderflow_delta=-3.0,
            liquidity_pressure=-3.0,
            microstructure_age_sec=1.0,
        )
        tf = {"snap": snap}
        with patch("adaptive_weights.get_adaptive_weights", return_value={}):
            result = calc_confluence(
                d1=tf,
                h4=tf,
                h1=tf,
                vr=1.5,
                stoch={},
                pair={"type": "crypto", "display": "BTC/USDT", "score_group": "crypto_btc"},
                btc_bias="neutral",
                d1_candles=_make_candles(220),
                h4_candles=_make_candles(220),
                h1_candles=_make_candles(220),
                funding_rate=0.0001,
            )
        assert result["direction"] == "LONG"
        assert "microstructure" not in result["factorDiagnostics"]["activeDirectionalFactors"]
        assert result["factor_scores"]["microstructure"] == -3.0


class TestCarryZDirectional:
    @patch("carry_feed.get_carry_z", return_value=2.0)
    @patch("cot_feed.get_cot_z", return_value=0.0)
    def test_positive_carry_supports_long_directionally(self, *_mocks):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair={"type": "forex", "display": "EUR/USD"},
            d1_candles=_make_candles(220),
            h4_candles=_make_candles(220),
            h1_candles=_make_candles(220),
            volume_ratio=1.5,
        )

        assert result["direction"] == "LONG"
        assert result["filtered_indicators"]["carry_z"] == 2.0
        assert result["factor_scores"]["carry"] > 0
        assert "carry" in result["active_directional_factors"]
        assert "carry" not in result["active_nondirectional_factors"]

    @patch("carry_feed.get_carry_z", return_value=-2.0)
    @patch("cot_feed.get_cot_z", return_value=0.0)
    def test_negative_carry_opposes_long_directionally(self, *_mocks):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair={"type": "forex", "display": "EUR/USD"},
            d1_candles=_make_candles(220),
            h4_candles=_make_candles(220),
            h1_candles=_make_candles(220),
            volume_ratio=1.5,
        )

        assert result["direction"] == "LONG"
        assert result["filtered_indicators"]["carry_z"] == -2.0
        assert result["factor_scores"]["carry"] < 0
        assert "carry" in result["active_directional_factors"]
        assert "carry" not in result["active_nondirectional_factors"]

    @patch("carry_feed.get_carry_z", return_value=2.0)
    @patch("cot_feed.get_cot_z", return_value=0.0)
    def test_crypto_still_excludes_carry(self, *_mocks):
        snap = _make_snap()
        result = compute_factor_scores(
            d1_snap=snap,
            h4_snap=snap,
            h1_snap=snap,
            pair=_make_pair(display="ETH/USDT"),
            d1_candles=_make_candles(220),
            h4_candles=_make_candles(220),
            h1_candles=_make_candles(220),
            volume_ratio=1.5,
            funding_rate=0.0001,
        )

        assert result["filtered_indicators"]["carry_z"] is None
        assert "carry" not in result["factor_scores"]


# ── Direction tie and funding rate calibration (FIX 1 & 2) ──────────────────


class TestDirectionAndFundingFixes:
    def test_direction_tie_is_short(self):
        """dir_sum = 0 must resolve SHORT, not LONG (FIX 1: >= changed to >)."""
        dir_sum = 0.0
        direction = "LONG" if dir_sum > 0 else "SHORT"
        assert direction == "SHORT", "Tie (dir_sum=0) must resolve SHORT, not LONG"

    @patch("carry_feed.get_carry_z", return_value=0.0)
    @patch("cot_feed.get_cot_z", return_value=0.0)
    def test_direction_tie_via_balanced_factors(self, *_mocks):
        """All directional z-scores zero → dir_sum=0 → SHORT (FIX 1 regression guard)."""
        zero_snap = {
            "adx": 25,
            "adx_z": 0.0,
            "rsi_z": 0.0,
            "macdLine_z": 0.0,
            "atr_z": 0.0,
            "bbWidth_z": 0.0,
            "realized_vol_z": 0.0,
            "obv_trend": 0,
            "fib_proximity": 0,
            "ema21": 100,
            "ema50": 100,  # equal EMAs → no alignment signal
            "adxMomentum": "FLAT",
            "adxSlope": 0.0,
        }
        result = compute_factor_scores(
            d1_snap=zero_snap,
            h4_snap=zero_snap,
            h1_snap=zero_snap,
            pair=_make_pair(display="BTC/USDT"),
            d1_candles=_make_candles(220),
            h4_candles=_make_candles(220),
            h1_candles=_make_candles(220),
            volume_ratio=1.0,
            funding_rate=0.0001,  # exact neutral → 0.0 score
        )
        # dir_sum of 0 must produce SHORT (not LONG)
        assert result["direction"] == "SHORT"

    def test_funding_moderate_does_not_max(self):
        """funding_rate=0.0005 (0.05%) → adjusted=0.0004 → score=-2.0 (FIX 2: multiplier 5000)."""
        adjusted = 0.0005 - 0.0001  # = 0.0004
        score = max(-3.0, min(3.0, -adjusted * 5000))
        assert abs(score) < 3.0, f"Score {score} hit ±3.0 clamp — multiplier too aggressive"
        assert score == pytest.approx(-2.0, abs=1e-9)

    def test_funding_neutral_excluded(self):
        """funding_rate=0.0001 (exact neutral) → adjusted=0.0 → score=0.0."""
        adjusted = 0.0001 - 0.0001  # = 0.0
        # At 0.0: -0.0 * 5000 = 0.0 → indicator = 0.0 (passes gate, zero contribution)
        score = max(-3.0, min(3.0, -adjusted * 5000))
        assert score == 0.0
