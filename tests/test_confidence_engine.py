"""Focused regression tests for confidence-engine factor taxonomy."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from confidence_engine import _DEFAULT_FACTOR_MAP, compute_confidence


def test_default_factor_map_separates_trend_from_trend_strength():
    assert _DEFAULT_FACTOR_MAP["trend"] == [
        "ema_trend",
        "h4_ema_trend",
        "d1_ema_trend",
    ]
    assert _DEFAULT_FACTOR_MAP["trend_strength"] == ["adx_z"]
    assert "adx_z" not in _DEFAULT_FACTOR_MAP["trend"]


def test_compute_confidence_treats_adx_as_trend_strength_not_trend_alignment():
    factor_result = {
        "regime": "TRENDING",
        "factor_scores": {"trend": 1.0, "trend_strength": 0.8},
        "filtered_indicators": {
            "ema_trend": 1.0,
            "h4_ema_trend": 1.0,
            "d1_ema_trend": 1.0,
            "adx_z": -0.8,
        },
    }

    result = compute_confidence(
        factor_result=factor_result,
        d1_factor_result={"final_score": 1.0},
        h4_factor_result={"final_score": 1.0},
        h1_factor_result={"final_score": 1.0},
        signal_type="trend",
    )

    assert result["components"]["indicator_agreement"] == 1.0
    assert result["confidence"] == 1.0


def test_session_quality_low_multiplier_reduces_confidence():
    factor_result = {
        "regime": "TRENDING",
        "factor_scores": {"trend": 1.0},
        "filtered_indicators": {
            "ema_trend": 1.0,
            "h4_ema_trend": 1.0,
            "d1_ema_trend": 1.0,
        },
    }
    high = compute_confidence(
        factor_result=factor_result,
        d1_factor_result={"final_score": 1.0},
        h4_factor_result={"final_score": 1.0},
        h1_factor_result={"final_score": 1.0},
        signal_type="trend",
        session_quality="high",
    )
    low = compute_confidence(
        factor_result=factor_result,
        d1_factor_result={"final_score": 1.0},
        h4_factor_result={"final_score": 1.0},
        h1_factor_result={"final_score": 1.0},
        signal_type="trend",
        session_quality="low",
    )
    assert low["confidence"] < high["confidence"]
