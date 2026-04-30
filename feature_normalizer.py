"""feature_normalizer.py — Rolling normalization utilities for Athena indicators.

Provides z-score, percentile rank, and min-max scaling with configurable lookbacks.
All z-scores are clamped to [-3, +3] to prevent extreme influence.
"""

import logging
from bisect import bisect_left, insort
import pandas as pd
from typing import List, Optional
from config import CONFIG

log = logging.getLogger("sentinel")


def _rolling_mean(series: List[float], window: int) -> List[Optional[float]]:
    """Rolling mean using pandas for speed. None-safe."""
    if window <= 0 or len(series) < window:
        return [None] * len(series)
    s = pd.Series(series, dtype=float)
    result = s.rolling(window=window, min_periods=window).mean()
    return [None if pd.isna(v) else float(v) for v in result]


def _rolling_std(series: List[float], window: int) -> List[Optional[float]]:
    """Rolling sample std (ddof=1) using pandas for speed. None-safe."""
    if window <= 0 or len(series) < window:
        return [None] * len(series)
    s = pd.Series(series, dtype=float)
    result = s.rolling(window=window, min_periods=window).std(ddof=1)
    return [None if pd.isna(v) else float(v) for v in result]


def zscore_normalize(series: List[float], window: int, clamp: bool = True) -> List[Optional[float]]:
    """Rolling z-score with window. Returns None until window is full.

    Args:
        series: input values
        window: rolling window size
        clamp: if True (default), hard-clamp to [-3, +3] for UI-stable display.
               Set clamp=False for volatility/ML features where tail information
               must be preserved (e.g., 6-sigma events should read ~6, not 3).
    """
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
            if clamp:
                z = max(-3.0, min(3.0, z))
            out.append(z)
    return out


def percentile_rank(series: List[float], window: int) -> List[Optional[float]]:
    """Rolling percentile rank (0–1) using window; returns None until window is full."""
    n = len(series)
    out = [None] * n
    if window <= 0:
        return out

    sorted_window: List[float] = []
    none_count = 0

    for i, val in enumerate(series):
        if val is None:
            none_count += 1
        else:
            insort(sorted_window, val)

        if i >= window:
            old = series[i - window]
            if old is None:
                none_count -= 1
            else:
                old_idx = bisect_left(sorted_window, old)
                if old_idx < len(sorted_window):
                    sorted_window.pop(old_idx)

        if i >= window - 1 and none_count == 0 and len(sorted_window) == window:
            current = series[i]
            out[i] = bisect_left(sorted_window, current) / window
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
