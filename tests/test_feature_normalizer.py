"""Parity tests for feature_normalizer hot-path helpers."""

import os
import sys
from statistics import mean, stdev

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from feature_normalizer import percentile_rank, zscore_normalize


def _naive_percentile_rank(series, window):
    out = [None] * len(series)
    if window <= 0:
        return out
    for i in range(window - 1, len(series)):
        window_vals = [v for v in series[i - window + 1 : i + 1] if v is not None]
        if len(window_vals) == window:
            sorted_vals = sorted(window_vals)
            rank = sum(1 for v in sorted_vals if v < series[i])
            out[i] = rank / window
    return out


def test_percentile_rank_matches_previous_behavior_on_representative_series():
    series = [10, 11, 13, 13, 12, 15, 14, 16, 16, 17, 19]
    assert percentile_rank(series, 5) == _naive_percentile_rank(series, 5)


def test_percentile_rank_preserves_none_and_warmup_semantics():
    series = [1, None, 2, 3, 4, 5, None, 6, 7]
    assert percentile_rank(series, 3) == _naive_percentile_rank(series, 3)


def test_zscore_normalize_current_window_contract_is_explicit():
    series = [1, 2, 3, 4, 5, 100]
    window = 5

    actual = zscore_normalize(series, window, clamp=False)[-1]
    current_window = series[-window:]
    prior_window = series[-window - 1 : -1]

    assert actual == pytest.approx(
        (series[-1] - mean(current_window)) / stdev(current_window)
    )
    assert actual != pytest.approx(
        (series[-1] - mean(prior_window)) / stdev(prior_window)
    )
