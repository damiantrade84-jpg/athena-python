"""Candle and style-level orchestration service."""

from __future__ import annotations

from typing import Callable, Dict, Any

from config import scan_candle_limits


def engine_a_scoring_candles_from_state(
    pair: Dict[str, Any] | None,
    state: Dict[str, Any] | None,
    fallback: list | None = None,
) -> list:
    """Return closed candles for Engine A scoring from a market-state payload."""
    if isinstance(state, dict):
        return list(state.get("confirmed") or [])
    return list(fallback or [])


def _trim_unconfirmed_tail(candles: list | None) -> list:
    """Drop trailing candles explicitly marked unconfirmed (forming bar).

    Mirrors data_feeds.trim_unconfirmed_tail_candles: only rows with
    ``confirmed is False`` are removed; unmarked rows are treated as
    confirmed (legacy providers).
    """
    out = list(candles or [])
    while out and isinstance(out[-1], dict) and out[-1].get("confirmed") is False:
        out.pop()
    return out


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
    get_pair_level_atr_class: Callable[[dict], str] | None = None,
    bybit_atr_for_levels: Callable[..., float | None] | None = None,
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

    # Confirmed-only ATR/levels: strip provider-marked forming tail bars so the
    # quick-exec ATR matches the confirmed-only policy used for scoring
    # indicators (same semantics as data_feeds.trim_unconfirmed_tail_candles;
    # kept local to avoid the heavyweight data_feeds import). The crypto Bybit
    # levels feed is unaffected: its ATR is computed upstream from
    # confirmed-only bars and overrides this value below.
    d1 = _trim_unconfirmed_tail(d1)
    h4 = _trim_unconfirmed_tail(h4)
    h1 = _trim_unconfirmed_tail(h1)
    if not d1 or not h4 or not h1:
        raise ValueError("Candles unavailable")

    d1i = calc_indicators_with_normalized(d1, ptype) if d1 else {}
    h4i = calc_indicators_with_normalized(h4, ptype) if h4 else {}
    h1i = calc_indicators_with_normalized(h1, ptype) if h1 else {}

    exec_atr = atr_for_levels(d1i, h4i, h1i, pair=pair_obj, style=mode)
    if (
        str(ptype).lower() == "crypto"
        and str(config.get("ENGINE_A_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
    ):
        bybit_atr = (
            bybit_atr_for_levels(pair_obj, mode)
            if callable(bybit_atr_for_levels)
            else None
        )
        if bybit_atr:
            exec_atr = bybit_atr
        elif not bool(config.get("ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
            raise ValueError("Bybit ATR unavailable")
    if not exec_atr or exec_atr <= 0:
        raise ValueError("ATR unavailable")

    exec_price = float(sig.get("price") or 0.0)
    exec_dir = sig.get("direction", "LONG")
    regime_state = _resolve_regime_state(sig)
    level_atr_class = (
        get_pair_level_atr_class(pair_obj)
        if callable(get_pair_level_atr_class)
        else ptype
    )
    lvl = calc_levels(
        exec_price,
        exec_atr,
        exec_dir,
        level_atr_class,
        regime_state=regime_state,
        style=mode,
    )
    return {
        "pip_mode": mode,
        "atr": exec_atr,
        "levels": lvl,
    }

