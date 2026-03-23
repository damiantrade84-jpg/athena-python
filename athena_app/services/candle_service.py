"""Candle and style-level orchestration service."""

from __future__ import annotations

from typing import Callable, Dict, Any


def recompute_levels_for_style(
    sig: Dict[str, Any],
    pip_mode: str,
    *,
    resolve_pair_from_signal: Callable[[dict], dict | None],
    fetch_candles: Callable[[dict, str, int], list],
    calc_indicators: Callable[[list], dict],
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
    d1 = fetch_candles(pair_obj, "D1", config.get("D1_CANDLES", 250))
    h4 = fetch_candles(pair_obj, "H4", config.get("H4_CANDLES", 250))
    h1 = fetch_candles(pair_obj, "H1", config.get("H1_CANDLES", 250))
    d1i = calc_indicators(d1) if d1 else {}
    h4i = calc_indicators(h4) if h4 else {}
    h1i = calc_indicators(h1) if h1 else {}

    exec_atr = atr_for_levels(d1i, h4i, h1i, pair=pair_obj, style=mode)
    if not exec_atr or exec_atr <= 0:
        raise ValueError("ATR unavailable")

    exec_price = float(sig.get("price", 0))
    exec_dir = sig.get("direction", "LONG")
    lvl = calc_levels(
        exec_price,
        exec_atr,
        exec_dir,
        ptype,
        regime_state=None,
        style=mode,
    )
    return {
        "pip_mode": mode,
        "atr": exec_atr,
        "levels": lvl,
    }

