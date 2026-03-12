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
from config import CONFIG

log = logging.getLogger("sentinel")

# Default component weights (must sum to 1.0)
_DEFAULT_WEIGHTS = {
    "indicator_agreement": 0.35,
    "timeframe_alignment": 0.30,
    "regime_fit": 0.20,
    "liquidity_quality": 0.15,
}

# Regime-signal affinity matrix: regime → signal_type → fit score
_REGIME_FIT_MATRIX = {
    "TRENDING":        {"trend": 1.0, "momentum": 0.8, "mean_reversion": 0.3, "breakout": 0.7},
    "RANGING":         {"trend": 0.3, "momentum": 0.6, "mean_reversion": 1.0, "breakout": 0.5},
    "HIGH_VOLATILITY": {"trend": 0.5, "momentum": 0.6, "mean_reversion": 0.4, "breakout": 1.0},
    "LOW_VOLATILITY":  {"trend": 0.6, "momentum": 0.4, "mean_reversion": 0.7, "breakout": 0.3},
}


def _std(values: List[float]) -> float:
    """Sample standard deviation of a list of floats."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def indicator_agreement(factor_scores: Dict[str, Optional[float]],
                        filtered_indicators: Dict[str, Optional[float]],
                        factor_map: Dict[str, List[str]]) -> Optional[float]:
    """Measure variance between normalized indicator signals within each factor.
    agreement = 1 - mean(std per factor). Returns 0-1 or None if no data."""
    stds = []
    for factor, keys in factor_map.items():
        vals = [filtered_indicators.get(k) for k in keys
                if filtered_indicators.get(k) is not None]
        if len(vals) >= 2:
            stds.append(_std(vals))
        elif len(vals) == 1:
            stds.append(0.0)  # single indicator = perfect agreement
    if not stds:
        return None
    mean_std = sum(stds) / len(stds)
    # Clamp: z-scores are in [-3,3] so max std is ~3; normalize to 0-1
    return max(0.0, min(1.0, 1.0 - mean_std / 3.0))


def timeframe_alignment(d1_factor_result: Optional[Dict],
                        h4_factor_result: Optional[Dict],
                        h1_factor_result: Optional[Dict]) -> Optional[float]:
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
    return max(0.0, min(1.0, 1.0 - std_val / 10.0))


def regime_fit(regime: str, signal_type: str = "trend") -> Optional[float]:
    """If detected regime supports the signal type, return high score.
    Returns 0-1 or None if regime is unknown."""
    regime_upper = (regime or "").upper()
    if regime_upper not in _REGIME_FIT_MATRIX:
        return None
    return _REGIME_FIT_MATRIX[regime_upper].get(signal_type, 0.5)


def liquidity_quality(volume_ratio: Optional[float],
                      microstructure: Optional[Dict] = None) -> Optional[float]:
    """Use normalized volume, orderflow, and spread metrics.
    Returns 0-1 or None if no data available."""
    components = []
    # Volume ratio: >1.0 means above-average volume
    if volume_ratio is not None and volume_ratio > 0:
        vol_score = min(1.0, volume_ratio / 2.0)  # 2x average = 1.0
        components.append(vol_score)
    # Microstructure signals (if available)
    if microstructure:
        imb = microstructure.get("order_book_imbalance")
        delta = microstructure.get("orderflow_delta")
        if imb is not None and imb != 0.0:
            # Higher absolute imbalance = clearer directional liquidity
            components.append(min(1.0, abs(imb)))
        if delta is not None and delta != 0.0:
            components.append(min(1.0, abs(delta)))
    if not components:
        return None
    return sum(components) / len(components)


def compute_confidence(factor_result: Dict,
                       d1_factor_result: Optional[Dict] = None,
                       h4_factor_result: Optional[Dict] = None,
                       h1_factor_result: Optional[Dict] = None,
                       signal_type: str = "trend",
                       volume_ratio: Optional[float] = None,
                       microstructure: Optional[Dict] = None,
                       factor_map: Optional[Dict] = None) -> Dict:
    """Compute signal confidence with graceful degradation.

    When a component has no data, it is excluded and weights are
    redistributed among available components.

    Returns dict with confidence (0-1), component scores, and diagnostics.
    """
    if factor_map is None:
        factor_map = {
            "trend": ["ema_trend", "adx_z"],
            "momentum": ["rsi_z", "macdLine_z"],
            "volatility": ["atr_z", "bbWidth_z", "realized_vol_z"],
            "volume": ["volume_ratio", "obv_trend"],
            "structure": ["fib_proximity"],
            "derivatives": ["funding_rate"],
            "microstructure": ["order_book_imbalance", "liquidity_wall_detection",
                               "orderflow_delta", "liquidity_pressure"],
        }

    regime = factor_result.get("regime", "UNKNOWN")
    filtered = factor_result.get("filtered_indicators", {})

    # Compute each component (None = unavailable)
    components = {
        "indicator_agreement": indicator_agreement(
            factor_result.get("factor_scores", {}), filtered, factor_map),
        "timeframe_alignment": timeframe_alignment(
            d1_factor_result, h4_factor_result, h1_factor_result),
        "regime_fit": regime_fit(regime, signal_type),
        "liquidity_quality": liquidity_quality(volume_ratio, microstructure),
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
