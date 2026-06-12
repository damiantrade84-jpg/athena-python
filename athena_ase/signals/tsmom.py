"""Time-series momentum signal (ASE v2.1 §3.1)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from athena_ase.horizon import Horizon, HORIZONS


@dataclass(frozen=True)
class TSMOMResult:
    direction: int
    raw_strength: float
    blend: float


def compute_tsmom(
    close_log: np.ndarray,
    sigma_bar: np.ndarray,
    idx: int,
    horizon: Horizon,
) -> TSMOMResult:
    lookbacks = HORIZONS[horizon].tsmom_lookbacks
    p_t = float(close_log[idx])
    sig1 = float(sigma_bar[idx])
    if math.isnan(sig1) or sig1 <= 0:
        return TSMOMResult(0, 0.0, 0.0)

    num = 0.0
    den = 0.0
    for lb in lookbacks:
        if idx - lb < 0:
            continue
        w = 1.0 / math.sqrt(lb)
        z = (p_t - float(close_log[idx - lb])) / (sig1 * math.sqrt(lb))
        z = max(-3.0, min(3.0, z))
        num += w * z
        den += w
    if den <= 0:
        return TSMOMResult(0, 0.0, 0.0)
    blend = num / den
    if abs(blend) < 0.5:
        direction = 0
    else:
        direction = 1 if blend > 0 else -1
    raw_strength = min(abs(blend) / 3.0, 1.0)
    return TSMOMResult(direction, raw_strength, blend)
