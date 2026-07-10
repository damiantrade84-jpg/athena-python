"""Currency-state shadow layer (WO Phase 1) — diagnostic only.

Reuses ASE's existing carry (FRED as-of rates) and tsmom outputs to summarize
each currency's carry level and aggregate trend, then expresses a per-pair
bias. HARD RULE: shadow only — no confluence vote, no gate, no score change,
no sizing input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from athena_ase.data.ptis import PTISStore
from athena_ase.horizon import Horizon
from athena_ase.instruments import DEFAULT_INSTRUMENTS
from athena_ase.signals.carry import FOREX_CARRY_LEGS, fred_rate_asof
from athena_ase.signals.common import bar_index_at_decision, load_bar_series
from athena_ase.signals.tsmom import compute_tsmom

# Derived from the carry-leg table so the currency list has one home.
CURRENCIES: tuple[str, ...] = tuple(
    sorted({ccy for legs in FOREX_CARRY_LEGS.values() for _, ccy in legs})
)

_LOOKBACK_MS = 400 * 86_400_000

# Per-decision-bucket cache of pair tsmom blends: (bucket, horizon) → {symbol: blend}
_TREND_CACHE: dict[tuple[int, str], dict[str, float | None]] = {}
_TREND_CACHE_MAX = 8


@dataclass(frozen=True)
class CurrencyState:
    carry_rate: float | None
    trend: float
    pairs_used: int
    freshness_ms: int


def _fx_universe() -> list[str]:
    return [i.symbol for i in DEFAULT_INSTRUMENTS if i.family == "forex"]


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _pair_blends(
    store: PTISStore, decision_time_ms: int, horizon: Horizon
) -> dict[str, float | None]:
    """tsmom blend per FX pair at the as-of bar, cached per decision bucket."""
    bucket = decision_time_ms // (3_600_000 if horizon == "intraday" else 86_400_000)
    key = (bucket, str(horizon))
    cached = _TREND_CACHE.get(key)
    if cached is not None:
        return cached
    blends: dict[str, float | None] = {}
    for symbol in _fx_universe():
        blend: float | None = None
        try:
            series = load_bar_series(
                store, symbol, horizon, decision_time_ms - _LOOKBACK_MS, decision_time_ms
            )
            if series is not None:
                idx = bar_index_at_decision(series, decision_time_ms)
                if idx is not None:
                    result = compute_tsmom(series.close_log, series.sigma_bar, idx, horizon)
                    blend = float(result.blend)
        except Exception:  # noqa: BLE001 — diagnostics must never break the scan
            blend = None
        blends[symbol] = blend
    if len(_TREND_CACHE) >= _TREND_CACHE_MAX:
        _TREND_CACHE.clear()
    _TREND_CACHE[key] = blends
    return blends


def compute_currency_states(
    store: PTISStore,
    decision_time_ms: int,
    horizon: Horizon,
) -> dict[str, CurrencyState]:
    blends = _pair_blends(store, decision_time_ms, horizon)
    states: dict[str, CurrencyState] = {}
    for ccy in CURRENCIES:
        total = 0.0
        used = 0
        for symbol, blend in blends.items():
            if blend is None or math.isnan(blend):
                continue
            base, quote = symbol[:3], symbol[3:6]
            if ccy == base:
                total += blend
                used += 1
            elif ccy == quote:
                total -= blend
                used += 1
        states[ccy] = CurrencyState(
            carry_rate=fred_rate_asof(store, ccy, decision_time_ms),
            trend=(total / used) if used else 0.0,
            pairs_used=used,
            freshness_ms=decision_time_ms,
        )
    return states


def pair_context(
    states: dict[str, CurrencyState],
    base: str,
    quote: str,
) -> dict[str, object]:
    """Diagnostic pair bias in [-1, 1]: equal-weight carry + trend, no tuning."""
    b = states.get(base.upper())
    q = states.get(quote.upper())
    if b is None or q is None:
        return {
            "bias": 0.0,
            "carry_diff": None,
            "trend_diff": 0.0,
            "freshness": "degraded",
        }
    trend_diff = _clamp(b.trend - q.trend)
    carry_diff: float | None = None
    if b.carry_rate is not None and q.carry_rate is not None:
        carry_diff = float(b.carry_rate - q.carry_rate)
    if carry_diff is None:
        # Trend-only fallback when a carry leg is missing.
        return {
            "bias": trend_diff,
            "carry_diff": None,
            "trend_diff": trend_diff,
            "freshness": "degraded",
        }
    carry_component = _clamp(carry_diff / 2.0)
    bias = _clamp(0.5 * carry_component + 0.5 * trend_diff)
    freshness = "ok" if (b.pairs_used and q.pairs_used) else "degraded"
    return {
        "bias": bias,
        "carry_diff": carry_diff,
        "trend_diff": trend_diff,
        "freshness": freshness,
    }
