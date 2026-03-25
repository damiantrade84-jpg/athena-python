"""Candle and style-level orchestration service."""

from __future__ import annotations

from typing import Callable, Dict, Any

from config import scan_candle_limits


def _resolve_regime_state(sig: Dict[str, Any]) -> int | None:
    regime = sig.get("regime")
    if isinstance(regime, dict):
        if regime.get("state") is not None:
            try:
                return int(regime.get("state"))
            except (TypeError, ValueError):
                pass
        regime_label = regime.get("label") or regime.get("regime")
    else:
        regime_label = regime

    if not regime_label:
        return None

    label = str(regime_label).upper()
    mapping = {
        "TRENDING": 0,
        "RANGING": 1,
        "HIGH_VOLATILITY": 2,
        "LOW_VOLATILITY": 3,
    }
    return mapping.get(label)


def recompute_levels_for_style(
    sig: Dict[str, Any],
    pip_mode: str,
    *,
    resolve_pair_from_signal: Callable[[dict], dict | None],
    fetch_candles: Callable[[dict, str, int], list],
    calc_indicators_with_normalized: Callable[[list, str], dict],
    atr_for_levels: Callable[..., float | None],
    calc_levels: Callable[..., dict],
    config: dict,
) -> dict:
    """Recompute SL/TP levels for selected style using fresh ATR context."""
    mode = (pip_mode or "swing").strip().lower()
    if mode not in ("scalp", "intraday", "swing"):
        mode = "swing"

    pair_obj = resolve_pair_from_signal(sig)
    if not pair_obj:
        raise ValueError("Pair not found for quick execute")

    ptype = pair_obj.get("type", "")
    _lim = scan_candle_limits()
    d1 = fetch_candles(pair_obj, "D1", _lim["D1"])
    h4 = fetch_candles(pair_obj, "H4", _lim["H4"])
    h1 = fetch_candles(pair_obj, "H1", _lim["H1"])
    if not d1 or not h4 or not h1:
        raise ValueError("Candles unavailable")

    # Match analyze_pair() indicator hygiene: ignore still-forming bars.
    d1 = d1[:-1] if len(d1) > 1 else d1
    h4 = h4[:-1] if len(h4) > 1 else h4
    h1 = h1[:-1] if len(h1) > 1 else h1

    d1i = calc_indicators_with_normalized(d1, ptype) if d1 else {}
    h4i = calc_indicators_with_normalized(h4, ptype) if h4 else {}
    h1i = calc_indicators_with_normalized(h1, ptype) if h1 else {}

    exec_atr = atr_for_levels(d1i, h4i, h1i, pair=pair_obj, style=mode)
    if not exec_atr or exec_atr <= 0:
        raise ValueError("ATR unavailable")

    exec_price = float(sig.get("price", 0))
    exec_dir = sig.get("direction", "LONG")
    regime_state = _resolve_regime_state(sig)
    lvl = calc_levels(
        exec_price,
        exec_atr,
        exec_dir,
        ptype,
        regime_state=regime_state,
        style=mode,
    )
    return {
        "pip_mode": mode,
        "atr": exec_atr,
        "levels": lvl,
    }

