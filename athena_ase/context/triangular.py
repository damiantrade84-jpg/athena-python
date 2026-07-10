"""Triangular consistency diagnostic for FX crosses (WO Phase 2) — shadow only.

For a cross like GBPJPY, decompose into USD legs (GBPUSD, USDJPY), compute
each leg's tsmom blend at the as-of bar, and label whether the proposed cross
direction is consistent with the implied leg directions. Never affects
decisionStatus, direction, or sizing.
"""

from __future__ import annotations

import math

from athena_ase.data.ptis import PTISStore
from athena_ase.horizon import Horizon
from athena_ase.instruments import DEFAULT_INSTRUMENTS
from athena_ase.signals.common import bar_index_at_decision, load_bar_series
from athena_ase.signals.tsmom import compute_tsmom

_LOOKBACK_MS = 400 * 86_400_000
_FLAT_BLEND = 0.5

CONSISTENT = "CONSISTENT"
PARTIALLY_CONSISTENT = "PARTIALLY_CONSISTENT"
CONTRADICTORY = "CONTRADICTORY"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def _fx_symbols() -> set[str]:
    return {i.symbol for i in DEFAULT_INSTRUMENTS if i.family == "forex"}


def _usd_leg(ccy: str, universe: set[str]) -> tuple[str, int] | None:
    """USD leg symbol and its sign as USD-relative strength of `ccy`.

    sign +1: pair up ⇒ ccy strengthening vs USD (ccy is base, e.g. GBPUSD).
    sign -1: pair up ⇒ ccy weakening vs USD (ccy is quote, e.g. USDJPY).
    """
    if ccy == "USD":
        return None
    direct = f"{ccy}USD"
    inverse = f"USD{ccy}"
    if direct in universe:
        return direct, 1
    if inverse in universe:
        return inverse, -1
    return None


def cross_legs(symbol: str) -> tuple[tuple[str, int], tuple[str, int]] | None:
    """(base_leg, quote_leg) with USD-strength signs, or None for non-crosses."""
    sym = symbol.upper().replace("/", "")
    if len(sym) < 6:
        return None
    base, quote = sym[:3], sym[3:6]
    if "USD" in (base, quote):
        return None
    universe = _fx_symbols()
    base_leg = _usd_leg(base, universe)
    quote_leg = _usd_leg(quote, universe)
    if base_leg is None or quote_leg is None:
        return None
    return base_leg, quote_leg


def _leg_strength(
    store: PTISStore,
    symbol: str,
    sign: int,
    decision_time_ms: int,
    horizon: Horizon,
) -> float | None:
    """Currency's USD-relative trend strength via the leg's tsmom blend."""
    try:
        series = load_bar_series(
            store, symbol, horizon, decision_time_ms - _LOOKBACK_MS, decision_time_ms
        )
    except Exception:  # noqa: BLE001 — diagnostics must never break the scan
        return None
    if series is None:
        return None
    idx = bar_index_at_decision(series, decision_time_ms)
    if idx is None:
        return None
    blend = float(compute_tsmom(series.close_log, series.sigma_bar, idx, horizon).blend)
    if math.isnan(blend):
        return None
    return sign * blend


def triangular_label(
    store: PTISStore,
    symbol: str,
    direction: int,
    decision_time_ms: int,
    horizon: Horizon,
) -> dict[str, object] | None:
    """Consistency label for a proposed cross trade; None for non-crosses."""
    legs = cross_legs(symbol)
    if legs is None:
        return None
    (base_sym, base_sign), (quote_sym, quote_sign) = legs
    d = 1 if direction > 0 else -1 if direction < 0 else 0
    base_strength = _leg_strength(store, base_sym, base_sign, decision_time_ms, horizon)
    quote_strength = _leg_strength(store, quote_sym, quote_sign, decision_time_ms, horizon)
    legs_out = {
        "base": {"symbol": base_sym, "strength": base_strength},
        "quote": {"symbol": quote_sym, "strength": quote_strength},
    }
    if base_strength is None or quote_strength is None or d == 0:
        return {"label": INSUFFICIENT_DATA, "legs": legs_out}

    # Cross up pressure = base strengthening vs USD and/or quote weakening.
    support_base = d * base_strength
    support_quote = d * (-quote_strength)
    implied = base_strength - quote_strength

    if implied * d < 0 and abs(implied) >= _FLAT_BLEND:
        label = CONTRADICTORY
    elif support_base >= _FLAT_BLEND and support_quote >= _FLAT_BLEND:
        label = CONSISTENT
    elif min(support_base, support_quote) <= -_FLAT_BLEND:
        label = CONTRADICTORY
    else:
        label = PARTIALLY_CONSISTENT
    return {"label": label, "legs": legs_out, "implied": implied}
