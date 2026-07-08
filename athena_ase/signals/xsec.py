"""Cross-sectional momentum — swing, equity/crypto/index_etf (ASE v2.1 §3.3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from athena_ase.horizon import HORIZONS
from athena_ase.instruments import Instrument
from athena_ase.settings import xsec_continuous_enabled, xsec_z_cap
from athena_ase.signals.common import BarSeries, bar_index_at_decision

log = logging.getLogger("ase.signals.xsec")

_MIN_UNIVERSE = 10
_xsec_disable_logged: set[str] = set()


@dataclass(frozen=True)
class XSecResult:
    direction: int
    raw_strength: float
    pct: float
    disabled: bool = False


def _r63(close_log: np.ndarray, sigma_bar: np.ndarray, idx: int, lookback: int = 63) -> float | None:
    if idx - lookback < 0:
        return None
    sig = float(sigma_bar[idx])
    if np.isnan(sig) or sig <= 0:
        return None
    ret = float(close_log[idx] - close_log[idx - lookback])
    return ret / (sig * np.sqrt(lookback))


def _percentile_direction(pct: float) -> int:
    if pct >= 0.8:
        return 1
    if pct <= 0.2:
        return -1
    return 0


def compute_xsec(
    instrument: Instrument,
    family_series: dict[str, BarSeries],
    decision_time_ms: int,
    horizon: str,
) -> XSecResult:
    if instrument.family not in ("equity", "crypto", "index_etf"):
        return XSecResult(0, 0.0, 0.5)
    lookback = HORIZONS["swing"].xsec_lookback
    all_scores: list[float] = []
    for series in family_series.values():
        i = bar_index_at_decision(series, decision_time_ms)
        if i is None:
            continue
        r = _r63(series.close_log, series.sigma_bar, i, lookback)
        if r is not None:
            all_scores.append(r)

    if len(all_scores) < _MIN_UNIVERSE:
        day_key = f"{instrument.family}:{decision_time_ms // 86_400_000}"
        if day_key not in _xsec_disable_logged:
            _xsec_disable_logged.add(day_key)
            log.info(
                "xsec disabled: family=%s universe=%d<%d at %s",
                instrument.family,
                len(all_scores),
                _MIN_UNIVERSE,
                decision_time_ms,
            )
        return XSecResult(0, 0.0, 0.5, disabled=True)

    target = family_series.get(instrument.symbol)
    if target is None:
        return XSecResult(0, 0.0, 0.5)
    idx = bar_index_at_decision(target, decision_time_ms)
    if idx is None:
        return XSecResult(0, 0.0, 0.5)

    own = _r63(target.close_log, target.sigma_bar, idx, lookback)
    if own is None:
        return XSecResult(0, 0.0, 0.5)

    less = sum(1 for s in all_scores if s < own)
    pct = less / (len(all_scores) - 1) if len(all_scores) > 1 else 0.5

    if xsec_continuous_enabled():
        arr = np.asarray(all_scores, dtype=float)
        mu = float(np.mean(arr))
        sd = float(np.std(arr))
        if sd <= 1e-12:
            return XSecResult(0, 0.0, pct)
        z = (own - mu) / sd
        direction = 1 if z > 0 else -1 if z < 0 else 0
        cap = max(xsec_z_cap(), 1e-6)
        raw_strength = min(abs(z) / cap, 1.0)
        if abs(z) < 0.25:
            direction = 0
            raw_strength = 0.0
        return XSecResult(direction, raw_strength, pct)

    direction = _percentile_direction(pct)
    raw_strength = abs(pct - 0.5) * 2.0
    return XSecResult(direction, raw_strength, pct)
