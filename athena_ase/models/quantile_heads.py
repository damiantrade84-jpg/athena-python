"""Quantile head helpers and bracket clamps (ASE v2.1 §6, §5)."""

from __future__ import annotations

from typing import Iterable


def fix_quantile_crossing(row: dict[str, float]) -> dict[str, float]:
    keys = sorted(row.keys(), key=lambda k: float(k.replace("q", "")))
    vals = sorted(float(row[k]) for k in keys)
    return {k: v for k, v in zip(keys, vals)}


def clamp_brackets(
    *,
    entry: float,
    direction: int,
    sl: float,
    tp1: float,
    r_unit: float,
) -> tuple[float, float, str | None]:
    if r_unit <= 0:
        return sl, tp1, "invalid_r_unit"
    sl_dist = abs(entry - sl)
    min_sl = 0.5 * r_unit
    max_sl = 1.5 * r_unit
    if sl_dist < min_sl or sl_dist > max_sl:
        clamped = entry - direction * max(min_sl, min(sl_dist, max_sl))
        sl = clamped
        sl_dist = abs(entry - sl)
    tp_dist = abs(tp1 - entry)
    if tp_dist < 0.6 * sl_dist:
        return sl, tp1, "rr_floor_watch"
    return sl, tp1, None
