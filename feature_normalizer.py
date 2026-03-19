"""feature_normalizer.py — Rolling normalization utilities for Athena indicators.

Provides z-score, percentile rank, and min-max scaling with configurable lookbacks.
All z-scores are clamped to [-3, +3] to prevent extreme influence.
"""

import math
import logging
from typing import List, Optional
from config import CONFIG

log = logging.getLogger("sentinel")


def _rolling_mean(series: List[float], window: int) -> List[Optional[float]]:
    """Rolling mean with None padding."""
    n = len(series)
    out = [None] * n
    if window <= 0:
        return out
    for i in range(window - 1, n):
        window_vals = [v for v in series[i - window + 1 : i + 1] if v is not None]
        if len(window_vals) == window:
            out[i] = sum(window_vals) / window
    return out


def _rolling_std(series: List[float], window: int) -> List[Optional[float]]:
    """Rolling sample standard deviation with None padding."""
    n = len(series)
    out = [None] * n
    if window <= 0:
        return out
    for i in range(window - 1, n):
        window_vals = [v for v in series[i - window + 1 : i + 1] if v is not None]
        if len(window_vals) == window:
            mean = sum(window_vals) / window
            var = sum((v - mean) ** 2 for v in window_vals) / (window - 1)
            out[i] = math.sqrt(var) if var > 0 else 0.0
    return out


def zscore_normalize(series: List[float], window: int) -> List[Optional[float]]:
    """Rolling z-score with window; clamped to [-3, +3]. Returns None until window is full."""
    if len(series) < window:
        return [None] * len(series)
    means = _rolling_mean(series, window)
    stds = _rolling_std(series, window)
    out = []
    for val, mean_val, std_val in zip(series, means, stds):
        if val is None or mean_val is None or std_val is None or std_val == 0:
            out.append(None)
        else:
            z = (val - mean_val) / std_val
            z = max(-3.0, min(3.0, z))  # clamp
            out.append(z)
    return out


def percentile_rank(series: List[float], window: int) -> List[Optional[float]]:
    """Rolling percentile rank (0–1) using window; returns None until window is full."""
    n = len(series)
    out = [None] * n
    if window <= 0:
        return out
    for i in range(window - 1, n):
        window_vals = [v for v in series[i - window + 1 : i + 1] if v is not None]
        if len(window_vals) == window:
            sorted_vals = sorted(window_vals)
            rank = sum(1 for v in sorted_vals if v < series[i])
            out[i] = rank / window
    return out


def minmax_scale(series: List[float], window: int) -> List[Optional[float]]:
    """Rolling min-max scaling (0–1) with window; returns None until window is full."""
    n = len(series)
    out = [None] * n
    if window <= 0:
        return out
    for i in range(window - 1, n):
        window_vals = [v for v in series[i - window + 1 : i + 1] if v is not None]
        if len(window_vals) == window:
            min_val = min(window_vals)
            max_val = max(window_vals)
            if max_val == min_val:
                out[i] = 0.5
            else:
                out[i] = (series[i] - min_val) / (max_val - min_val)
    return out


def get_normalization_lookback(asset_type: str) -> int:
    """Helper to fetch per-asset normalization lookback from config."""
    return CONFIG.get("NORMALIZATION_LOOKBACK", {}).get(asset_type, 200)
