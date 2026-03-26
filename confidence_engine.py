"""confidence_engine.py — Signal confidence scoring independent from signal score.

Computes confidence from four components with graceful degradation:
when a component has no data, it is excluded and weights are redistributed.

Components:
  1. indicator_agreement  — variance within each factor's indicators
  2. timeframe_alignment  — consistency of factor scores across D1/H4/H1
  3. regime_fit            — how well signal type matches detected regime
  4. liquidity_quality     — normalized volume, orderflow, spread metrics
"""

import math
import logging
from typing import Dict, Optional, List

log = logging.getLogger("sentinel")

# Default component weights (must sum to 1.0)
_DEFAULT_WEIGHTS = {
    "indicator_agreement": 0.40,  # increased from 0.35
    "timeframe_alignment": 0.15,  # reduced from 0.30
    "regime_fit": 0.30,  # increased from 0.20
    "liquidity_quality": 0.15,
}

# Mirror the live Engine A factor taxonomy for indicator-agreement checks.
# Missing keys are ignored at runtime, so listing all supported indicators is safe.
_DEFAULT_FACTOR_MAP = {
    "trend": ["ema_trend", "h4_ema_trend", "d1_ema_trend"],
    "momentum": ["rsi_z", "macdLine_z"],
    "derivatives": ["funding_rate", "cot_z", "carry_z"],
    "microstructure": [
        "order_book_imbalance",
        "liquidity_wall_detection",
        "orderflow_delta",
        "liquidity_pressure",
        "volume_momentum_spread",
    ],
    "trend_strength": ["adx_z"],
    "volatility": ["atr_z", "bbWidth_z", "realized_vol_z"],
    "volume": ["volume_ratio", "obv_trend"],
    "structure": ["fib_proximity"],
}

# Regime-signal affinity matrix: regime → signal_type → fit score
_REGIME_FIT_MATRIX = {
    "TRENDING": {"trend": 1.0, "momentum": 0.8, "mean_reversion": 0.3, "breakout": 0.7},
    "RANGING": {"trend": 0.3, "momentum": 0.6, "mean_reversion": 1.0, "breakout": 0.5},
    "HIGH_VOLATILITY": {
        "trend": 0.5,
        "momentum": 0.6,
        "mean_reversion": 0.4,
        "breakout": 1.0,
    },
    "LOW_VOLATILITY": {
        "trend": 0.6,
        "momentum": 0.4,
        "mean_reversion": 0.7,
        "breakout": 0.3,
    },
}


def _std(values: List[float]) -> float:
    """Sample standard deviation of a list of floats."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _mad(values: List[float]) -> float:
    """Median Absolute Deviation - robust measure of dispersion."""
    if len(values) < 2:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    median = (
        sorted_vals[n // 2]
        if n % 2 == 1
        else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    )
    mad = (
        sorted(abs(v - median) for v in values)[n // 2]
        if n % 2 == 1
        else sorted(abs(v - median) for v in values)[n // 2 - 1]
    )
    return mad


def _robust_z_score_normalization(mean_mad: float) -> float:
    """Normalize using robust Z-score approach with graceful scaling for extreme values.
    Uses 0.6745 constant to convert MAD to standard deviation equivalent.
    """
    if mean_mad <= 0:
        return 1.0  # perfect agreement

    # Convert MAD to standard deviation equivalent
    std_equiv = mean_mad / 0.6745

    # Robust Z-score scaling with graceful handling of extreme values
    # Instead of hard-clamping at 3.0, use soft scaling that preserves information
    robust_z = 1.0 / (1.0 + std_equiv)

    # Apply additional scaling for extreme volatility spikes (>3.0 std equiv)
    if std_equiv > 3.0:
        # Graceful degradation for extreme macroeconomic volatility
        extreme_factor = 1.0 / (1.0 + (std_equiv - 3.0) * 0.1)
        robust_z *= extreme_factor

    return max(0.0, min(1.0, robust_z))


def indicator_agreement(
    factor_scores: Dict[str, Optional[float]],
    filtered_indicators: Dict[str, Optional[float]],
    factor_map: Dict[str, List[str]],
) -> Optional[float]:
    """Measure variance between normalized indicator signals within each factor using robust MAD.
    agreement = robust_z_score_normalization(mean_mad). Returns 0-1 or None if no data."""
    mads = []
    for factor, keys in factor_map.items():
        vals = [
            filtered_indicators.get(k)
            for k in keys
            if filtered_indicators.get(k) is not None
        ]
        if len(vals) >= 2:
            mads.append(_mad(vals))
        elif len(vals) == 1:
            mads.append(0.0)  # single indicator = perfect agreement
    if not mads:
        return None
    mean_mad = sum(mads) / len(mads)
    # Use robust Z-score normalization instead of hard-clamping
    return _robust_z_score_normalization(mean_mad)


def timeframe_alignment(
    d1_factor_result: Optional[Dict],
    h4_factor_result: Optional[Dict],
    h1_factor_result: Optional[Dict],
) -> Optional[float]:
    """Compare factor scores across timeframes.
    alignment = 1 - std(factor_scores_across_timeframes). Returns 0-1 or None."""
    scores = []
    for result in [d1_factor_result, h4_factor_result, h1_factor_result]:
        if result is not None and result.get("final_score") is not None:
            scores.append(result["final_score"])
    if len(scores) < 2:
        return None
    std_val = _std(scores)
    # Normalize: typical score range is roughly [-10, 10]
    return max(0.0, min(1.0, 1.0 - std_val / 2.0))


def regime_fit(regime: str, signal_type: str = "trend") -> Optional[float]:
    """If detected regime supports the signal type, return high score.
    Returns 0-1 or None if regime is unknown."""
    regime_upper = (regime or "").upper()
    if regime_upper not in _REGIME_FIT_MATRIX:
        return None
    return _REGIME_FIT_MATRIX[regime_upper].get(signal_type, 0.5)


def liquidity_quality(
    volume_ratio: Optional[float],
    microstructure: Optional[Dict] = None,
    session_vol_multiplier: float = 1.0,
) -> Optional[float]:
    """Use normalized volume and session-based volatility multiplier.
    Forex is decentralized, so we rely on volume_ratio and session timing.
    Returns 0-1 or None if no data available."""
    components = []
    # Volume ratio: >1.0 means above-average volume
    if volume_ratio is not None and volume_ratio > 0:
        vol_score = min(1.0, volume_ratio / 2.0)  # 2x average = 1.0
        # Apply session-based volatility multiplier
        vol_score = min(1.0, vol_score * session_vol_multiplier)
        components.append(vol_score)
    # Microstructure signals (excluding order book metrics for Forex)
    if microstructure:
        wall = microstructure.get("liquidity_wall_detection")
        pressure = microstructure.get("liquidity_pressure")
        if wall is not None and wall != 0.0:
            components.append(min(1.0, abs(wall)))
        if pressure is not None and pressure != 0.0:
            components.append(min(1.0, abs(pressure)))
    if not components:
        return None
    return sum(components) / len(components)


def compute_confidence(
    factor_result: Dict,
    d1_factor_result: Optional[Dict] = None,
    h4_factor_result: Optional[Dict] = None,
    h1_factor_result: Optional[Dict] = None,
    signal_type: str = "trend",
    volume_ratio: Optional[float] = None,
    microstructure: Optional[Dict] = None,
    session_vol_multiplier: float = 1.0,
    factor_map: Optional[Dict] = None,
) -> Dict:
    """Compute signal confidence with graceful degradation.

    When a component has no data, it is excluded and weights are
    redistributed among available components.

    Returns dict with confidence (0-1), component scores, and diagnostics.
    """
    if factor_map is None:
        factor_map = _DEFAULT_FACTOR_MAP

    regime = factor_result.get("regime", "UNKNOWN")
    filtered = factor_result.get("filtered_indicators", {})

    # Compute each component (None = unavailable)
    components = {
        "indicator_agreement": indicator_agreement(
            factor_result.get("factor_scores", {}), filtered, factor_map
        ),
        "timeframe_alignment": timeframe_alignment(
            d1_factor_result, h4_factor_result, h1_factor_result
        ),
        "regime_fit": regime_fit(regime, signal_type),
        "liquidity_quality": liquidity_quality(
            volume_ratio, microstructure, session_vol_multiplier
        ),
    }

    # Redistribute weights among available components
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return {
            "confidence": 0.0,
            "components": components,
            "available_count": 0,
            "degraded": True,
        }

    # Redistribute: scale weights of available components to sum to 1.0
    raw_weight_sum = sum(_DEFAULT_WEIGHTS[k] for k in available)
    final = 0.0
    used_weights = {}
    for name, score in available.items():
        w = _DEFAULT_WEIGHTS[name] / raw_weight_sum
        used_weights[name] = round(w, 3)
        final += w * score

    confidence = max(0.0, min(1.0, final))

    return {
        "confidence": round(confidence, 4),
        "components": components,
        "weights_used": used_weights,
        "available_count": len(available),
        "degraded": len(available) < len(_DEFAULT_WEIGHTS),
    }
