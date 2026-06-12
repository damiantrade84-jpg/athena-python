"""FX short-horizon mean reversion — intraday forex only (ASE v2.1 §3.4)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from athena_ase.instruments import Instrument


@dataclass(frozen=True)
class MeanRevResult:
    direction: int
    raw_strength: float
    z: float


def compute_meanrev(
    instrument: Instrument,
    close_log: np.ndarray,
    idx: int,
    tsmom_blend: float,
    *,
    window: int = 20,
) -> MeanRevResult:
    if instrument.family != "forex" or idx + 1 < window:
        return MeanRevResult(0, 0.0, 0.0)
    segment = close_log[idx - window + 1 : idx + 1]
    m = float(np.mean(segment))
    s = float(np.std(segment, ddof=0))
    if s <= 0 or math.isnan(s):
        return MeanRevResult(0, 0.0, 0.0)
    z = (float(close_log[idx]) - m) / s
    if z >= 2.0:
        direction = -1
    elif z <= -2.0:
        direction = 1
    else:
        return MeanRevResult(0, 0.0, z)
    raw_strength = min((abs(z) - 2.0) / 1.5, 1.0)
    if direction != 0 and tsmom_blend != 0:
        if direction == -int(math.copysign(1, tsmom_blend)) and abs(tsmom_blend) >= 1.5:
            return MeanRevResult(0, 0.0, z)
    return MeanRevResult(direction, raw_strength, z)
