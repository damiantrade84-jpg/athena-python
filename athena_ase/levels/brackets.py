"""Bracket levels from quantile heads (ASE v2.1 §5)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from athena_ase.horizon import HORIZONS, Horizon


@dataclass(frozen=True)
class BracketLevels:
    entry_reference: float
    entry_zone: tuple[float, float]
    sl: float
    tp1: float
    tp2: float
    max_hold_bars: int
    rr_ok: bool


def _q(quantiles: dict[str, float], key: str, default: float = 0.0) -> float:
    if key in quantiles:
        return float(quantiles[key])
    alt = key.replace("q", "")
    if alt in quantiles:
        return float(quantiles[alt])
    return default


def compute_brackets(
    *,
    direction: int,
    entry_reference: float,
    sigma_bar: float,
    horizon: Horizon,
    mae_q: dict[str, float],
    mfe_q: dict[str, float],
    k_sl: float = 1.0,
) -> BracketLevels:
    cfg = HORIZONS[horizon]
    h = cfg.max_hold_bars
    r_unit = k_sl * sigma_bar * math.sqrt(h)
    if r_unit <= 0 or direction == 0:
        return BracketLevels(entry_reference, (entry_reference, entry_reference), entry_reference, entry_reference, entry_reference, h, False)

    d = direction
    zone_half = 0.25 * sigma_bar * math.sqrt(h) ** 0.5
    if d > 0:
        entry_zone = (entry_reference - zone_half, entry_reference)
    else:
        entry_zone = (entry_reference, entry_reference + zone_half)

    mae50 = max(_q(mae_q, "q50", 0.25), 0.25)
    mae90 = max(_q(mae_q, "q90", mae50), 0.6)
    mfe50 = _q(mfe_q, "q50", 0.5)
    mfe75 = _q(mfe_q, "q75", mfe50)

    sl_dist = max(mae90, 0.6) * r_unit
    sl_dist = min(max(sl_dist, 0.5 * r_unit), 1.5 * r_unit)
    tp1_dist = max(mfe50, 0.0) * r_unit
    tp2_dist = max(mfe75, tp1_dist) * r_unit

    sl = entry_reference - d * sl_dist
    tp1 = entry_reference + d * tp1_dist
    tp2 = entry_reference + d * tp2_dist

    rr_ok = tp1_dist >= 0.6 * sl_dist if sl_dist > 0 else False
    return BracketLevels(
        entry_reference=entry_reference,
        entry_zone=entry_zone,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        max_hold_bars=h,
        rr_ok=rr_ok,
    )
