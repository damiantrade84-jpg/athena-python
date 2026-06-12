"""TSMOM signal formula tests."""

from __future__ import annotations

import math

import numpy as np
import pytest

from athena_ase.signals.tsmom import compute_tsmom


def test_tsmom_hand_computed_blend():
    n = 200
    close_log = np.linspace(0.0, 0.5, n)
    sigma = np.full(n, 0.01)
    idx = 180
    res = compute_tsmom(close_log, sigma, idx, "intraday")
    p_t = close_log[idx]
    sig = sigma[idx]
    z24 = (p_t - close_log[idx - 24]) / (sig * math.sqrt(24))
    z72 = (p_t - close_log[idx - 72]) / (sig * math.sqrt(72))
    z168 = (p_t - close_log[idx - 168]) / (sig * math.sqrt(168))
    w24, w72, w168 = 1 / math.sqrt(24), 1 / math.sqrt(72), 1 / math.sqrt(168)
    blend = (
        w24 * max(-3, min(3, z24))
        + w72 * max(-3, min(3, z72))
        + w168 * max(-3, min(3, z168))
    ) / (w24 + w72 + w168)
    assert res.blend == pytest.approx(blend, rel=1e-9)
    if abs(blend) < 0.5:
        expected_dir = 0
    else:
        expected_dir = 1 if blend > 0 else -1
    assert res.direction == expected_dir
    assert res.raw_strength == pytest.approx(min(abs(blend) / 3.0, 1.0))
