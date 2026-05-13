"""Engine A and Engine B backtest loops (extracted from athena.py).

Uses ``athena_runtime.rt`` (the runtime accessor) for candle fetch and monolith helpers. Call only
after ``set_runtime()`` has run (normal app / CLI load order).
"""
from __future__ import annotations

import bisect
import logging
import math
import os
import random
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import pandas as pd
from telemetry import build_strategy_lab_telemetry

from athena_runtime import rt as _art_rt
from athena_app.services.crypto_signal_feed import (
    fetch_crypto_signal_candles,
    resolve_crypto_signal_feed,
)
from backtest_candle_cache import fetch_backtest_candles, fetch_backtest_eodhd_intraday
from calibration import calibration_report
from config import CONFIG, _json_safe
from indicators import (
    calc_atr,
    calc_fib,
    calc_fib_proximity,
    calc_indicators_with_normalized,
    calc_levels,
    calc_sma,
    calc_stochastic,
    calc_usd_relative_strength_context,
)
from factor_scoring import build_oi_context_for_factor_scoring
from intermarket import (
    apply_confirmation_to_score,
    FOREX_ENGINE_A_MAX_SCORE,
    build_point_in_time_context,
    discover_active_universe,
    prepare_series_store,
)
from scoring import (
    calc_confluence,
    get_score_threshold,
    get_pair_profile,
    get_pair_level_atr_class,
    get_pair_score_group,
    is_trend_state_blocked,
)
from meta_learner import meta_report
from research_metrics import (
    annualized_sharpe_from_r_multiples,
    annualized_sortino_engine_a_from_r_multiples,
    build_research_metrics,
    compute_trades_per_year_from_span,
    enrich_backtest_summary,
    trades_per_year_from_parallel_trade_days,
)
from research_validation import (
    backtest_bar_validation_state,
    build_validation_report,
    normalize_validation_mode,
    temporal_validation_mode,
    volume_threshold_for_backtest,
)
from stability_monitor import record_backtest_summary

from market_structure import (
    engine_b_forex_asian_session_blocks_bar,
    resolve_engine_b_asset_class,
)


def _bt_forex_d1_bar_time(d1_ts: str) -> str:
    """Replace D1 bar midnight UTC timestamp with 13:00 UTC for forex session check.

    D1 bars carry midnight timestamps (00:00 UTC) which always fail the session
    filter (London: 07-17, NY: 12-21). 13:00 UTC is peak London/NY overlap and
    represents the most liquid part of the trading day -- appropriate for a D1
    signal that spans the full day.
    """
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(str(d1_ts).replace("Z", "+00:00"))
        return dt.replace(hour=13, minute=0, second=0, microsecond=0).isoformat()
    except Exception:
        return d1_ts  # fallback: use original if parsing fails


log = logging.getLogger("sentinel")


def _rt():
    return _art_rt()


def _engine_a_structure_first_cfg() -> dict:
    cfg = ((CONFIG.get("ENGINE_A") or {}).get("structure_first_entry") or {})
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "lookback_bars": int(cfg.get("lookback_bars", 5) or 5),
        "require_bos": bool(cfg.get("require_bos", True)),
        "require_choch": bool(cfg.get("require_choch", False)),
    }


def _engine_a_structure_first_entry_passes(
    structure_result: dict | None,
    direction: str | None,
    trigger_candles: list | None,
    cfg: dict | None = None,
) -> tuple[bool, dict]:
    cfg = _engine_a_structure_first_cfg() if cfg is None else dict(cfg or {})
    detail = {"enabled": bool(cfg.get("enabled", False))}
    if not detail["enabled"]:
        return True, detail
    if not isinstance(structure_result, dict):
        detail["reason"] = "missing_structure_result"
        return False, detail
    if direction not in ("LONG", "SHORT"):
        detail["reason"] = "missing_engine_a_direction"
        return False, detail

    structure_direction = structure_result.get("direction")
    if isinstance(structure_direction, str) and structure_direction in ("LONG", "SHORT"):
        if structure_direction != direction:
            detail["reason"] = "structure_direction_mismatch"
            detail["structure_direction"] = structure_direction
            return False, detail

    lookback = max(0, int(cfg.get("lookback_bars", 5) or 5))
    candles = list(trigger_candles or [])
    last_idx = len(candles) - 1

    def _recent(index_value) -> tuple[bool, int | None]:
        try:
            idx = int(index_value)
        except (TypeError, ValueError):
            return False, None
        bars_ago = last_idx - idx
        return 0 <= bars_ago <= lookback, bars_ago

    require_bos = bool(cfg.get("require_bos", True))
    require_choch = bool(cfg.get("require_choch", False))
    stale_bos_seen = False

    if require_bos and bool(structure_result.get("bos_confirmed")):
        bos_data = structure_result.get("bos_data") or {}
        index_key = "bos_bull_bar_index" if direction == "LONG" else "bos_bear_bar_index"
        is_recent, bars_ago = _recent(bos_data.get(index_key))
        detail.update({"bos_confirmed": True, "bos_recent": is_recent, "bos_bars_ago": bars_ago})
        if is_recent:
            return True, detail
        stale_bos_seen = True

    if require_choch and bool(structure_result.get("choch_confirmed")):
        detail.update({"choch_confirmed": True, "choch_recent": True})
        return True, detail

    if stale_bos_seen:
        detail["reason"] = "structure_not_recent"
    else:
        detail["reason"] = "missing_required_structure"
    return False, detail


def _engine_a_structure_first_entry_check(
    *,
    engine,
    pair: dict,
    direction: str | None,
    d1_candles: list,
    h4_candles: list,
    h1_candles: list,
    current_price: float | None,
    atr: float | None,
    regime: str | None,
    style: str,
    d1_snap: dict | None = None,
    h4_snap: dict | None = None,
    cfg: dict | None = None,
) -> tuple[bool, dict]:
    cfg = _engine_a_structure_first_cfg() if cfg is None else cfg
    if not cfg.get("enabled", False):
        return True, {"enabled": False}
    if engine is None:
        return False, {"enabled": True, "reason": "structure_engine_unavailable"}
    if not direction or current_price is None or not atr or atr <= 0:
        return False, {"enabled": True, "reason": "missing_structure_inputs"}
    try:
        structure_result = engine.analyze_structure(
            d1_candles,
            h4_candles,
            h1_candles,
            float(current_price),
            direction,
            float(atr),
            regime=regime or "RANGING",
            asset_type=pair.get("type", ""),
            enable_zone_registry=False,
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            style=style,
            pair=pair,
        )
    except Exception as exc:
        log.debug("[BT] %s structure-first check error: %s", pair.get("display"), exc)
        return False, {"enabled": True, "reason": "structure_engine_error", "error": str(exc)}

    structure_tf = str(structure_result.get("structure_tf") or "").upper()
    if structure_tf == "D1":
        recency_candles = d1_candles
    elif structure_tf == "H1":
        recency_candles = h1_candles
    else:
        recency_candles = h4_candles
    return _engine_a_structure_first_entry_passes(
        structure_result,
        direction,
        trigger_candles=recency_candles,
        cfg=cfg,
    )


def _bt_indicators_from_cache(
    candles: list,
    *,
    end_idx: int,
    window: int,
    asset_type: str,
    cache: dict[tuple[int, int, str], dict],
) -> dict | None:
    """Exact parity cache: compute the same rolling window as legacy path once."""
    if end_idx < 0:
        return None
    key = (end_idx, window, asset_type)
    hit = cache.get(key)
    if hit is not None:
        return hit
    start = max(0, end_idx - window + 1)
    w = candles[start : end_idx + 1]
    if len(w) < 50:
        return None
    out = calc_indicators_with_normalized(w, asset_type)
    cache[key] = out
    return out


def _engine_b_indexed_atr(
    atr_map: dict,
    atr_tf: str,
    *,
    d1_idx: int,
    h4_idx: int,
    h1_idx: int,
    current_price: float,
) -> tuple[float, list, int]:
    """Return Engine B ATR using the index that matches the requested ATR TF."""
    tf = str(atr_tf or "H4").upper()
    atr_full = atr_map.get(tf) or atr_map.get("H4") or []
    if tf == "D1":
        idx = d1_idx
    elif tf == "H1":
        idx = h1_idx
    else:
        idx = h4_idx
    fallback_atr = float(current_price) * 0.01
    atr = atr_full[idx - 1] if idx > 0 and idx <= len(atr_full) else fallback_atr
    atr_list_50 = atr_full[max(0, idx - 50): idx] if idx > 0 else []
    return float(atr or fallback_atr), atr_list_50, idx


def _bt_cached_fetch(
    pair: dict,
    tf: str,
    limit: int,
    fetcher,
    *,
    provider: str,
    min_bars: int | None = None,
):
    return fetch_backtest_candles(
        pair,
        tf,
        limit,
        fetcher,
        provider=provider,
        min_bars=min_bars,
    )


_BINANCE_BT_INTERVALS = {"D1": "1d", "H4": "4h", "H1": "1h"}


def _crypto_bt_signal_candles(
    pair: dict,
    *,
    engine: str,
    tf: str,
    limit: int,
    min_bars: int | None,
):
    """Fetch crypto backtest candles from the configured Engine A/B signal feed."""
    tf_u = str(tf or "").upper()
    feed = resolve_crypto_signal_feed(engine, CONFIG)
    provider = "bybit_linear_kline" if feed == "bybit" else "binance_futures"
    sym = pair["symbol"]

    def _default_fetch(pair_arg: dict, tf_arg: str, limit_arg: int):
        interval = _BINANCE_BT_INTERVALS.get(str(tf_arg or "").upper())
        if interval is None:
            return _rt().fetch_candles(pair_arg, tf_arg, limit_arg)
        if str(tf_arg or "").upper() == "D1":
            return _rt().fetch_binance(sym, interval, limit_arg)
        return _rt().fetch_binance_paginated(sym, interval, limit_arg)

    def _fetch(limit_arg: int):
        result = fetch_crypto_signal_candles(
            pair,
            tf_u,
            limit_arg,
            engine=engine,
            config=CONFIG,
            default_fetch=_default_fetch,
            bybit_fetch=getattr(_rt(), "fetch_bybit_klines", None),
            bybit_paginated_fetch=getattr(_rt(), "fetch_bybit_klines_paginated", None),
        )
        return result.candles

    return _bt_cached_fetch(
        pair,
        tf_u,
        limit,
        _fetch,
        provider=provider,
        min_bars=min_bars,
    )


def _bt_cached_eodhd_intraday(
    pair: dict,
    *,
    days: int = 730,
    h4_limit: int = 4400,
    h1_limit: int = 17600,
):
    return fetch_backtest_eodhd_intraday(
        pair,
        days,
        lambda fetch_days: _rt().fetch_eodhd_intraday_bt(pair, days=fetch_days),
        h4_limit=h4_limit,
        h1_limit=h1_limit,
    )


def _live_base_risk_pct(asset_type: str) -> float:
    """Mirror the live risk_engine base per-asset risk percentages."""
    return {
        "forex": 0.005,
        "crypto": 0.010,
        "stock": 0.010,
        "commodity": 0.010,
        "index": 0.010,
    }.get(asset_type, CONFIG["RISK_PCT"])


def _engine_a_level_atr_for_bt(
    signal_atr: float | None, pair: dict, style: str | None, as_of=None
) -> tuple[float | None, str]:
    """Resolve Engine A backtest ATR for SL/TP levels.

    Binance-sourced crypto backtests default to the ATR from the same OHLCV as
    scoring (``signal``) when ``ENGINE_A_CRYPTO_BT_LEVEL_ATR_USE_SIGNAL_FEED``
    is true, avoiding Binance-vs-Bybit basis drift. Other sources still use
    Bybit when ``ENGINE_A_CRYPTO_LEVELS_FEED`` is ``bybit``.
    """
    p = pair or {}
    if str(p.get("type") or "").lower() == "crypto":
        if (
            str(p.get("source") or "").lower() == "binance"
            and bool(CONFIG.get("ENGINE_A_CRYPTO_BT_LEVEL_ATR_USE_SIGNAL_FEED", True))
        ):
            if signal_atr is not None:
                try:
                    return float(signal_atr), "signal"
                except (TypeError, ValueError):
                    pass
            if not bool(CONFIG.get("ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                return None, "signal_unavailable"
        if str(CONFIG.get("ENGINE_A_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit":
            bybit_atr_for_levels = getattr(_rt(), "bybit_atr_for_levels", None)
            bybit_atr = None
            if callable(bybit_atr_for_levels):
                try:
                    bybit_atr = bybit_atr_for_levels(pair, style, as_of=as_of)
                except TypeError:
                    bybit_atr = None
            if bybit_atr:
                return float(bybit_atr), "bybit"
            if not bool(CONFIG.get("ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                return None, "bybit_unavailable"
    return signal_atr, "signal"


def _engine_b_level_atr_for_bt(
    signal_atr: float | None, pair: dict, style: str | None, *, as_of=None
) -> tuple[float | None, str]:
    """Resolve Engine B backtest ATR for structure/levels using the live level feed contract."""
    p = pair or {}
    if str(p.get("type") or "").lower() == "crypto":
        if (
            str(p.get("source") or "").lower() == "binance"
            and bool(CONFIG.get("ENGINE_B_CRYPTO_BT_LEVEL_ATR_USE_SIGNAL_FEED", True))
        ):
            if signal_atr is not None:
                try:
                    return float(signal_atr), "signal"
                except (TypeError, ValueError):
                    pass
            if not bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                log.warning(
                    "[ENGINE B BT] %s: signal-feed ATR unavailable and "
                    "ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK=false — "
                    "skipping level resolution",
                    p.get("display", "?"),
                )
                return None, "signal_unavailable"
        if str(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit":
            bybit_fn = getattr(_rt(), "bybit_atr_for_levels", None)
            bybit_atr = None
            if callable(bybit_fn):
                try:
                    bybit_atr = bybit_fn(pair, style, as_of=as_of)
                except TypeError:
                    bybit_atr = bybit_fn(pair, style)
            if bybit_atr:
                return float(bybit_atr), "bybit"
            if not bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                log.warning(
                    "[ENGINE B BT] %s: Bybit ATR unavailable and "
                    "ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK=false — "
                    "skipping level resolution",
                    p.get("display", "?"),
                )
                return None, "bybit_unavailable"
    if signal_atr is None:
        return None, "signal"
    try:
        return float(signal_atr), "signal"
    except (TypeError, ValueError):
        return None, "signal"


def _engine_a_bt_gate_note() -> str:
    """Explain Engine A backtest threshold routing for API payload (matches scoring.get_score_threshold).

    Stage 4.2: BT_MIN / BT_MIN_GROUP deleted. Backtest and live use identical
    Engine A threshold resolution from scoring.py.
    """
    sq_on = bool(CONFIG.get("SCAN_QUANTILE_ENABLED", False))
    return (
        "Engine A backtest uses live thresholds "
        "(profile -> pair/group config -> 3-tier fallback). "
        "Backtest/live parity enforced. "
        "Live scan: get_min_confluence_threshold plus optional SCAN_QUANTILE_ENABLED."
        + ("" if sq_on else " SCAN_QUANTILE_ENABLED is off.")
    )


def _bt_btc_bias(d1_window: list, pair: dict) -> str:
    """Derive BTC bias from the D1 window available at this bar.

    Uses EMA21/50/200 alignment on the supplied D1 series — no precompute needed,
    point-in-time correct.  Returns 'neutral' for non-crypto or short windows.
    """
    if pair.get("type") != "crypto":
        return "neutral"
    try:
        from indicators import calc_indicators
        if len(d1_window) < 200:
            return "neutral"
        s = calc_indicators(d1_window)["snap"]
        if s.get("ema21") and s.get("ema50") and s.get("ema200"):
            if s["ema21"] > s["ema50"] > s["ema200"]:
                return "bullish"
            elif s["ema21"] < s["ema50"] < s["ema200"]:
                return "bearish"
    except Exception:
        pass
    return "neutral"


def _bt_gold_macro_context(
    pair: dict,
    cutoff_ts,
    asset_h4_window: list,
    dxy_h4_raw: list | None,
    dxy_h4_times,
) -> dict | None:
    """Point-in-time DXY macro context for XAU/USD backtests."""
    if pair.get("display") != "XAU/USD" or cutoff_ts is None or pd.isna(cutoff_ts):
        return None
    if not asset_h4_window or not dxy_h4_raw or dxy_h4_times is None:
        return None

    dxy_idx = bisect.bisect_left(dxy_h4_times, cutoff_ts)
    dxy_window = dxy_h4_raw[max(0, dxy_idx - 100) : dxy_idx]
    if len(dxy_window) < 6:
        return None

    return calc_usd_relative_strength_context(
        asset_h4_window,
        dxy_window,
        asset_label="XAU",
        proxy_label="DXY",
        tf="H4",
    )


def _bt_crypto_funding_oi_for_bar(
    ptype: str,
    funding_rows: list | None,
    oi_rows: list | None,
    entry_ts,
    prev_bar_ts,
) -> tuple[float | None, dict | None]:
    """Point-in-time funding rate and OI context for crypto Engine A backtests."""
    if ptype != "crypto" or entry_ts is None or pd.isna(entry_ts):
        return None, None
    from data_feeds import build_oi_data_for_divergence, point_in_time_funding_rate

    bar_ms = int(entry_ts.timestamp() * 1000)
    fr = (
        point_in_time_funding_rate(funding_rows or [], bar_ms)
        if funding_rows
        else None
    )
    prev_ms = None
    if prev_bar_ts is not None and not pd.isna(prev_bar_ts):
        prev_ms = int(prev_bar_ts.timestamp() * 1000)
    oi_data = (
        build_oi_data_for_divergence(oi_rows or [], bar_ms, prev_ms)
        if oi_rows
        else None
    )
    return fr, oi_data


_BASE_BACKTEST_SLIP = {
    "forex": 0.0001,
    "crypto": 0.002,
    "commodity": 0.001,
    "stock": 0.001,
    "index": 0.001,
}


def _get_slippage_for_bar(bar: dict, ptype: str) -> float:
    """Deterministic per-bar slippage model shared by all backtest engines."""
    base = _BASE_BACKTEST_SLIP.get(ptype, 0.001)

    if ptype == "forex":
        t = bar.get("time", "")
        try:
            h = int(t[11:13]) if len(t) > 13 else -1
        except (ValueError, IndexError):
            h = -1

        if 0 <= h < 7 or h >= 22:
            return base * 1.8
        if 13 <= h < 16:
            base = base * 0.7

    model = CONFIG.get("BT_SLIPPAGE_MODEL", {}) or {}
    if not bool(model.get("ENABLED", False)):
        return base

    def _float_or_none(value):
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) and out > 0 else None

    price = _float_or_none(bar.get("open", bar.get("close")))
    atr = _float_or_none(bar.get("atr"))
    if atr is None:
        atr = _float_or_none(bar.get("ATR"))
    qty_adv_ratio = _float_or_none(bar.get("qty_adv_ratio"))
    if qty_adv_ratio is None:
        qty = _float_or_none(bar.get("qty"))
        adv = _float_or_none(bar.get("adv"))
        if qty is not None and adv is not None:
            qty_adv_ratio = qty / adv
    if qty_adv_ratio is None:
        qty_adv_ratio = _float_or_none(model.get("DEFAULT_QTY_ADV_RATIO")) or 0.0

    k1 = _float_or_none(model.get("K1_TICK_MULT")) or 1.0
    k2 = _float_or_none(model.get("K2_ATR_IMPACT")) or 0.0
    cap = _float_or_none(model.get("MAX_SLIPPAGE_PCT")) or 0.05
    impact = 0.0
    if price and atr and qty_adv_ratio > 0:
        impact = (atr / price) * k2 * math.sqrt(qty_adv_ratio)
    return min(cap, max(base, (k1 * base) + impact))


def _bar_with_slippage_context(bar: dict, *, atr: float | None = None) -> dict:
    if atr is None:
        return bar
    try:
        atr_f = float(atr)
    except (TypeError, ValueError):
        return bar
    if not math.isfinite(atr_f) or atr_f <= 0:
        return bar
    out = dict(bar)
    out["atr"] = atr_f
    return out


def _bt_transaction_cost_r(entry: float, sl: float, ptype: str) -> float:
    try:
        entry_f = float(entry)
        sl_f = float(sl)
    except (TypeError, ValueError):
        return 0.0
    sl_dist = abs(entry_f - sl_f)
    if sl_dist <= 0:
        return 0.0
    return float(CONFIG.get("FEE_PCT", {}).get(ptype, 0.0004)) * entry_f / sl_dist


def _resolve_barrier_exit(
    bar: dict,
    *,
    direction: str,
    sl: float,
    tp1: float | None = None,
    tp2: float | None = None,
    sl_outcome: str = "SL",
) -> tuple[str | None, bool]:
    """Resolve TP/SL touches on a single OHLC bar.

    When both SL and TP are touched on the same bar, intrabar order cannot
    be determined from OHLC. Use the bar's open-to-level distance as a
    tiebreaker: whichever level the bar opened closer to is assumed hit
    first. This gives an unbiased estimate vs the old always-SL pessimism.
    """
    bar_high = float(bar["high"])
    bar_low = float(bar["low"])

    if direction == "LONG":
        hit_sl = bar_low <= sl
        hit_tp2 = tp2 is not None and bar_high >= tp2
        hit_tp1 = tp1 is not None and bar_high >= tp1
    else:
        hit_sl = bar_high >= sl
        hit_tp2 = tp2 is not None and bar_low <= tp2
        hit_tp1 = tp1 is not None and bar_low <= tp1

    same_bar_both_hit = hit_sl and (hit_tp2 or hit_tp1)
    if same_bar_both_hit:
        _open = float(bar.get("open", (bar_high + bar_low) / 2))
        _tp_ref = tp2 if hit_tp2 else tp1
        if _tp_ref is not None and abs(_open - _tp_ref) < abs(_open - sl):
            return ("TP2" if hit_tp2 else "TP1"), True
        return sl_outcome, True
    if hit_tp2:
        return "TP2", False
    if hit_tp1:
        return "TP1", False
    if hit_sl:
        return sl_outcome, False
    return None, False


def _engine_a_trade_path_diagnostics(
    future_window: list[dict],
    *,
    direction: str,
    entry: float,
    sl: float,
    tp1: float | None = None,
    tp2: float | None = None,
) -> dict:
    """Measure Engine A BT path quality without changing exit behavior."""
    risk = abs(float(entry) - float(sl))
    if risk <= 0 or not math.isfinite(risk):
        return {
            "max_favorable_excursion_r": 0.0,
            "max_adverse_excursion_r": 0.0,
            "highest_r_seen": 0.0,
            "lowest_r_seen": 0.0,
            "bars_to_mfe": None,
            "bars_to_mae": None,
            "reached_half_r": False,
            "reached_one_r": False,
            "reached_tp1": False,
            "reached_tp2": False,
            "reached_sl": False,
            "tp1_rr": None,
            "tp2_rr": None,
        }

    direction = str(direction or "").upper()
    max_favorable_excursion_r = 0.0
    max_adverse_excursion_r = 0.0
    bars_to_mfe = None
    bars_to_mae = None
    reached_half_r = False
    reached_one_r = False
    reached_tp1 = False
    reached_tp2 = False
    reached_sl = False

    def _rr(target: float | None) -> float | None:
        if target is None:
            return None
        try:
            return round(abs(float(target) - float(entry)) / risk, 3)
        except (TypeError, ValueError, OverflowError):
            return None

    for offset, bar in enumerate(future_window or [], start=1):
        try:
            high = float(bar["high"])
            low = float(bar["low"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue

        if direction == "LONG":
            favorable_r = (high - entry) / risk
            adverse_r = (low - entry) / risk
            reached_sl = reached_sl or low <= sl
            reached_tp1 = reached_tp1 or (tp1 is not None and high >= tp1)
            reached_tp2 = reached_tp2 or (tp2 is not None and high >= tp2)
        else:
            favorable_r = (entry - low) / risk
            adverse_r = (entry - high) / risk
            reached_sl = reached_sl or high >= sl
            reached_tp1 = reached_tp1 or (tp1 is not None and low <= tp1)
            reached_tp2 = reached_tp2 or (tp2 is not None and low <= tp2)

        if favorable_r > max_favorable_excursion_r:
            max_favorable_excursion_r = favorable_r
            bars_to_mfe = offset
        if adverse_r < max_adverse_excursion_r:
            max_adverse_excursion_r = adverse_r
            bars_to_mae = offset

        reached_half_r = reached_half_r or favorable_r >= 0.5
        reached_one_r = reached_one_r or favorable_r >= 1.0

    return {
        "max_favorable_excursion_r": round(max_favorable_excursion_r, 2),
        "max_adverse_excursion_r": round(max_adverse_excursion_r, 2),
        "highest_r_seen": round(max_favorable_excursion_r, 2),
        "lowest_r_seen": round(max_adverse_excursion_r, 2),
        "bars_to_mfe": bars_to_mfe,
        "bars_to_mae": bars_to_mae,
        "reached_half_r": reached_half_r,
        "reached_one_r": reached_one_r,
        "reached_tp1": reached_tp1,
        "reached_tp2": reached_tp2,
        "reached_sl": reached_sl,
        "tp1_rr": _rr(tp1),
        "tp2_rr": _rr(tp2),
    }


def _engine_a_exit_diagnostics_summary(
    trades: list[dict], same_bar_both_hit: int = 0
) -> dict:
    """Aggregate Engine A BT path diagnostics for TP/SL investigations."""
    trades = trades or []
    outcome_counts: dict[str, int] = {}
    for trade in trades:
        outcome = str(trade.get("outcome") or "UNKNOWN")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    losses = [t for t in trades if float(t.get("resultR", 0) or 0) <= 0]
    wins = [t for t in trades if float(t.get("resultR", 0) or 0) > 0]
    sl_losses = [t for t in trades if t.get("outcome") == "SL"]
    diagnostic_trades = [
        t for t in trades if t.get("max_favorable_excursion_r") is not None
    ]

    def _pct(items: list[dict], key: str) -> float:
        return round(
            sum(1 for item in items if bool(item.get(key))) / len(items) * 100,
            1,
        ) if items else 0.0

    def _avg(items: list[dict], key: str) -> float | None:
        values = []
        for item in items:
            raw = item.get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(value):
                values.append(value)
        return round(sum(values) / len(values), 2) if values else None

    return {
        "tradesMeasured": len(diagnostic_trades),
        "outcomes": outcome_counts,
        "sameBarBothHit": int(same_bar_both_hit or 0),
        "lossesReachedHalfR": _pct(losses, "reached_half_r"),
        "lossesReachedOneR": _pct(losses, "reached_one_r"),
        "slReachedHalfR": _pct(sl_losses, "reached_half_r"),
        "slReachedOneR": _pct(sl_losses, "reached_one_r"),
        "avgMfeLossesR": _avg(losses, "max_favorable_excursion_r"),
        "avgMaeLossesR": _avg(losses, "max_adverse_excursion_r"),
        "avgMfeWinsR": _avg(wins, "max_favorable_excursion_r"),
        "avgMaeWinsR": _avg(wins, "max_adverse_excursion_r"),
    }


def _resolve_engine_c_bt_levels_after_fill(
    *,
    actual_entry: float,
    consensus: dict | None,
    style_profile: dict | None,
    resolved_style: str,
    direction: str,
    pair_type: str,
    atr: float,
    max_sl_pct: float,
    regime_state=None,
) -> dict:
    """Resolve Engine C BT levels from the actual filled entry only."""

    consensus = consensus or {}
    style_profile = style_profile or {}
    min_rr = float(style_profile.get("min_rr", 1.0) or 1.0)
    fallback_rr = float(style_profile.get("fallback_rr", min_rr) or min_rr)
    fallback_rr = max(min_rr, fallback_rr)

    def _is_valid_sl(value) -> bool:
        try:
            px = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(px) or px <= 0:
            return False
        return px < actual_entry if direction == "LONG" else px > actual_entry

    def _is_valid_tp(value) -> bool:
        try:
            px = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(px) or px <= 0:
            return False
        return px > actual_entry if direction == "LONG" else px < actual_entry

    def _target_from_rr(risk_value: float, rr_value: float) -> float | None:
        if risk_value <= 0 or rr_value <= 0:
            return None
        if direction == "LONG":
            return actual_entry + (risk_value * rr_value)
        return actual_entry - (risk_value * rr_value)

    fallback_levels = None

    def _get_fallback_levels():
        nonlocal fallback_levels
        if fallback_levels is None:
            fallback_levels = calc_levels(
                actual_entry,
                atr,
                direction,
                pair_type,
                regime_state=regime_state,
                style=resolved_style,
            ) or {}
        return fallback_levels

    raw_sl = consensus.get("sl")
    raw_sl_method = str(consensus.get("sl_method") or "consensus")
    if _is_valid_sl(raw_sl):
        final_sl = float(raw_sl)
        selected_sl_source = "structural" if raw_sl_method.startswith("structural") else "consensus"
    else:
        fallback = _get_fallback_levels()
        fallback_sl = fallback.get("sl")
        if not _is_valid_sl(fallback_sl):
            return {
                "final_sl": None,
                "final_tp": None,
                "final_rr": 0.0,
                "selected_tp_source": "calc_levels_fallback",
                "selected_sl_source": "calc_levels_fallback",
                "sl_pct": None,
            }
        final_sl = float(fallback_sl)
        selected_sl_source = "calc_levels_fallback"

    risk = abs(actual_entry - final_sl)
    if risk <= 0:
        return {
            "final_sl": None,
            "final_tp": None,
            "final_rr": 0.0,
            "selected_tp_source": "calc_levels_fallback",
            "selected_sl_source": selected_sl_source,
            "sl_pct": None,
        }

    raw_tp = consensus.get("tp")
    raw_tp_method = str(consensus.get("tp_method") or "consensus")

    if _is_valid_tp(raw_tp):
        final_tp = float(raw_tp)
        actual_rr = abs(final_tp - actual_entry) / risk if risk > 0 else 0.0
        if actual_rr < min_rr:
            final_tp = _target_from_rr(risk, min_rr)
            actual_rr = min_rr
            selected_tp_source = "rebuilt_to_min_rr"
        elif actual_rr > fallback_rr:
            final_tp = _target_from_rr(risk, fallback_rr)
            actual_rr = fallback_rr
            selected_tp_source = "capped_to_fallback_rr"
        else:
            selected_tp_source = (
                "structural_within_band"
                if raw_tp_method.startswith("structural")
                else "consensus_within_band"
            )
    else:
        fallback = _get_fallback_levels()
        fallback_tp = fallback.get("tp1")
        if not _is_valid_tp(fallback_tp):
            return {
                "final_sl": round(final_sl, 6),
                "final_tp": None,
                "final_rr": 0.0,
                "selected_tp_source": "calc_levels_fallback",
                "selected_sl_source": selected_sl_source,
                "sl_pct": abs(actual_entry - final_sl) / actual_entry if actual_entry > 0 else None,
            }
        final_tp = float(fallback_tp)
        actual_rr = abs(final_tp - actual_entry) / risk if risk > 0 else 0.0
        actual_rr = min(max(actual_rr, min_rr), fallback_rr)
        final_tp = _target_from_rr(risk, actual_rr)
        selected_tp_source = "calc_levels_fallback"

    sl_pct = abs(actual_entry - final_sl) / actual_entry if actual_entry > 0 else None
    if max_sl_pct and sl_pct is not None:
        sl_pct = float(sl_pct)

    return {
        "final_sl": round(final_sl, 6),
        "final_tp": round(float(final_tp), 6) if final_tp is not None else None,
        "final_rr": round(float(actual_rr), 6),
        "selected_tp_source": selected_tp_source,
        "selected_sl_source": selected_sl_source,
        "sl_pct": sl_pct,
    }


def _time_series_quality(label: str, times) -> dict:
    """Summarize parse quality and ordering for a timestamp series."""
    valid = times.dropna()
    return {
        "label": label,
        "total": int(len(times)),
        "parse_fail": int(times.isna().sum()),
        "duplicate": int(valid.duplicated().sum()),
        "monotonic": bool(valid.is_monotonic_increasing),
    }


def _normalize_style(style: str | None) -> str:
    s = (style or "auto").lower()
    return s if s in ("auto", "swing", "intraday", "scalp") else "auto"


def _effective_backtest_style(pair: dict, requested_style: str) -> str:
    if requested_style != "auto":
        return requested_style
    ptype = pair.get("type", "")
    if ptype == "crypto":
        return "intraday"
    elif ptype == "forex":
        return "intraday"
    else:
        return "swing"


def _records_for_calibration(
    trades: list[dict],
    *,
    engine: str,
    asset_class: str,
    style: str,
    default_max_score: float | None = None,
) -> list[dict]:
    records = []
    for trade in trades or []:
        records.append(
            {
                "engine": engine,
                "asset_class": asset_class,
                "style": style,
                "raw_score": trade.get("score"),
                "max_score": trade.get("max_score", default_max_score),
                "won": (trade.get("resultR", trade.get("r_multiple", 0)) or 0) > 0,
            }
        )
    return records


def _engine_a_volume_threshold(pair_ctx: dict, canonical_validation_mode: str) -> float:
    return volume_threshold_for_backtest(
        get_pair_profile(pair_ctx).get("volume_threshold"),
        validation_mode=canonical_validation_mode,
        default_live=float(CONFIG.get("VOLUME_THRESHOLD", 1.5)),
        default_bt=float(
            CONFIG.get(
                "VOLUME_THRESHOLD_BACKTEST", CONFIG.get("VOLUME_THRESHOLD", 1.5)
            )
        ),
    )


def _attach_research_validation_payload(
    result: dict,
    trades: list,
    *,
    canonical_vm: str,
    temporal_vm: str,
    purge_gap: int,
    folds: int,
    mode_warning: str | None,
) -> None:
    if trades:
        _has_fvg = any(t.get("fvg_bonus", 0.0) > 0 for t in trades)
        _has_vol = any(t.get("volume_strength", 0.0) > 0 for t in trades)
        _engine = result.get("engineType", "")
        if _engine == "NAKED":
            if not _has_fvg:
                log.warning(f"[TELEMETRY-WARN] {result.get('pair', 'Unknown')} Engine B backtest has 0 non-zero fvg_bonus. Possible regression.")
            if not _has_vol:
                log.warning(f"[TELEMETRY-WARN] {result.get('pair', 'Unknown')} Engine B backtest has 0 non-zero volume_strength. Possible regression.")
            _all_2r = all(abs(t.get("rr_target", 0.0) - 2.0) < 0.01 for t in trades)
            if _all_2r:
                log.warning(f"[TELEMETRY-WARN] {result.get('pair', 'Unknown')} Engine B backtest rr_target is constant 2.0. Possible regression.")
            _missing_actual_rr = sum(1 for t in trades if "actual_rr" not in t)
            if _missing_actual_rr / len(trades) > 0.05:
                log.warning(f"[TELEMETRY-WARN] {result.get('pair', 'Unknown')} Engine B backtest actual_rr is missing from >5% trades.")

    result["validationMode"] = canonical_vm
    result["researchValidation"] = build_validation_report(
        trades,
        validation_mode=canonical_vm,
        temporal_mode=temporal_vm,
        purge_gap=purge_gap,
        folds=folds,
        mode_warning=mode_warning,
        wf_split=result.get("wfSplit"),
    )


def _records_for_meta(
    trades: list[dict],
    *,
    engine: str,
    asset_class: str,
    style: str,
) -> list[dict]:
    records = []
    for trade in trades or []:
        records.append(
            {
                "engine": engine,
                "asset_class": asset_class,
                "style": style,
                "won": (trade.get("resultR", trade.get("r_multiple", 0)) or 0) > 0,
                "expectancy_r": trade.get("resultR", trade.get("r_multiple")),
                "slippage_bps": trade.get("slippage_bps"),
                "regime": trade.get("regime"),
            }
        )
    return records


def backtest_pair(pair, style="auto", validation_mode="standard", purge_gap=200, folds=3):
    """Walk-forward backtest on D1/H4 bars with slippage, regime tagging, and Monte Carlo DD simulation."""

    requested_style = _normalize_style(style)

    effective_style = _effective_backtest_style(pair, requested_style)

    if effective_style not in ("swing", "intraday", "scalp"):
        return {"error": f"Unsupported backtest style: {requested_style}"}

    log.info(
        f"[BT] {pair['display']} fetching data... style={requested_style}->{effective_style}"
    )

    try:
        import pandas as pd

        def df_to_candles(df):

            return [
                {
                    "time": str(idx),
                    "open": float(r["Open"]),
                    "high": float(r["High"]),
                    "low": float(r["Low"]),
                    "close": float(r["Close"]),
                    "vol": float(r.get("Volume", 0)),
                }
                for idx, r in df.iterrows()
            ]

        _ptype = pair.get("type", "")

        if pair["source"] == "binance":
            # Crypto: config-gated Engine A signal feed (Binance default, Bybit experiment optional).
            d1_raw = _crypto_bt_signal_candles(
                pair,
                engine="A",
                tf="D1",
                limit=750,
                min_bars=230,
            )

            h4_raw = _crypto_bt_signal_candles(
                pair,
                engine="A",
                tf="H4",
                limit=4400,
                min_bars=500,
            )

            h1_raw = _crypto_bt_signal_candles(
                pair,
                engine="A",
                tf="H1",
                limit=17600,
                min_bars=500,
            )

        elif pair["source"] == "mt5":
            # MT5 price backtests must remain MT5-sourced; missing/thin data skips later.
            d1_raw = _bt_cached_fetch(
                pair,
                "D1",
                750,
                lambda lim: _rt().fetch_candles(pair, "D1", lim),
                provider="mt5",
                min_bars=230,
            )
            h4_raw = _bt_cached_fetch(
                pair,
                "H4",
                4400,
                lambda lim: _rt().fetch_candles(pair, "H4", lim),
                provider="mt5",
                min_bars=500,
            )
            h1_raw = _bt_cached_fetch(
                pair,
                "H1",
                17600,
                lambda lim: _rt().fetch_candles(pair, "H1", lim),
                provider="mt5",
                min_bars=500,
            )
        elif _ptype in ("stock", "commodity", "index"):
            # Stocks/Commodities/Indices: EODHD D1 + EODHD intraday (730d)
            # Fallback chain: EODHD → Polygon (commodities) → yfinance
            d1_raw = _bt_cached_fetch(
                pair,
                "D1",
                750,
                lambda lim: _rt().extract_candles(_rt().fetch_eodhd(pair, "D1", lim)),
                provider="eodhd",
                min_bars=230,
            ) or _bt_cached_fetch(
                pair,
                "D1",
                750,
                lambda lim: _rt().fetch_candles(pair, "D1", lim),
                provider=str(pair.get("source") or "fallback"),
                min_bars=230,
            )

            h4_raw, h1_raw = _bt_cached_eodhd_intraday(pair, days=730)

            if not h4_raw or not h1_raw:
                # Try Polygon first for commodities (better data quality than yfinance)
                _pg_ticker = _rt().polygon_ticker_for_pair(pair)
                if _pg_ticker and _ptype == "commodity":
                    log.info(
                        f"[BT] {pair['display']}: EODHD intraday failed, trying Polygon"
                    )
                    _pg_h4 = _rt().extract_candles(_rt().fetch_polygon(pair, "H4", 4400))
                    _pg_h1 = _rt().extract_candles(_rt().fetch_polygon(pair, "H1", 17600))
                    h4_raw = h4_raw or _pg_h4
                    h1_raw = h1_raw or _pg_h1
                # Legacy vendor fallback removed.
                _h4_thin = not h4_raw or len(h4_raw or []) < 500
                _h1_thin = not h1_raw or len(h1_raw or []) < 500
                if False and (_h4_thin or _h1_thin) and _ptype == "commodity":
                    pass

                # yfinance as final fallback — also triggers when Polygon returns thin data (<500 H4 bars)
                _h4_thin = not h4_raw or len(h4_raw or []) < 500
                _h1_thin = not h1_raw or len(h1_raw or []) < 500
                if _h4_thin or _h1_thin:
                    _yf_sym = _rt().yfinance_symbol_for_pair(pair)
                    if _yf_sym:
                        log.info(
                            f"[BT] {pair['display']}: H4={len(h4_raw or [])} H1={len(h1_raw or [])} bars — trying yfinance for better coverage"
                        )
                        _yf_h4, _yf_h1 = _rt().fetch_bt_yfinance(_yf_sym)
                        if _yf_h4 and len(_yf_h4) > len(h4_raw or []):
                            h4_raw = _yf_h4
                        elif not h4_raw:
                            h4_raw = _yf_h4
                        if _yf_h1 and len(_yf_h1) > len(h1_raw or []):
                            h1_raw = _yf_h1
                        elif not h1_raw:
                            h1_raw = _yf_h1

        else:
            d1_raw = _bt_cached_fetch(
                pair,
                "D1",
                750,
                lambda lim: _rt().fetch_candles(pair, "D1", lim),
                provider=str(pair.get("source") or "fallback"),
                min_bars=230,
            )

            h4_raw = _bt_cached_fetch(
                pair,
                "H4",
                4400,
                lambda lim: _rt().fetch_candles(pair, "H4", lim),
                provider=str(pair.get("source") or "fallback"),
                min_bars=500,
            )

            h1_raw = _bt_cached_fetch(
                pair,
                "H1",
                17600,
                lambda lim: _rt().fetch_candles(pair, "H1", lim),
                provider=str(pair.get("source") or "fallback"),
                min_bars=500,
            )

        if not d1_raw:
            return {"error": f"No D1 data for {pair['display']}"}

        if not h4_raw or not h1_raw:
            return {"error": f"No H4/H1 data for {pair['display']}"}

        if len(d1_raw) < 230:
            return {
                "error": f"Insufficient D1 history for {pair['display']} ({len(d1_raw)} bars)"
            }

        if effective_style == "swing" and len(h4_raw) < 250:
            return {
                "error": f"Insufficient H4 history for swing backtest ({len(h4_raw)} bars, need 250+) — Polygon free plan may cap intraday history. Try adding a yfinanceSymbol override or EODHD intraday key."
            }

        if effective_style == "intraday" and len(h4_raw) < 260:
            return {
                "error": f"Insufficient H4 history for {pair['display']} ({len(h4_raw)} bars, need 260+)"
            }

        if effective_style == "scalp" and len(h1_raw) < 260:
            return {
                "error": f"Insufficient H1 history for {pair['display']} ({len(h1_raw)} bars)"
            }

        h4_times = pd.to_datetime(
            [c["time"] for c in h4_raw], utc=True, errors="coerce"
        )

        h1_times = pd.to_datetime(
            [c["time"] for c in h1_raw], utc=True, errors="coerce"
        )
        h4_time_quality = _time_series_quality("H4", h4_times)
        h1_time_quality = _time_series_quality("H1", h1_times)

    except Exception as e:
        return {"error": f"Data fetch failed: {e}"}

    bt_vectorized = bool(CONFIG.get("BT_VECTORIZED", False))
    d1_cache: dict[tuple[int, int, str], dict] = {}
    h4_cache: dict[tuple[int, int, str], dict] = {}
    h1_cache: dict[tuple[int, int, str], dict] = {}
    if bt_vectorized:
        log.info("[BT] %s vectorized indicator cache enabled", pair["display"])

    # N8: Session-variable slippage â€" forex widens during Asian/off-hours

    # Shared init

    trades = []
    equity = 1.0
    equity_curve = [1.0]

    _ptype = pair["type"]
    requested_style = _normalize_style(style)
    _pair_score_group = get_pair_score_group(pair)
    _pair_ctx = dict(pair)
    for _k in ["votes", "sentiment", "eventRisk", "fundingRate", "confluenceScore"]:
        _pair_ctx.pop(_k, None)
    _pair_ctx["score_group"] = _pair_score_group
    _level_atr_class = get_pair_level_atr_class(_pair_ctx)

    # Engine A backtest gate: pair profile -> pair/group config -> 3-tier fallback.
    # Stage 4.2: Backtest and live use identical thresholds.
    bt_min = get_score_threshold(_pair_ctx, is_backtest=True)

    _canonical_vm, _vm_mode_warning = normalize_validation_mode(validation_mode)
    _temporal_vm = temporal_validation_mode(_canonical_vm)
    _bt_volume_threshold = _engine_a_volume_threshold(_pair_ctx, _canonical_vm)
    if _vm_mode_warning:
        log.warning("[BT] %s: %s", pair.get("display"), _vm_mode_warning)

    _h4_need = max(50, CONFIG["H4_CANDLES"])

    _h1_need = max(50, CONFIG["H1_CANDLES"])

    funnel = {
        "total_setups": 0,
        "fail_structure": 0,
        "fail_score": 0,
        "fail_macro": 0,
        "fail_regime": 0,
        "taken": 0,
        "skip_window": 0,
        "h4ParseFail": h4_time_quality["parse_fail"],
        "h1ParseFail": h1_time_quality["parse_fail"],
        "h4DuplicateTs": h4_time_quality["duplicate"],
        "h1DuplicateTs": h1_time_quality["duplicate"],
        "h4Monotonic": h4_time_quality["monotonic"],
        "h1Monotonic": h1_time_quality["monotonic"],
    }
    time_alignment_warnings = []
    for quality in (h4_time_quality, h1_time_quality):
        if quality["parse_fail"]:
            time_alignment_warnings.append(
                f"{quality['label']} parse failures: {quality['parse_fail']}/{quality['total']}"
            )
        if quality["duplicate"]:
            time_alignment_warnings.append(
                f"{quality['label']} duplicate timestamps: {quality['duplicate']}"
            )
        if not quality["monotonic"]:
            time_alignment_warnings.append(
                f"{quality['label']} timestamps are not monotonic"
            )
    funnel["validationMode"] = _canonical_vm
    funnel["temporalValidationMode"] = _temporal_vm
    funnel["liveParityExecutionStress"] = _canonical_vm == "live_parity"
    funnel["timeAlignmentWarnings"] = time_alignment_warnings
    if time_alignment_warnings:
        log.warning(
            "[BT] %s timestamp quality warnings: %s",
            pair["display"],
            "; ".join(time_alignment_warnings),
        )

    _recent_scores = []  # CR3: rolling score history for adaptive percentile threshold

    _pair_max_score = _rt().max_score_for_pair(pair)  # Pre-compute for score-based sizing
    same_bar_both_hit = 0
    _structure_first_cfg = _engine_a_structure_first_cfg()
    _structure_first_engine = None
    if _structure_first_cfg.get("enabled"):
        try:
            from market_structure import NakedEngine

            _structure_first_engine = NakedEngine()
        except Exception as _structure_engine_err:
            log.warning(
                "[BT] %s structure-first engine unavailable: %s",
                pair.get("display"),
                _structure_engine_err,
            )
    funnel["structureFirstEnabled"] = bool(_structure_first_cfg.get("enabled"))

    # BUG 1 fix: BTC bias is derived point-in-time per bar via _bt_btc_bias(d1_window, pair).
    # No precompute needed — each call reads the D1 window available at that bar.

    # Crypto: historical funding + OI series (Bybit, Binance fallback) for point-in-time bars.
    _bt_crypto_funding_rows: list | None = None
    _bt_crypto_oi_rows: list | None = None
    if _ptype == "crypto":
        try:
            from data_feeds import prepare_crypto_backtest_derivative_series

            _bt_crypto_funding_rows, _bt_crypto_oi_rows = (
                prepare_crypto_backtest_derivative_series(
                    pair, d1_raw, h4_raw, h1_raw
                )
            )
            log.info(
                "[BT-DERIV] %s: historical funding_rows=%d oi_rows=%d",
                pair.get("display"),
                len(_bt_crypto_funding_rows or []),
                len(_bt_crypto_oi_rows or []),
            )
        except Exception as _deriv_err:
            log.warning(
                "[BT-DERIV] %s: derivative series load failed: %s",
                pair.get("display"),
                _deriv_err,
            )

    _bt_dxy_h4_raw: list | None = None
    _bt_dxy_h4_times = None
    if pair.get("display") == "XAU/USD":
        try:
            _bt_dxy_h4_raw, _ = _rt().fetch_bt_yfinance("DX-Y.NYB")
            if _bt_dxy_h4_raw:
                _bt_dxy_h4_times = pd.to_datetime(
                    [c["time"] for c in _bt_dxy_h4_raw], utc=True, errors="coerce"
                )
                log.info(
                    "[BT-MACRO] %s: loaded %d DXY H4 bars for gold macro context",
                    pair.get("display"),
                    len(_bt_dxy_h4_raw),
                )
        except Exception as _macro_err:
            log.warning(
                "[BT-MACRO] %s: DXY history load failed: %s",
                pair.get("display"),
                _macro_err,
            )

    _bt_intermarket_series_store = None
    if bool((CONFIG.get("INTERMARKET_CONFIRMATION", {}) or {}).get("enabled", False)):
        try:
            _bt_im_universe = discover_active_universe(
                getattr(_rt(), "ALL_PAIRS", []),
                disabled_pairs=getattr(_rt(), "disabled_pairs", []),
                etf_pairs=getattr(_rt(), "ETF_PAIRS", []),
            )
            _bt_im_limit = max(
                int(CONFIG.get("H4_CANDLES", 1000) or 1000),
                220,
            )
            _bt_intermarket_series_store = prepare_series_store(
                _bt_im_universe,
                fetch_candles=_rt().fetch_candles,
                timeframe="H4",
                limit=_bt_im_limit,
                config=CONFIG,
                preloaded_candles={pair.get("display"): h4_raw},
            )
        except Exception as _bt_im_err:
            _bt_intermarket_series_store = None
            log.warning(
                "[BT-INTERMARKET] %s: history store build failed: %s",
                pair.get("display"),
                _bt_im_err,
            )

    if effective_style == "swing":
        # --- SWING D1 LOOP â€" UNCHANGED ---

        MIN_BARS = 220  # Fixed indicator warmup (EMA200 + buffer) — do not tie to D1_CANDLES fetch depth
        COOLDOWN = 3
        MAX_OPEN = 3  # R5: max concurrent positions

        total_bars = len(d1_raw)

        i = MIN_BARS
        last_exit_bar = 0
        open_positions = 0

        while i < total_bars - 1:
            _vf = backtest_bar_validation_state(
                i,
                min_bars=MIN_BARS,
                total_bars=total_bars,
                temporal_mode=_temporal_vm,
                purge_gap=purge_gap,
                folds=folds,
            )
            if _vf["skip"]:
                i += 1
                continue
            if i - last_exit_bar < COOLDOWN:
                i += 1
                continue

            # R5: Max concurrent positions cap

            if open_positions >= MAX_OPEN:
                i += 1
                continue

            d1_window = d1_raw[i - MIN_BARS : i]

            entry_ts = pd.to_datetime(d1_raw[i]["time"], utc=True, errors="coerce")

            if pd.isna(entry_ts):
                i += 1
                continue

            # BUG 8 fix: Align H4/H1 windows to the same decision timestamp (entry_ts).
            # Do not allow bars from the entry day to leak into the confluence math.
            intraday_cutoff = entry_ts

            # BUG 9 fix: Optimize window construction with binary search (O(log N) vs O(N))
            h4_idx = bisect.bisect_left(h4_times, intraday_cutoff)
            h4_window = h4_raw[max(0, h4_idx - _h4_need) : h4_idx]

            h1_idx = bisect.bisect_left(h1_times, intraday_cutoff)
            h1_window = h1_raw[max(0, h1_idx - _h1_need) : h1_idx]

            if len(h4_window) < 50 or len(h1_window) < 50:
                funnel["skip_window"] += 1
                i += 1
                continue

            try:
                if bt_vectorized:
                    d1i = _bt_indicators_from_cache(
                        d1_raw,
                        end_idx=i - 1,
                        window=MIN_BARS,
                        asset_type=pair.get("type", "stock"),
                        cache=d1_cache,
                    )
                    h4i = _bt_indicators_from_cache(
                        h4_raw,
                        end_idx=h4_idx - 1,
                        window=_h4_need,
                        asset_type=pair.get("type", "stock"),
                        cache=h4_cache,
                    )
                    h1i = _bt_indicators_from_cache(
                        h1_raw,
                        end_idx=h1_idx - 1,
                        window=_h1_need,
                        asset_type=pair.get("type", "stock"),
                        cache=h1_cache,
                    )
                    if not d1i or not h4i or not h1i:
                        i += 1
                        continue
                else:
                    d1i = calc_indicators_with_normalized(
                        d1_window, pair.get("type", "stock")
                    )
                    h4i = calc_indicators_with_normalized(
                        h4_window, pair.get("type", "stock")
                    )
                    h1i = calc_indicators_with_normalized(
                        h1_window, pair.get("type", "stock")
                    )

                # Inject fib_proximity so structure factor is non-None during backtest
                try:
                    _bt_fib = calc_fib(h4_window)
                    _bt_h4_close = h4_window[-1]["close"] if h4_window else None
                    if _bt_fib and _bt_h4_close:
                        h4i["snap"]["fib_proximity"] = calc_fib_proximity(
                            float(_bt_h4_close), _bt_fib
                        )
                except Exception:
                    pass

                vols = [c["vol"] for c in h1_window]
                vsma = calc_sma(vols, 20)

                vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0

                stoch = calc_stochastic(
                    h4_window, 5, 3, 3
                )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

                # BUG 1 fix: use historical BTC bias at this bar (not hardcoded "neutral")
                btc_bias = _bt_btc_bias(d1_window, _pair_ctx)
                _bt_intermarket_ctx = None
                if _bt_intermarket_series_store is not None:
                    _bt_intermarket_ctx = build_point_in_time_context(
                        _pair_ctx,
                        all_pairs=getattr(_rt(), "ALL_PAIRS", []),
                        disabled_pairs=getattr(_rt(), "disabled_pairs", []),
                        etf_pairs=getattr(_rt(), "ETF_PAIRS", []),
                        series_store=_bt_intermarket_series_store,
                        cutoff_ts=intraday_cutoff,
                        config=CONFIG,
                    )

                # Engine A v2: all asset classes (including forex) use calc_confluence → compute_factor_scores
                if False:  # forex branch retired — Engine A v2 handles forex natively
                    pass
                else:
                    _bt_funding_rate = None
                    _bt_oi_data = None
                    if _ptype == "crypto":
                        _prev_bt = (
                            pd.to_datetime(
                                d1_raw[i - 1]["time"], utc=True, errors="coerce"
                            )
                            if i >= 1
                            else None
                        )
                        _bt_funding_rate, _bt_oi_data = _bt_crypto_funding_oi_for_bar(
                            _ptype,
                            _bt_crypto_funding_rows,
                            _bt_crypto_oi_rows,
                            entry_ts,
                            _prev_bt,
                        )
                    _bt_oi_ctx = build_oi_context_for_factor_scoring(
                        _bt_oi_data, d1_window, h1i.get("snap")
                    )
                    _bt_macro_ctx = _bt_gold_macro_context(
                        _pair_ctx,
                        intraday_cutoff,
                        h4_window,
                        _bt_dxy_h4_raw,
                        _bt_dxy_h4_times,
                    )
                    res = calc_confluence(
                        d1i,
                        h4i,
                        h1i,
                        vr,
                        stoch,
                        _pair_ctx,
                        btc_bias,
                        d1_candles=d1_window,
                        h4_candles=h4_window,
                        h1_candles=h1_window,
                        volume_threshold=_bt_volume_threshold,
                        bar_time=h4_window[-1].get("time") if h4_window else None,
                        funding_rate=_bt_funding_rate,
                        oi_data=_bt_oi_data,
                        oi_context=_bt_oi_ctx,
                        macro_context=_bt_macro_ctx,
                        intermarket_context=_bt_intermarket_ctx,
                    )

            except Exception as _bt_bar_err:
                log.debug(
                    f"[BT] {pair['display']} bar {i} skipped: {_bt_bar_err}",
                    exc_info=False
                )
                i += 1
                continue

            funnel["total_setups"] += 1

            _ts = res.get("trendState", "UNKNOWN")

            _recent_scores.append(res["score"])

            if _structure_first_cfg.get("enabled"):
                _ea_direction = res.get("direction")
                try:
                    _structure_price = float(d1_window[-1]["close"])
                except (TypeError, ValueError, KeyError, IndexError):
                    _structure_price = None
                _structure_atr = _rt().atr_for_levels(
                    d1i, h4i, h1i, pair=pair, style=effective_style
                )
                _structure_ok, _structure_detail = _engine_a_structure_first_entry_check(
                    engine=_structure_first_engine,
                    pair=_pair_ctx,
                    direction=_ea_direction,
                    d1_candles=d1_window,
                    h4_candles=h4_window,
                    h1_candles=h1_window,
                    current_price=_structure_price,
                    atr=_structure_atr,
                    regime=res.get("regimeName") or ((res.get("regime") or {}).get("label")),
                    style=effective_style,
                    d1_snap=d1i.get("snap") if isinstance(d1i, dict) else None,
                    h4_snap=h4i.get("snap") if isinstance(h4i, dict) else None,
                    cfg=_structure_first_cfg,
                )
                if not _structure_ok:
                    funnel["fail_structure"] = funnel.get("fail_structure", 0) + 1
                    i += 1
                    continue

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            if is_trend_state_blocked(_ts, _pair_ctx, scope="backtest"):
                funnel["fail_regime"] = funnel.get("fail_regime", 0) + 1
                i += 1
                continue

            direction = res["direction"]

            # Indicator windows end at i-1, so the first non-lookahead fill is bar i open.

            if i >= total_bars:
                i += 1
                continue

            entry_bar = d1_raw[i]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            atr = _rt().atr_for_levels(d1i, h4i, h1i, pair=pair, style=effective_style)
            atr, level_atr_feed = _engine_a_level_atr_for_bt(
                atr, pair, effective_style, as_of=entry_bar.get("time")
            )

            if not atr or atr == 0:
                i += 1
                continue

            _slip_mult = 3.0 if _canonical_vm == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(
                _bar_with_slippage_context(entry_bar, atr=atr), _ptype
            ) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            # C4: Use shared calc_levels (deduplicates with analyze_pair)

            _bt_regime_state = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _level_atr_class, regime_state=_bt_regime_state,
                style=effective_style,
            )

            sl = lvl["sl"]
            tp1 = lvl["tp1"]
            tp2 = lvl["tp2"]

            sl_mult = lvl["mults"]["sl"]
            tp1_mult = lvl["mults"]["tp1"]
            tp2_mult = lvl["mults"]["tp2"]

            rr1 = lvl["rr1"]

            # Validate levels are finite and meaningful — NaN/0 levels cause ghost OPEN trades

            if not all(math.isfinite(v) and v > 0 for v in (sl, tp1, tp2)):
                i += 1
                continue

            if (
                abs(entry - sl) < entry * 0.0001
            ):  # SL less than 0.01% from entry — invalid
                i += 1
                continue

            # V3: Structure-based stops for crypto — use wider of ATR-stop vs swing-stop

            if _ptype == "crypto":
                _recent = d1_window[-10:]

                if direction == "LONG":
                    # BUG 5 fix: LONG SL is below price — only tighten (use swing if higher/closer)
                    swing_sl = min(c["low"] for c in _recent)
                    if swing_sl > sl:
                        sl = swing_sl

                else:
                    # BUG 5 fix: SHORT SL is above price — only tighten (use swing if lower/closer)
                    swing_sl = max(c["high"] for c in _recent)
                    if swing_sl < sl:
                        sl = swing_sl

                rr1 = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
                # Guard: skip if structural SL has degraded RR below minimum
                _min_rr = float(CONFIG.get("STYLE_ATR_MULTS", {}).get(
                    effective_style, {}).get(_ptype, {}).get("tp1", 1.5)) / float(
                    CONFIG.get("STYLE_ATR_MULTS", {}).get(
                    effective_style, {}).get(_ptype, {}).get("sl", 1.0))
                if rr1 < max(0.8, _min_rr * 0.7):   # allow 30% degradation before skipping
                    funnel["fail_score"] += 1
                    i += 1
                    continue

            # MAX_SL_PCT rejection — Ensuring backtest results reflect the same risk thresholds as live trading.
            _max_sl_pct = CONFIG.get("MAX_SL_PCT", {}).get(_ptype, 0.05)
            _sl_dist_pct = abs(float(entry) - float(sl)) / float(entry)
            if _sl_dist_pct > _max_sl_pct:
                log.debug(
                    f"[SL-CAP] {pair['display']} {direction} SL {_sl_dist_pct:.1%} "
                    f"exceeds cap {_max_sl_pct:.1%} — REJECTED"
                )
                funnel["fail_score"] += 1
                i += 1
                continue

            # T1: Volatility-adjusted sizing â€" if ATR > 1.5x its 20-bar SMA, reduce size 30%

            _atr_series = calc_atr(
                [c["high"] for c in d1_window],
                [c["low"] for c in d1_window],
                [c["close"] for c in d1_window],
                14,
            )

            _atr_sma = calc_sma([v for v in _atr_series if v is not None], 20)

            _vol_adj = 1.0

            _valid_atr_sma = [v for v in _atr_sma if v is not None]

            if _valid_atr_sma and _valid_atr_sma[-1] and _valid_atr_sma[-1] > 0:
                if atr > _valid_atr_sma[-1] * 1.5:
                    _vol_adj = 0.7

            _score_factor = (
                max(0.25, min(1.0, res["score"] / _pair_max_score))
                if _pair_max_score > 0
                else 1.0
            )

            outcome = "OPEN"
            result_r = 0.0
            exit_bar = i

            for j in range(i, min(i + 20, total_bars)):
                bar = d1_raw[j]
                _bar_outcome, _both_hit = _resolve_barrier_exit(
                    bar,
                    direction=direction,
                    sl=sl,
                    tp1=tp1,
                    tp2=tp2,
                )
                if _both_hit:
                    same_bar_both_hit += 1
                if _bar_outcome == "TP2":
                    outcome = "TP2"
                    _sl_dist_r = abs(entry - sl)
                    result_r = (abs(tp2 - entry) / _sl_dist_r) if _sl_dist_r > 0 else (tp2_mult / sl_mult)
                    exit_bar = j
                    break
                if _bar_outcome == "TP1":
                    outcome = "TP1"
                    result_r = rr1 - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "SL":
                    _sl_slip_r = (
                        _get_slippage_for_bar(_bar_with_slippage_context(bar, atr=atr), _ptype) * sl / (atr * sl_mult)
                        if atr and sl_mult
                        else 0
                    )
                    outcome = "SL"
                    result_r = round(-1.0 - _sl_slip_r, 4)
                    exit_bar = j
                    break
            if outcome == "OPEN":
                # Force-close at last forward bar â€" record actual P&L vs recording a ghost 0R

                exit_bar = min(i + 19, total_bars - 1)
                _last_fwd = d1_raw[exit_bar]

                _exit_px = _last_fwd["close"]

                _sl_dist = abs(entry - sl)

                if _sl_dist > 0 and math.isfinite(_exit_px):
                    result_r = (
                        (_exit_px - entry) / _sl_dist
                        if direction == "LONG"
                        else (entry - _exit_px) / _sl_dist
                    )

                    result_r = round(max(-5.0, min(5.0, result_r)), 2)  # cap outliers

                else:
                    result_r = 0.0

                outcome = "TIMEOUT"

            _exit_path = _engine_a_trade_path_diagnostics(
                d1_raw[i : min(exit_bar + 1, total_bars)],
                direction=direction,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
            )

            live_risk_pct = _live_base_risk_pct(_ptype)

            # F3: Deduct round-trip exchange fee (entry + exit commission) from result_r

            _sl_dist_sw = abs(entry - sl)

            if _sl_dist_sw > 0:
                _fee_r_sw = CONFIG["FEE_PCT"].get(_ptype, 0.0004) * entry / _sl_dist_sw

                result_r = round(result_r - _fee_r_sw, 4)

            # T1: Apply volatility adjustment and score-based sizing to position size

            # Apply the same base per-asset risk percentages as the live risk gateway.
            # (forex=0.6, crypto=0.8). Live risk_engine does NOT apply RISK_MULT —
            # it uses asset_risk_map in _adaptive_risk_pct() instead.
            # BT equity curves are therefore ~40% smaller than live for forex.
            # Do not compare BT Sharpe/SQN directly to live P&L without adjusting.
            equity_change = (
                result_r * live_risk_pct * _vol_adj * _score_factor
            )

            equity = round(equity * (1 + equity_change), 6)

            equity_curve.append(round(equity, 4))

            # R2: Tag trade with regime for segmentation

            _regime = (
                _ts
                if _ts in ("TRENDING", "DEVELOPING", "RANGING", "DEAD RANGING")
                else "UNKNOWN"
            )

            open_positions += 1

            funnel["taken"] += 1

            from scoring import get_session

            trades.append(
                {
                    "date": entry_bar["time"][:10],
                    "pair": pair["display"],
                    "direction": direction,
                    "score": res["score"],
                    "entry": round(entry, 6),
                    "sl": round(sl, 6),
                    "tp1": round(tp1, 6),
                    "tp2": round(tp2, 6),
                    "outcome": outcome,
                    "resultR": round(result_r, 2),
                    "regime": _regime,
                    **build_strategy_lab_telemetry(
                        engine="ENGINE_A",
                        strategy_family="ENGINE_A_ONLY",
                        regime=_regime,
                        setup_type="standard",
                        timeframe="D1",
                        failure_reason=outcome if outcome not in ["TP1", "TP2"] else None,
                        entry_reason=None,
                        exit_reason=outcome,
                        source_module="backtest_runner",
                        source_function="backtest_pair"
                    ),
                    "oos": _vf["oos_label"],
                    "wf_fold": _vf["wf_fold"],
                    "validation_mode": _canonical_vm,
                    "volAdj": _vol_adj,
                    "factors": res.get("factor_scores") or {},
                    "factor_weights": res.get("factor_weights") or {},
                    "rsi": d1_window[-1].get("rsi") if d1_window else None,
                    "adx": d1_window[-1].get("adx") if d1_window else None,
                    "ema_directions": "aligned" if (d1_window and d1_window[-1].get("ema21", 0) > d1_window[-1].get("ema50", 0)) else "split",
                    "addon_score": res.get("factor_scores", {}).get("addon", 0.0),
                    "trend_score": res.get("factor_scores", {}).get("trend", 0.0),
                    "momentum_score": res.get("factor_scores", {}).get("momentum", 0.0),
                    "session": get_session(entry_bar["time"]).get("name", "UNKNOWN") if "time" in entry_bar else "UNKNOWN",
                    **_exit_path,
                }
            )

            if outcome not in ("OPEN",):
                last_exit_bar = exit_bar

                open_positions -= 1

            i = exit_bar + 1 if outcome != "OPEN" else i + 1

    elif effective_style == "intraday":  # walk H4 bars
        MIN_H4 = 250  # Fixed indicator warmup — do not tie to H4_CANDLES fetch depth
        COOLDOWN = 2
        MAX_HOLD = 24
        MAX_OPEN = 3

        total_h4 = len(h4_raw)

        i = MIN_H4
        last_exit_bar = 0
        open_positions = 0

        # Pre-parse timestamps once

        h4_ts = pd.to_datetime([c["time"] for c in h4_raw], utc=True, errors="coerce")

        d1_ts = pd.to_datetime([c["time"] for c in d1_raw], utc=True, errors="coerce")

        while i < total_h4 - 1:
            _vf = backtest_bar_validation_state(
                i,
                min_bars=MIN_H4,
                total_bars=total_h4,
                temporal_mode=_temporal_vm,
                purge_gap=purge_gap,
                folds=folds,
            )
            if _vf["skip"]:
                i += 1
                continue
            if i - last_exit_bar < COOLDOWN:
                i += 1
                continue

            if open_positions >= MAX_OPEN:
                i += 1
                continue

            h4_window = h4_raw[i - MIN_H4 : i]

            entry_ts = h4_ts[i]

            if pd.isna(entry_ts):
                i += 1
                continue

            # H1 alignment: all H1 bars before this H4 bar's timestamp + 4h

            # BUG 8 fix: Align H1 window to the H4 decision timestamp (entry_ts).
            h1_cutoff = entry_ts

            h1_idx = bisect.bisect_left(h1_times, h1_cutoff)
            h1_window = h1_raw[max(0, h1_idx - _h1_need) : h1_idx]

            # D1 context: real D1 data up to this point for Weinstein/regime

            d1_idx = bisect.bisect_left(d1_ts, entry_ts)
            d1_ctx = d1_raw[max(0, d1_idx - 220) : d1_idx]  # Fixed 220 bar lookback for indicators

            if len(h1_window) < 50 or len(d1_ctx) < 50:
                i += 1
                continue

            try:
                if bt_vectorized:
                    h4i = _bt_indicators_from_cache(
                        h4_raw,
                        end_idx=i - 1,
                        window=MIN_H4,
                        asset_type=pair.get("type", "stock"),
                        cache=h4_cache,
                    )
                    h1i = _bt_indicators_from_cache(
                        h1_raw,
                        end_idx=h1_idx - 1,
                        window=_h1_need,
                        asset_type=pair.get("type", "stock"),
                        cache=h1_cache,
                    )
                    d1i_ctx = _bt_indicators_from_cache(
                        d1_raw,
                        end_idx=d1_idx - 1,
                        window=220,
                        asset_type=pair.get("type", "stock"),
                        cache=d1_cache,
                    )
                    if not h4i or not h1i or not d1i_ctx:
                        i += 1
                        continue
                else:
                    h4i = calc_indicators_with_normalized(
                        h4_window, pair.get("type", "stock")
                    )
                    h1i = calc_indicators_with_normalized(
                        h1_window, pair.get("type", "stock")
                    )
                    d1i_ctx = calc_indicators_with_normalized(
                        d1_ctx, pair.get("type", "stock")
                    )

                # Inject fib_proximity so structure factor is non-None during backtest
                try:
                    _bt_fib = calc_fib(h4_window)
                    _bt_h4_close = h4_window[-1]["close"] if h4_window else None
                    if _bt_fib and _bt_h4_close:
                        h4i["snap"]["fib_proximity"] = calc_fib_proximity(
                            float(_bt_h4_close), _bt_fib
                        )
                except Exception:
                    pass

                vols = [c["vol"] for c in h1_window]
                vsma = calc_sma(vols, 20)

                vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0

                stoch = calc_stochastic(
                    h1_window, 5, 3, 3
                )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

                # BUG 1 fix: use historical BTC bias at this bar (not hardcoded "neutral")
                btc_bias = _bt_btc_bias(d1_ctx, _pair_ctx)
                _bt_intermarket_ctx = None
                if _bt_intermarket_series_store is not None:
                    _bt_intermarket_ctx = build_point_in_time_context(
                        _pair_ctx,
                        all_pairs=getattr(_rt(), "ALL_PAIRS", []),
                        disabled_pairs=getattr(_rt(), "disabled_pairs", []),
                        etf_pairs=getattr(_rt(), "ETF_PAIRS", []),
                        series_store=_bt_intermarket_series_store,
                        cutoff_ts=entry_ts,
                        config=CONFIG,
                    )

                # Engine A v2: all asset classes (including forex) use calc_confluence → compute_factor_scores
                if False:  # forex branch retired — Engine A v2 handles forex natively
                    pass
                else:
                    _bt_funding_rate = None
                    _bt_oi_data = None
                    if _ptype == "crypto":
                        _prev_bt = h4_ts[i - 1] if i >= 1 else None
                        _bt_funding_rate, _bt_oi_data = _bt_crypto_funding_oi_for_bar(
                            _ptype,
                            _bt_crypto_funding_rows,
                            _bt_crypto_oi_rows,
                            entry_ts,
                            _prev_bt,
                        )
                    _bt_oi_ctx = build_oi_context_for_factor_scoring(
                        _bt_oi_data, d1_ctx, h1i.get("snap")
                    )
                    _bt_macro_ctx = _bt_gold_macro_context(
                        _pair_ctx,
                        entry_ts,
                        h4_window,
                        _bt_dxy_h4_raw,
                        _bt_dxy_h4_times,
                    )
                    res = calc_confluence(
                        d1i_ctx,
                        h4i,
                        h1i,
                        vr,
                        stoch,
                        _pair_ctx,
                        btc_bias,
                        d1_candles=d1_ctx,
                        h4_candles=h4_window,
                        h1_candles=h1_window,
                        volume_threshold=_bt_volume_threshold,
                        bar_time=h4_window[-1].get("time") if h4_window else None,
                        funding_rate=_bt_funding_rate,
                        oi_data=_bt_oi_data,
                        oi_context=_bt_oi_ctx,
                        macro_context=_bt_macro_ctx,
                        intermarket_context=_bt_intermarket_ctx,
                    )

            except Exception as _bt_bar_err:
                log.debug(
                    f"[BT] {pair['display']} bar {i} skipped: {_bt_bar_err}",
                    exc_info=False
                )
                i += 1
                continue

            funnel["total_setups"] += 1

            _ts = res.get("trendState", "UNKNOWN")

            _recent_scores.append(res["score"])

            if _structure_first_cfg.get("enabled"):
                _ea_direction = res.get("direction")
                try:
                    _structure_price = float(h4_window[-1]["close"])
                except (TypeError, ValueError, KeyError, IndexError):
                    _structure_price = None
                _structure_atr = _rt().atr_for_levels(
                    d1i_ctx, h4i, h1i, pair=pair, style=effective_style
                )
                _structure_ok, _structure_detail = _engine_a_structure_first_entry_check(
                    engine=_structure_first_engine,
                    pair=_pair_ctx,
                    direction=_ea_direction,
                    d1_candles=d1_ctx,
                    h4_candles=h4_window,
                    h1_candles=h1_window,
                    current_price=_structure_price,
                    atr=_structure_atr,
                    regime=res.get("regimeName") or ((res.get("regime") or {}).get("label")),
                    style=effective_style,
                    d1_snap=d1i_ctx.get("snap") if isinstance(d1i_ctx, dict) else None,
                    h4_snap=h4i.get("snap") if isinstance(h4i, dict) else None,
                    cfg=_structure_first_cfg,
                )
                if not _structure_ok:
                    funnel["fail_structure"] = funnel.get("fail_structure", 0) + 1
                    i += 1
                    continue

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            if is_trend_state_blocked(_ts, _pair_ctx, scope="backtest"):
                funnel["fail_regime"] = funnel.get("fail_regime", 0) + 1
                i += 1
                continue

            direction = res["direction"]

            # Indicator windows end at i-1, so the first non-lookahead fill is bar i open.

            if i >= total_h4:
                i += 1
                continue

            entry_bar = h4_raw[i]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            atr = _rt().atr_for_levels(
                d1i_ctx, h4i, h1i, pair=pair, style=effective_style
            )
            atr, level_atr_feed = _engine_a_level_atr_for_bt(
                atr, pair, effective_style, as_of=entry_bar.get("time")
            )

            if not atr or atr == 0:
                i += 1
                continue

            _slip_mult = 3.0 if _canonical_vm == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(
                _bar_with_slippage_context(entry_bar, atr=atr), _ptype
            ) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            _bt_regime_state2 = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _level_atr_class, regime_state=_bt_regime_state2,
                style=effective_style,
            )

            sl = lvl["sl"]
            tp1 = lvl["tp1"]
            tp2 = lvl["tp2"]

            sl_mult = lvl["mults"]["sl"]
            tp1_mult = lvl["mults"]["tp1"]
            tp2_mult = lvl["mults"]["tp2"]

            rr1 = lvl["rr1"]

            if not all(math.isfinite(v) and v > 0 for v in (sl, tp1, tp2)):
                i += 1
                continue

            if abs(entry - sl) < entry * 0.0001:
                i += 1
                continue

            # BUG 2 fix: 10 H4 bars = 40 hours — too short, produces noisy swing points.
            # Use 20 H4 bars (80 hours) to match the time depth of swing's 10 D1 bars (10 days).
            if _ptype == "crypto":
                _recent = h4_window[-20:]

                if direction == "LONG":
                    # BUG 5 fix: LONG SL is below price — only tighten (use swing if higher/closer)
                    swing_sl = min(c["low"] for c in _recent)
                    if swing_sl > sl:
                        sl = swing_sl

                else:
                    # BUG 5 fix: SHORT SL is above price — only tighten (use swing if lower/closer)
                    swing_sl = max(c["high"] for c in _recent)
                    if swing_sl < sl:
                        sl = swing_sl

                rr1 = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
                # Guard: skip if structural SL has degraded RR below minimum
                _min_rr = float(CONFIG.get("STYLE_ATR_MULTS", {}).get(
                    effective_style, {}).get(_ptype, {}).get("tp1", 1.5)) / float(
                    CONFIG.get("STYLE_ATR_MULTS", {}).get(
                    effective_style, {}).get(_ptype, {}).get("sl", 1.0))
                if rr1 < max(0.8, _min_rr * 0.7):   # allow 30% degradation before skipping
                    funnel["fail_score"] += 1
                    i += 1
                    continue

            # MAX_SL_PCT rejection — Ensuring backtest results reflect the same risk thresholds as live trading.
            _max_sl_pct = CONFIG.get("MAX_SL_PCT", {}).get(_ptype, 0.05)
            _sl_dist_pct = abs(float(entry) - float(sl)) / float(entry)
            if _sl_dist_pct > _max_sl_pct:
                log.debug(
                    f"[SL-CAP] {pair['display']} {direction} SL {_sl_dist_pct:.1%} "
                    f"exceeds cap {_max_sl_pct:.1%} — REJECTED"
                )
                funnel["fail_score"] += 1
                i += 1
                continue

            _atr_series = calc_atr(
                [c["high"] for c in h4_window],
                [c["low"] for c in h4_window],
                [c["close"] for c in h4_window],
                14,
            )

            _atr_sma = calc_sma([v for v in _atr_series if v is not None], 20)

            _vol_adj = 1.0

            _valid_atr_sma = [v for v in _atr_sma if v is not None]

            if _valid_atr_sma and _valid_atr_sma[-1] and _valid_atr_sma[-1] > 0:
                if atr > _valid_atr_sma[-1] * 1.5:
                    _vol_adj = 0.7

            _score_factor = (
                max(0.25, min(1.0, res["score"] / _pair_max_score))
                if _pair_max_score > 0
                else 1.0
            )

            outcome = "OPEN"
            result_r = 0.0
            exit_bar = i

            for j in range(i, min(i + MAX_HOLD, total_h4)):
                bar = h4_raw[j]
                _bar_outcome, _both_hit = _resolve_barrier_exit(
                    bar,
                    direction=direction,
                    sl=sl,
                    tp1=tp1,
                    tp2=tp2,
                )
                if _both_hit:
                    same_bar_both_hit += 1
                if _bar_outcome == "TP2":
                    outcome = "TP2"
                    _sl_dist_r = abs(entry - sl)
                    result_r = (abs(tp2 - entry) / _sl_dist_r) if _sl_dist_r > 0 else (tp2_mult / sl_mult)
                    exit_bar = j
                    break
                if _bar_outcome == "TP1":
                    outcome = "TP1"
                    result_r = rr1 - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "SL":
                    _sl_slip_r = (
                        _get_slippage_for_bar(_bar_with_slippage_context(bar, atr=atr), _ptype) * sl / (atr * sl_mult)
                        if atr and sl_mult
                        else 0
                    )
                    outcome = "SL"
                    result_r = round(-1.0 - _sl_slip_r, 4)
                    exit_bar = j
                    break
            if outcome == "OPEN":
                exit_bar = min(i + MAX_HOLD - 1, total_h4 - 1)
                _last_fwd = h4_raw[exit_bar]

                _exit_px = _last_fwd["close"]

                _sl_dist = abs(entry - sl)

                if _sl_dist > 0 and math.isfinite(_exit_px):
                    result_r = (
                        (_exit_px - entry) / _sl_dist
                        if direction == "LONG"
                        else (entry - _exit_px) / _sl_dist
                    )

                    result_r = round(max(-5.0, min(5.0, result_r)), 2)

                else:
                    result_r = 0.0

                outcome = "TIMEOUT"

            _exit_path = _engine_a_trade_path_diagnostics(
                h4_raw[i : min(exit_bar + 1, total_h4)],
                direction=direction,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
            )

            live_risk_pct = _live_base_risk_pct(_ptype)

            # F3: Deduct round-trip exchange fee from result_r

            _sl_dist_id = abs(entry - sl)

            if _sl_dist_id > 0:
                _fee_r_id = CONFIG["FEE_PCT"].get(_ptype, 0.0004) * entry / _sl_dist_id

                result_r = round(result_r - _fee_r_id, 4)

            # Apply the same base per-asset risk percentages as the live risk gateway.
            # (forex=0.6, crypto=0.8). Live risk_engine does NOT apply RISK_MULT —
            # it uses asset_risk_map in _adaptive_risk_pct() instead.
            # BT equity curves are therefore ~40% smaller than live for forex.
            # Do not compare BT Sharpe/SQN directly to live P&L without adjusting.
            equity_change = (
                result_r * live_risk_pct * _vol_adj * _score_factor
            )

            equity = round(equity * (1 + equity_change), 6)

            equity_curve.append(round(equity, 4))

            _regime = (
                _ts
                if _ts in ("TRENDING", "DEVELOPING", "RANGING", "DEAD RANGING")
                else "UNKNOWN"
            )

            open_positions += 1

            funnel["taken"] += 1

            from scoring import get_session

            trades.append(
                {
                    "date": entry_bar["time"][:10],
                    "pair": pair["display"],
                    "direction": direction,
                    "score": res["score"],
                    "entry": round(entry, 6),
                    "sl": round(sl, 6),
                    "tp1": round(tp1, 6),
                    "tp2": round(tp2, 6),
                    "outcome": outcome,
                    "resultR": round(result_r, 2),
                    "regime": _regime,
                    **build_strategy_lab_telemetry(
                        engine="ENGINE_A",
                        strategy_family="ENGINE_A_ONLY",
                        regime=_regime,
                        setup_type="standard",
                        timeframe="H4",
                        failure_reason=outcome if outcome not in ["TP1", "TP2"] else None,
                        entry_reason=None,
                        exit_reason=outcome,
                        source_module="backtest_runner",
                        source_function="backtest_pair"
                    ),
                    "oos": _vf["oos_label"],
                    "wf_fold": _vf["wf_fold"],
                    "validation_mode": _canonical_vm,
                    "volAdj": _vol_adj,
                    "factors": res.get("factor_scores") or {},
                    "factor_weights": res.get("factor_weights") or {},
                    "rsi": h4_window[-1].get("rsi") if h4_window else None,
                    "adx": h4_window[-1].get("adx") if h4_window else None,
                    "ema_directions": "aligned" if (h4_window and h4_window[-1].get("ema21", 0) > h4_window[-1].get("ema50", 0)) else "split",
                    "addon_score": res.get("factor_scores", {}).get("addon", 0.0),
                    "trend_score": res.get("factor_scores", {}).get("trend", 0.0),
                    "momentum_score": res.get("factor_scores", {}).get("momentum", 0.0),
                    "session": get_session(entry_bar["time"]).get("name", "UNKNOWN") if "time" in entry_bar else "UNKNOWN",
                    **_exit_path,
                }
            )

            if outcome not in ("OPEN",):
                last_exit_bar = exit_bar

                open_positions -= 1

            i = exit_bar + 1 if outcome != "OPEN" else i + 1

    elif effective_style == "scalp":  # H1 bar walk-forward
        MIN_H1 = 250  # Fixed indicator warmup — do not tie to H1_CANDLES fetch depth
        COOLDOWN = 1
        MAX_HOLD = 12
        MAX_OPEN = 3

        total_h1 = len(h1_raw)

        i = MIN_H1
        last_exit_bar = 0
        open_positions = 0

        h1_ts_sc = pd.to_datetime(
            [c["time"] for c in h1_raw], utc=True, errors="coerce"
        )

        d1_ts_sc = pd.to_datetime(
            [c["time"] for c in d1_raw], utc=True, errors="coerce"
        )

        h4_ts_sc = pd.to_datetime(
            [c["time"] for c in h4_raw], utc=True, errors="coerce"
        )

        while i < total_h1 - 1:
            _vf = backtest_bar_validation_state(
                i,
                min_bars=MIN_H1,
                total_bars=total_h1,
                temporal_mode=_temporal_vm,
                purge_gap=purge_gap,
                folds=folds,
            )
            if _vf["skip"]:
                i += 1
                continue
            if i - last_exit_bar < COOLDOWN:
                i += 1
                continue

            if open_positions >= MAX_OPEN:
                i += 1
                continue

            h1_window = h1_raw[i - MIN_H1 : i]

            entry_ts = h1_ts_sc[i]

            if pd.isna(entry_ts):
                i += 1
                continue

            # H4 context: all H4 bars before this H1 bar

            h4_idx = bisect.bisect_left(h4_ts_sc, entry_ts)
            h4_ctx = h4_raw[max(0, h4_idx - 250) : h4_idx]  # Fixed 250 bar lookback for indicators

            # D1 context: real D1 data up to this point for Weinstein/regime

            d1_idx = bisect.bisect_left(d1_ts_sc, entry_ts)
            d1_ctx = d1_raw[max(0, d1_idx - 220) : d1_idx]  # Fixed 220 bar lookback for indicators

            if len(h4_ctx) < 50 or len(d1_ctx) < 50:
                i += 1
                continue

            try:
                if bt_vectorized:
                    h1i = _bt_indicators_from_cache(
                        h1_raw,
                        end_idx=i - 1,
                        window=MIN_H1,
                        asset_type=pair.get("type", "stock"),
                        cache=h1_cache,
                    )
                    h4i_ctx = _bt_indicators_from_cache(
                        h4_raw,
                        end_idx=h4_idx - 1,
                        window=250,
                        asset_type=pair.get("type", "stock"),
                        cache=h4_cache,
                    )
                    d1i_ctx = _bt_indicators_from_cache(
                        d1_raw,
                        end_idx=d1_idx - 1,
                        window=220,
                        asset_type=pair.get("type", "stock"),
                        cache=d1_cache,
                    )
                    if not h1i or not h4i_ctx or not d1i_ctx:
                        i += 1
                        continue
                else:
                    h1i = calc_indicators_with_normalized(
                        h1_window, pair.get("type", "stock")
                    )
                    h4i_ctx = calc_indicators_with_normalized(
                        h4_ctx, pair.get("type", "stock")
                    )
                    d1i_ctx = calc_indicators_with_normalized(
                        d1_ctx, pair.get("type", "stock")
                    )

                vols = [c["vol"] for c in h1_window]
                vsma = calc_sma(vols, 20)

                vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0

                stoch = calc_stochastic(
                    h1_window, 5, 3, 3
                )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

                # BUG 1 fix: use historical BTC bias at this bar (not hardcoded "neutral")
                btc_bias = _bt_btc_bias(d1_ctx, _pair_ctx)
                _bt_intermarket_ctx = None
                if _bt_intermarket_series_store is not None:
                    _bt_intermarket_ctx = build_point_in_time_context(
                        _pair_ctx,
                        all_pairs=getattr(_rt(), "ALL_PAIRS", []),
                        disabled_pairs=getattr(_rt(), "disabled_pairs", []),
                        etf_pairs=getattr(_rt(), "ETF_PAIRS", []),
                        series_store=_bt_intermarket_series_store,
                        cutoff_ts=entry_ts,
                        config=CONFIG,
                    )

                # Engine A v2: all asset classes (including forex) use calc_confluence → compute_factor_scores
                if False:  # forex branch retired — Engine A v2 handles forex natively
                    pass
                else:
                    _bt_funding_rate = None
                    _bt_oi_data = None
                    if _ptype == "crypto":
                        _prev_bt = h1_ts_sc[i - 1] if i >= 1 else None
                        _bt_funding_rate, _bt_oi_data = _bt_crypto_funding_oi_for_bar(
                            _ptype,
                            _bt_crypto_funding_rows,
                            _bt_crypto_oi_rows,
                            entry_ts,
                            _prev_bt,
                        )
                    _bt_oi_ctx = build_oi_context_for_factor_scoring(
                        _bt_oi_data, d1_ctx, h1i.get("snap")
                    )
                    _bt_macro_ctx = _bt_gold_macro_context(
                        _pair_ctx,
                        entry_ts,
                        h4_ctx,
                        _bt_dxy_h4_raw,
                        _bt_dxy_h4_times,
                    )
                    res = calc_confluence(
                        d1i_ctx,
                        h4i_ctx,
                        h1i,
                        vr,
                        stoch,
                        _pair_ctx,
                        btc_bias,
                        d1_candles=d1_ctx,
                        h4_candles=h4_ctx,
                        h1_candles=h1_window,
                        volume_threshold=_bt_volume_threshold,
                        bar_time=h1_window[-1].get("time") if h1_window else None,
                        funding_rate=_bt_funding_rate,
                        oi_data=_bt_oi_data,
                        oi_context=_bt_oi_ctx,
                        macro_context=_bt_macro_ctx,
                        intermarket_context=_bt_intermarket_ctx,
                    )

            except Exception as _bt_bar_err:
                log.debug(
                    f"[BT] {pair['display']} bar {i} skipped: {_bt_bar_err}",
                    exc_info=False
                )
                i += 1
                continue

            funnel["total_setups"] += 1

            _ts = res.get("trendState", "UNKNOWN")

            _recent_scores.append(res["score"])

            if _structure_first_cfg.get("enabled"):
                _ea_direction = res.get("direction")
                try:
                    _structure_price = float(h1_window[-1]["close"])
                except (TypeError, ValueError, KeyError, IndexError):
                    _structure_price = None
                _structure_atr = _rt().atr_for_levels(
                    d1i_ctx, h4i_ctx, h1i, pair=pair, style=effective_style
                )
                _structure_ok, _structure_detail = _engine_a_structure_first_entry_check(
                    engine=_structure_first_engine,
                    pair=_pair_ctx,
                    direction=_ea_direction,
                    d1_candles=d1_ctx,
                    h4_candles=h4_ctx,
                    h1_candles=h1_window,
                    current_price=_structure_price,
                    atr=_structure_atr,
                    regime=res.get("regimeName") or ((res.get("regime") or {}).get("label")),
                    style=effective_style,
                    d1_snap=d1i_ctx.get("snap") if isinstance(d1i_ctx, dict) else None,
                    h4_snap=h4i_ctx.get("snap") if isinstance(h4i_ctx, dict) else None,
                    cfg=_structure_first_cfg,
                )
                if not _structure_ok:
                    funnel["fail_structure"] = funnel.get("fail_structure", 0) + 1
                    i += 1
                    continue

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            if is_trend_state_blocked(_ts, _pair_ctx, scope="backtest"):
                funnel["fail_regime"] = funnel.get("fail_regime", 0) + 1
                i += 1
                continue

            direction = res["direction"]

            if i >= total_h1:
                i += 1
                continue

            entry_bar = h1_raw[i]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            atr = _rt().atr_for_levels(
                d1i_ctx, h4i_ctx, h1i, pair=pair, style=effective_style
            )
            atr, level_atr_feed = _engine_a_level_atr_for_bt(
                atr, pair, effective_style, as_of=entry_bar.get("time")
            )

            if not atr or atr == 0:
                i += 1
                continue

            _slip_mult = 3.0 if _canonical_vm == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(
                _bar_with_slippage_context(entry_bar, atr=atr), _ptype
            ) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            _bt_regime_state3 = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _level_atr_class, regime_state=_bt_regime_state3,
                style=effective_style,
            )

            sl = lvl["sl"]
            tp1 = lvl["tp1"]
            tp2 = lvl["tp2"]

            sl_mult = lvl["mults"]["sl"]
            tp1_mult = lvl["mults"]["tp1"]
            tp2_mult = lvl["mults"]["tp2"]

            rr1 = lvl["rr1"]

            if not all(math.isfinite(v) and v > 0 for v in (sl, tp1, tp2)):
                i += 1
                continue

            if abs(entry - sl) < entry * 0.0001:
                i += 1
                continue

            # BUG 2 fix: 10 H1 bars = 10 hours — too short, produces noisy swing points.
            # Use 24 H1 bars (24 hours) to match the time depth of swing's 10 D1 bars (10 days).
            if _ptype == "crypto":
                _recent = h1_window[-24:]

                if direction == "LONG":
                    # BUG 5 fix: LONG SL is below price — only tighten (use swing if higher/closer)
                    swing_sl = min(c["low"] for c in _recent)
                    if swing_sl > sl:
                        sl = swing_sl

                else:
                    # BUG 5 fix: SHORT SL is above price — only tighten (use swing if lower/closer)
                    swing_sl = max(c["high"] for c in _recent)
                    if swing_sl < sl:
                        sl = swing_sl

                rr1 = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
                # Guard: skip if structural SL has degraded RR below minimum
                _min_rr = float(CONFIG.get("STYLE_ATR_MULTS", {}).get(
                    effective_style, {}).get(_ptype, {}).get("tp1", 1.5)) / float(
                    CONFIG.get("STYLE_ATR_MULTS", {}).get(
                    effective_style, {}).get(_ptype, {}).get("sl", 1.0))
                if rr1 < max(0.8, _min_rr * 0.7):   # allow 30% degradation before skipping
                    funnel["fail_score"] += 1
                    i += 1
                    continue

            # MAX_SL_PCT rejection — Ensuring backtest results reflect the same risk thresholds as live trading.
            _max_sl_pct = CONFIG.get("MAX_SL_PCT", {}).get(_ptype, 0.05)
            _sl_dist_pct = abs(float(entry) - float(sl)) / float(entry)
            if _sl_dist_pct > _max_sl_pct:
                log.debug(
                    f"[SL-CAP] {pair['display']} {direction} SL {_sl_dist_pct:.1%} "
                    f"exceeds cap {_max_sl_pct:.1%} — REJECTED"
                )
                funnel["fail_score"] += 1
                i += 1
                continue

            _atr_series = calc_atr(
                [c["high"] for c in h1_window],
                [c["low"] for c in h1_window],
                [c["close"] for c in h1_window],
                14,
            )

            _atr_sma = calc_sma([v for v in _atr_series if v is not None], 20)

            _vol_adj = 1.0

            _valid_atr_sma = [v for v in _atr_sma if v is not None]

            if _valid_atr_sma and _valid_atr_sma[-1] and _valid_atr_sma[-1] > 0:
                if atr > _valid_atr_sma[-1] * 1.5:
                    _vol_adj = 0.7

            _score_factor = (
                max(0.25, min(1.0, res["score"] / _pair_max_score))
                if _pair_max_score > 0
                else 1.0
            )

            outcome = "OPEN"
            result_r = 0.0
            exit_bar = i

            for j in range(i, min(i + MAX_HOLD, total_h1)):
                bar = h1_raw[j]
                _bar_outcome, _both_hit = _resolve_barrier_exit(
                    bar,
                    direction=direction,
                    sl=sl,
                    tp1=tp1,
                    tp2=tp2,
                )
                if _both_hit:
                    same_bar_both_hit += 1
                if _bar_outcome == "TP2":
                    outcome = "TP2"
                    _sl_dist_r = abs(entry - sl)
                    result_r = (abs(tp2 - entry) / _sl_dist_r) if _sl_dist_r > 0 else (tp2_mult / sl_mult)
                    exit_bar = j
                    break
                if _bar_outcome == "TP1":
                    outcome = "TP1"
                    result_r = rr1 - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "SL":
                    _sl_slip_r = (
                        _get_slippage_for_bar(_bar_with_slippage_context(bar, atr=atr), _ptype) * sl / (atr * sl_mult)
                        if atr and sl_mult
                        else 0
                    )
                    outcome = "SL"
                    result_r = round(-1.0 - _sl_slip_r, 4)
                    exit_bar = j
                    break
            if outcome == "OPEN":
                exit_bar = min(i + MAX_HOLD - 1, total_h1 - 1)
                _last_fwd = h1_raw[exit_bar]

                _exit_px = _last_fwd["close"]

                _sl_dist = abs(entry - sl)

                if _sl_dist > 0 and math.isfinite(_exit_px):
                    result_r = (
                        (_exit_px - entry) / _sl_dist
                        if direction == "LONG"
                        else (entry - _exit_px) / _sl_dist
                    )

                    result_r = round(max(-5.0, min(5.0, result_r)), 2)

                else:
                    result_r = 0.0

                outcome = "TIMEOUT"

            _exit_path = _engine_a_trade_path_diagnostics(
                h1_raw[i : min(exit_bar + 1, total_h1)],
                direction=direction,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
            )

            live_risk_pct = _live_base_risk_pct(_ptype)

            # F3: Deduct round-trip exchange fee from result_r

            _sl_dist_sc = abs(entry - sl)

            if _sl_dist_sc > 0:
                _fee_r_sc = CONFIG["FEE_PCT"].get(_ptype, 0.0004) * entry / _sl_dist_sc

                result_r = round(result_r - _fee_r_sc, 4)

            # Apply the same base per-asset risk percentages as the live risk gateway.
            # (forex=0.6, crypto=0.8). Live risk_engine does NOT apply RISK_MULT —
            # it uses asset_risk_map in _adaptive_risk_pct() instead.
            # BT equity curves are therefore ~40% smaller than live for forex.
            # Do not compare BT Sharpe/SQN directly to live P&L without adjusting.
            equity_change = (
                result_r * live_risk_pct * _vol_adj * _score_factor
            )

            equity = round(equity * (1 + equity_change), 6)

            equity_curve.append(round(equity, 4))

            _regime = (
                _ts
                if _ts in ("TRENDING", "DEVELOPING", "RANGING", "DEAD RANGING")
                else "UNKNOWN"
            )

            open_positions += 1

            funnel["taken"] += 1

            trades.append(
                {
                    "date": entry_bar["time"][:10],
                    "pair": pair["display"],
                    "direction": direction,
                    "score": res["score"],
                    "entry": round(entry, 6),
                    "sl": round(sl, 6),
                    "tp1": round(tp1, 6),
                    "tp2": round(tp2, 6),
                    "outcome": outcome,
                    "resultR": round(result_r, 2),
                    "regime": _regime,
                    **build_strategy_lab_telemetry(
                        engine="ENGINE_A",
                        strategy_family="ENGINE_A_ONLY",
                        regime=_regime,
                        setup_type="standard",
                        timeframe="H1",
                        failure_reason=outcome if outcome not in ["TP1", "TP2"] else None,
                        entry_reason=None,
                        exit_reason=outcome,
                        source_module="backtest_runner",
                        source_function="backtest_pair"
                    ),
                    "oos": _vf["oos_label"],
                    "wf_fold": _vf["wf_fold"],
                    "validation_mode": _canonical_vm,
                    "volAdj": _vol_adj,
                    "factors": res.get("factor_scores") or {},
                    "factor_weights": res.get("factor_weights") or {},
                    **_exit_path,
                }
            )

            if outcome not in ("OPEN",):
                last_exit_bar = exit_bar

                open_positions -= 1

            i = exit_bar + 1 if outcome != "OPEN" else i + 1

    if not trades:
        _max_score_seen = round(max(_recent_scores), 3) if _recent_scores else 0
        _bt_min_used = bt_min
        _h4_bars = len(h4_raw) if h4_raw else 0
        _h1_bars = len(h1_raw) if h1_raw else 0
        log.warning(
            f"No signals generated for {pair['display']} — "
            f"setups={funnel['total_setups']} skip_window={funnel['skip_window']} "
            f"fail_score={funnel['fail_score']} evalThreshold={_bt_min_used} "
            f"max_score_seen={_max_score_seen} "
            f"d1={len(d1_raw)} h4={len(h4_raw)} h1={len(h1_raw)}"
        )
        try:
            _bh = (
                round((d1_raw[-1]["close"] / d1_raw[0]["close"] - 1) * 100, 2)
                if d1_raw and d1_raw[0].get("close")
                else None
            )
        except Exception:
            _bh = None
        _calibration_report = calibration_report(
            records=[],
            engine="engine_a",
            asset_class=pair.get("type", ""),
            style=effective_style,
            default_max_score=_pair_max_score,
        )
        _research_metrics = build_research_metrics(
            [],
            observed_sharpe=0.0,
        )
        _meta_report = meta_report(
            {
                "engine": "engine_a",
                "type": pair.get("type"),
                "style": effective_style,
                "regime": None,
            },
            records=[],
        )
        _empty_out = {
            "pair": pair["display"],
            "symbol": pair["symbol"],
            "type": pair.get("type", ""),
            "totalTrades": 0,
            "wins": 0,
            "losses": 0,
            "winRate": 0,
            "profitFactor": None,
            "totalR": 0,
            "expectancy": 0,
            "sqn": 0,
            "sharpe": 0,
            "sortino": 0,
            "avgWin": 0,
            "avgLoss": 0,
            "rSkew": None,
            "maxDrawdownPct": 0,
            "maxRecoveryBars": 0,
            "mcDD": {"p5": 0, "p50": 0, "p95": 0},
            "scoreBands": {},
            "regimeStats": {},
            "wfSplit": {
                "is_trades": 0,
                "oos_trades": 0,
                "is_sqn": None,
                "oos_sqn": 0,
                "overfit_flag": False,
                "wf_note": "No trades — walk-forward not applicable",
            },
            "funnel": funnel,
            "btStyle": effective_style,
            "btStyleRequested": requested_style,
            "quantileGateNote": _engine_a_bt_gate_note(),
            "evalThreshold": bt_min,
            "scanQuantileEnabled": CONFIG.get("SCAN_QUANTILE_ENABLED", True),
            "bhReturn": _bh,
            "calibrationReport": _calibration_report,
            "researchMetrics": _research_metrics,
            "metaReport": _meta_report,
            "volumeThreshold": {
                "bt": CONFIG.get("VOLUME_THRESHOLD_BACKTEST", 1.2),
                "live": CONFIG.get("VOLUME_THRESHOLD", 1.5),
            },
            "exitDiagnostics": _engine_a_exit_diagnostics_summary(
                [], same_bar_both_hit
            ),
            "same_bar_both_hit": same_bar_both_hit,
            "pairMaxScore": _pair_max_score,
            "equityCurve": [1.0],
            "trades": [],
        }
        _attach_research_validation_payload(
            _empty_out,
            [],
            canonical_vm=_canonical_vm,
            temporal_vm=_temporal_vm,
            purge_gap=purge_gap,
            folds=folds,
            mode_warning=_vm_mode_warning,
        )
        return _empty_out

    wins = [
        t
        for t in trades
        if t["outcome"] in ("TP1", "TP2")
        or (t["outcome"] == "TIMEOUT" and t["resultR"] > 0)
    ]

    losses = [
        t
        for t in trades
        if t["outcome"] == "SL" or (t["outcome"] == "TIMEOUT" and t["resultR"] <= 0)
    ]

    gross_profit = sum(t["resultR"] for t in wins)

    gross_loss = abs(sum(t["resultR"] for t in losses))

    win_rate = round(len(wins) / len(trades) * 100, 1) if trades else 0

    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

    total_r = round(sum(t["resultR"] for t in trades), 2)

    r_values = [t["resultR"] for t in trades]

    avg_r = round(total_r / len(trades), 3) if trades else 0

    _var = (
        sum((r - avg_r) ** 2 for r in r_values) / (len(r_values) - 1)
        if len(r_values) > 1
        else 0
    )

    sqn = (
        round(max(-10, min(10, (avg_r / _var**0.5) * (len(trades) ** 0.5))), 2)
        if len(trades) > 1 and avg_r != 0 and _var > 0
        else 0
    )

    peak = 1.0
    max_dd = 0.0

    for e in equity_curve:
        if e > peak:
            peak = e

        dd = (peak - e) / peak

        if dd > max_dd:
            max_dd = dd

    max_dd_pct = round(max_dd * 100, 1)

    # B2a: Recovery time — max bars from drawdown start to equity peak recovery

    _peak_eq = 1.0
    _in_dd_since = 0
    max_recovery_bars = 0

    for _idx, _eq in enumerate(equity_curve):
        if _eq >= _peak_eq:
            if _in_dd_since:
                max_recovery_bars = max(max_recovery_bars, _idx - _in_dd_since)

                _in_dd_since = 0

            _peak_eq = _eq

        elif not _in_dd_since:
            _in_dd_since = _idx

    # A4: Expectancy decomposition

    avg_win = round(sum(t["resultR"] for t in wins) / len(wins), 3) if wins else 0

    avg_loss = (
        round(sum(t["resultR"] for t in losses) / len(losses), 3) if losses else 0
    )

    r_skew = round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else None

    # Sharpe / Sortino (annualized on R-multiples; shared with live /api/performance helpers)

    _trade_date0 = str(trades[0].get("date", ""))[:10] if trades else None
    _trade_date1 = str(trades[-1].get("date", ""))[:10] if trades else None
    _trades_per_year = compute_trades_per_year_from_span(len(trades), _trade_date0, _trade_date1)

    sharpe = annualized_sharpe_from_r_multiples(r_values, _trades_per_year, decimals=2)

    sortino = annualized_sortino_engine_a_from_r_multiples(
        r_values, _trades_per_year, decimals=2
    )

    # B2: Monte Carlo drawdown simulation â€" 500 random shuffles of trade sequence

    # Transforms single-path DD into distribution: P5=best case, P95=worst case

    import random as _rnd

    _risk_pct = _live_base_risk_pct(pair["type"])

    _mc_dds = []

    for _ in range(500):
        _shuffled = r_values[:]
        _rnd.shuffle(_shuffled)

        _eq = 1.0
        _pk = 1.0
        _mdd = 0.0

        for _r in _shuffled:
            _eq *= 1 + _r * _risk_pct

            if _eq > _pk:
                _pk = _eq

            _d = (_pk - _eq) / _pk

            if _d > _mdd:
                _mdd = _d

        _mc_dds.append(_mdd)

    _mc_dds.sort()
    _nc = len(_mc_dds)

    mc_dd = {
        "p5": round(_mc_dds[int(_nc * 0.05)] * 100, 1),
        "p50": round(_mc_dds[int(_nc * 0.50)] * 100, 1),
        "p95": round(_mc_dds[int(_nc * 0.95)] * 100, 1),
    }

    # B3: Score band win rate tracking â€" which confluence scores actually deliver edge?

    score_bands = {}

    for band_label, lo_b, hi_b in [
        ("<1.2", 0.0, 1.2),
        ("1.2-1.6", 1.2, 1.6),
        ("1.6-2.0", 1.6, 2.0),
        ("2.0+", 2.0, 99),
    ]:
        band_trades = [t for t in trades if lo_b <= t["score"] < hi_b]

        if band_trades:
            bw = sum(1 for t in band_trades if t["outcome"] in ("TP1", "TP2"))

            score_bands[band_label] = {
                "trades": len(band_trades),
                "wr": round(bw / len(band_trades) * 100, 1),
            }

    # R2: Regime segmentation stats â€" track performance by market regime

    regime_stats = {}

    for regime in ["TRENDING", "DEVELOPING", "RANGING", "DEAD RANGING"]:
        rt = [t for t in trades if t.get("regime") == regime]

        if rt:
            rw = sum(1 for t in rt if t["outcome"] in ("TP1", "TP2"))

            regime_stats[regime] = {
                "trades": len(rt),
                "wr": round(rw / len(rt) * 100, 1),
                "expectancy": round(sum(t["resultR"] for t in rt) / len(rt), 3),
            }

    # R4: Walk-forward split â€" in-sample vs out-of-sample SQN comparison

    is_trades = [t for t in trades if not t.get("oos", False)]

    oos_trades = [t for t in trades if t.get("oos", False)]

    def _calc_sqn(tlist):

        if len(tlist) < 2:
            return 0

        rv = [t["resultR"] for t in tlist]

        _a = sum(rv) / len(rv)

        _v = sum((r - _a) ** 2 for r in rv) / (len(rv) - 1) if len(rv) > 1 else 0

        return (
            round(max(-10, min(10, (_a / _v**0.5) * (len(rv) ** 0.5))), 2)
            if _a != 0 and _v > 0
            else 0
        )

    is_sqn = _calc_sqn(is_trades)

    oos_sqn = _calc_sqn(oos_trades)

    _is_insufficient = len(is_trades) < 5

    _sq_warn_n = max(3, int(CONFIG.get("BT_LOW_SAMPLE_SQ_WARN_TRADES", 30) or 30))

    wf_split = {
        "is_trades": len(is_trades),
        "oos_trades": len(oos_trades),
        "is_sqn": None if _is_insufficient else is_sqn,
        "oos_sqn": oos_sqn,
        "overfit_flag": oos_sqn < is_sqn * 0.5
        if is_sqn > 0 and len(oos_trades) >= 3
        else False,
        "wf_note": "IS period had insufficient setups (<5 trades) — OOS result is fully out-of-sample"
        if _is_insufficient
        else None,
        "lowSampleSqnWarning": len(trades) < _sq_warn_n,
        "lowSampleSqnTradeFloor": _sq_warn_n,
    }

    # F5: Buy-and-hold benchmark — passive return over full D1 data period

    try:
        bh_return = (
            round((d1_raw[-1]["close"] / d1_raw[0]["close"] - 1) * 100, 2)
            if d1_raw and d1_raw[0]["close"]
            else None
        )

    except Exception:
        bh_return = None

    log.warning(
        f"[BT] {pair['display']} done: {len(trades)} trades, WR {win_rate}%, PF {profit_factor}, Expect {avg_r}R, SQN {sqn}, Sharpe {sharpe}, Sortino {sortino}, IS:{is_sqn}/OOS:{oos_sqn}, MC-P95 DD {mc_dd['p95']}%, MaxRec {max_recovery_bars} bars"
    )

    # Save backtest result to DB
    try:
        import sqlite3 as _sq

        with _sq.connect(_rt().AUDIT_DB) as _con:
            _con.execute(
                "INSERT INTO backtest_results "
                "(run_date,pair,asset_type,engine,trades,win_rate,profit_factor,"
                "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,bt_min,atr_source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    __import__("datetime").datetime.utcnow().isoformat(),
                    pair["display"],
                    pair.get("type", ""),
                    "forex_scoring"
                    if pair.get("type") == "forex"
                    else "factor_scoring",
                    len(trades),
                    round(win_rate, 4),
                    round(profit_factor, 4),
                    round(avg_r, 4),
                    round(sqn, 4),
                    round(sharpe, 4),
                    round(sortino, 4),
                    round(is_sqn, 4),
                    round(oos_sqn, 4),
                    round(max_dd_pct, 4),
                    float(bt_min),
                    "D1_ATR" if pair.get("type") != "crypto" else "H4_ATR",
                ),
            )
        log.info(
            f"[BT-DB] Saved: {pair['display']} SQN={sqn:.2f} ({len(trades)} trades) → audit.db"
        )
    except Exception as _dbe:
        log.debug(f"[BT-DB] Failed to save result: {_dbe}")

    try:
        _pass_rate = None
        if funnel.get("total_setups", 0):
            _pass_rate = funnel.get("taken", 0) / float(funnel["total_setups"])
        record_backtest_summary(
            engine="engine_a",
            pass_rate=_pass_rate,
            expectancy_r=avg_r,
            score=min(1.0, max(0.0, win_rate / 100.0)),
            max_score=1.0,
            feature_map={
                "win_rate": win_rate / 100.0,
                "sqn": sqn,
                "profit_factor": profit_factor,
            },
            db_path=_rt().AUDIT_DB,
            meta={
                "pair": pair.get("display"),
                "style": effective_style,
                "asset_type": pair.get("type"),
                "runtime_only_metric": False,
            },
        )
    except Exception as _ssi_err:
        log.debug(f"[SSI] Engine A backtest sample skipped: {_ssi_err}")

    calibration_summary = calibration_report(
        records=_records_for_calibration(
            trades,
            engine="engine_a",
            asset_class=pair.get("type", ""),
            style=effective_style,
            default_max_score=_pair_max_score,
        ),
        engine="engine_a",
        asset_class=pair.get("type", ""),
        style=effective_style,
        default_max_score=_pair_max_score,
    )
    research_metrics = build_research_metrics(
        r_values,
        observed_sharpe=sharpe,
        num_trials=int(CONFIG.get("BT_NUM_VARIANTS_TRIED", 1) or 1),
        bootstrap_iterations=int(CONFIG.get("BT_BOOTSTRAP_CI_ITERATIONS", 1000) or 0),
        trades_per_year=_trades_per_year,
    )
    meta_summary = meta_report(
        {
            "engine": "engine_a",
            "type": pair.get("type"),
            "style": effective_style,
            "regime": trades[-1].get("regime") if trades else None,
        },
        records=_records_for_meta(
            trades,
            engine="engine_a",
            asset_class=pair.get("type", ""),
            style=effective_style,
        ),
    )

    _bt_metrics_notes: list[str] = []
    if pair.get("type") == "forex":
        _bt_metrics_notes.append(
            "Forex BT compounds R at base risk_pct; aggregate path can diverge from live MT5 paper audits — "
            "align risk_scale before comparing Sharpe/SQN to live."
        )

    _bt_result = {
        "pair": pair["display"],
        "symbol": pair.get("symbol") or pair.get("display"),
        "type": pair["type"],
        "totalTrades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": win_rate,
        "profitFactor": profit_factor,
        "totalR": total_r,
        "expectancy": avg_r,
        "sqn": sqn,
        "sharpe": sharpe,
        "sortino": sortino,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "rSkew": r_skew,
        "maxDrawdownPct": max_dd_pct,
        "maxRecoveryBars": max_recovery_bars,
        "mcDD": mc_dd,
        "scoreBands": score_bands,
        "regimeStats": regime_stats,
        "wfSplit": wf_split,
        "funnel": funnel,
        "btStyle": effective_style,
        "btStyleRequested": requested_style,
        "quantileGateNote": _engine_a_bt_gate_note(),
        "evalThreshold": bt_min,
        "metricsInterpretationNotes": _bt_metrics_notes,
        "scanQuantileEnabled": CONFIG.get("SCAN_QUANTILE_ENABLED", True),
        "bhReturn": bh_return,
        "calibrationReport": calibration_summary,
        "researchMetrics": research_metrics,
        "metaReport": meta_summary,
        "volumeThreshold": {
            "bt": CONFIG.get("VOLUME_THRESHOLD_BACKTEST", 1.2),
            "live": CONFIG.get("VOLUME_THRESHOLD", 1.5),
        },
        "exitDiagnostics": _engine_a_exit_diagnostics_summary(
            trades, same_bar_both_hit
        ),
        "same_bar_both_hit": same_bar_both_hit,
        "pairMaxScore": _pair_max_score,
        "equityCurve": equity_curve,
        "trades": trades,
    }
    _attach_research_validation_payload(
        _bt_result,
        trades,
        canonical_vm=_canonical_vm,
        temporal_vm=_temporal_vm,
        purge_gap=purge_gap,
        folds=folds,
        mode_warning=_vm_mode_warning,
    )
    return _bt_result


def _format_backtest_results(
    trades,
    pair,
    engine_type="NAKED",
    same_bar_both_hit: int = 0,
    validation_mode: str = "standard",
):
    """Format Engine B backtest results to match Engine A's response schema exactly."""
    if not trades:
        return {
            "error": f"No signals generated for {pair['display']} in NAKED mode",
            "same_bar_both_hit": same_bar_both_hit,
        }

    # Use resultR as primary (Engine A schema); fall back to r_multiple for older records
    def _r(t):
        return t.get("resultR", t.get("r_multiple", 0.0))

    def _sqn_for(_trades):
        if len(_trades) <= 1:
            return 0
        _values = [_r(t) for t in _trades]
        _avg = sum(_values) / len(_values)
        _var_local = (
            sum((r - _avg) ** 2 for r in _values) / (len(_values) - 1)
            if len(_values) > 1
            else 0
        )
        return (
            round(max(-10, min(10, (_avg / _var_local**0.5) * (len(_trades) ** 0.5))), 2)
            if len(_trades) > 1 and _avg != 0 and _var_local > 0
            else 0
        )

    wins = [t for t in trades if _r(t) > 0]
    losses = [t for t in trades if _r(t) <= 0]
    gross_profit = sum(_r(t) for t in wins)
    gross_loss = abs(sum(_r(t) for t in losses))
    win_rate = round(len(wins) / len(trades) * 100, 1)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
    total_r = round(sum(_r(t) for t in trades), 2)
    r_values = [_r(t) for t in trades]
    _trade_day_hints: list[str | None] = []
    for t in trades:
        d = t.get("date") or t.get("time")
        raw = str(d).strip() if d is not None else ""
        _trade_day_hints.append(raw[:10] if len(raw) >= 10 else None)

    avg_r = round(total_r / len(trades), 3) if trades else 0
    avg_win = round(sum(_r(t) for t in wins) / len(wins), 3) if wins else 0
    avg_loss = round(sum(_r(t) for t in losses) / len(losses), 3) if losses else 0

    # SQN
    _var = (
        sum((r - avg_r) ** 2 for r in r_values) / (len(r_values) - 1)
        if len(r_values) > 1
        else 0
    )
    sqn = (
        round(max(-10, min(10, (avg_r / _var**0.5) * (len(trades) ** 0.5))), 2)
        if len(trades) > 1 and avg_r != 0 and _var > 0
        else 0
    )

    # R-skew (avg_win / |avg_loss|)
    r_skew = round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else None

    _tpy = trades_per_year_from_parallel_trade_days(r_values, _trade_day_hints)

    sharpe = annualized_sharpe_from_r_multiples(r_values, _tpy, decimals=2)
    sortino = annualized_sortino_engine_a_from_r_multiples(r_values, _tpy, decimals=2)

    _eq_risk_pct = float(_live_base_risk_pct(str(pair.get("type") or "stock")))

    # Equity compounds R with live base risk_pct (parity with Engine A MC paths).
    equity_curve = []
    eq = 1.0
    for rv in r_values:
        eq = round(eq * (1.0 + float(rv) * _eq_risk_pct), 4)
        equity_curve.append(eq)

    # Max drawdown
    peak = 1.0
    max_dd_pct = 0.0
    for eq_val in equity_curve:
        if eq_val > peak:
            peak = eq_val
        dd = (peak - eq_val) / peak * 100
        if dd > max_dd_pct:
            max_dd_pct = dd
    max_dd_pct = round(max_dd_pct, 2)

    _sq_warn_n = max(3, int(CONFIG.get("BT_LOW_SAMPLE_SQ_WARN_TRADES", 30) or 30))

    # Max recovery bars calculation
    _peak_eq = 1.0
    _recovery_start = None
    max_recovery_bars = 0
    for _idx, eq_val in enumerate(equity_curve):
        if eq_val >= _peak_eq:
            if _recovery_start is not None:
                max_recovery_bars = max(max_recovery_bars, _idx - _recovery_start)
                _recovery_start = None
            _peak_eq = eq_val
        elif _recovery_start is None:
            _recovery_start = _idx

    is_trades = [t for t in trades if not t.get("oos")]
    oos_trades = [t for t in trades if t.get("oos")]
    wf_split = {
        "is_trades": len(is_trades),
        "oos_trades": len(oos_trades),
        "is_sqn": _sqn_for(is_trades),
        "oos_sqn": _sqn_for(oos_trades) if oos_trades else None,
        "overfit_flag": False,
        "wf_note": "Engine B walk-forward split uses trade oos flags",
        "lowSampleSqnWarning": len(trades) < _sq_warn_n,
        "lowSampleSqnTradeFloor": _sq_warn_n,
    }

    # Monte Carlo drawdown simulation (500 shuffles)
    mc_dd_p50 = 0.0
    mc_dd_p95 = 0.0
    if len(r_values) >= 10:
        import random
        _mc_dds = []
        for _ in range(500):
            _shuffled = list(r_values)
            random.shuffle(_shuffled)
            _mc_eq = 1.0
            _mc_peak = 1.0
            _mc_max_dd = 0.0
            for rv in _shuffled:
                _mc_eq = _mc_eq * (1.0 + float(rv) * _eq_risk_pct)
                if _mc_eq > _mc_peak:
                    _mc_peak = _mc_eq
                _dd = (_mc_peak - _mc_eq) / _mc_peak * 100 if _mc_peak > 0 else 0
                if _dd > _mc_max_dd:
                    _mc_max_dd = _dd
            _mc_dds.append(_mc_max_dd)
        _mc_dds.sort()
        mc_dd_p50 = round(_mc_dds[len(_mc_dds) // 2], 2)
        mc_dd_p95 = round(_mc_dds[int(len(_mc_dds) * 0.95)], 2)
    mc_dd = {"p50": mc_dd_p50, "p95": mc_dd_p95, "p99": round(mc_dd_p95 * 1.15, 2)}

    # Confluence element analysis
    _ob_trades = [t for t in trades if t.get("ob_at_zone")]
    _mtf_trades = [t for t in trades if t.get("bos_mtf_confirmed")]
    _breaker_trades = [t for t in trades if t.get("breaker_active")]
    _fvg_trades = [t for t in trades if t.get("fvg_overlap")]

    def _wr(subset):
        if not subset:
            return None
        return round(len([t for t in subset if _r(t) > 0]) / len(subset) * 100, 1)

    confluence_analysis = {
        "ob_at_zone": {"count": len(_ob_trades), "wr": _wr(_ob_trades)},
        "bos_mtf": {"count": len(_mtf_trades), "wr": _wr(_mtf_trades)},
        "breaker": {"count": len(_breaker_trades), "wr": _wr(_breaker_trades)},
        "fvg_overlap": {"count": len(_fvg_trades), "wr": _wr(_fvg_trades)},
    }

    _metrics_interpretation: list[str] = []
    if pair.get("type") == "forex":
        _metrics_interpretation.append(
            "Forex BT equity compounds R at base risk_pct; live path adapters may realise different increments — "
            "compare BT Sharpe/SQN vs live audit cautiously."
        )

    result = {
        # ── Core identity (matches Engine A) ──────────────────────────────────
        "pair": pair["display"],
        "symbol": pair.get("symbol") or pair.get("display"),
        "type": pair["type"],
        # ── Trade stats ───────────────────────────────────────────────────────
        "totalTrades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": win_rate,
        "profitFactor": profit_factor,
        "totalR": total_r,
        "expectancy": avg_r,
        "sqn": sqn,
        "sharpe": sharpe,
        "sortino": sortino,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "rSkew": r_skew,
        # ── Risk/DD ───────────────────────────────────────────────────────────
        "maxDrawdownPct": max_dd_pct,
        "maxRecoveryBars": max_recovery_bars,
        "mcDD": mc_dd,
        "mcDdP50": mc_dd_p50,
        "mcDdP95": mc_dd_p95,
        # ── Analysis ──────────────────────────────────────────────────────────
        "scoreBands": {},
        "regimeStats": {},
        "funnel": {},
        "wfSplit": wf_split,
        "confluenceAnalysis": confluence_analysis,
        # ── Style / engine ────────────────────────────────────────────────────
        "btStyle": "naked",
        "btStyleRequested": "naked",
        "engine": engine_type,
        "same_bar_both_hit": same_bar_both_hit,
        # ── Benchmarks ────────────────────────────────────────────────────────
        "bhReturn": None,
        "pairMaxScore": None,
        "volumeThreshold": {
            "bt": CONFIG.get("VOLUME_THRESHOLD_BACKTEST", 1.2),
            "live": CONFIG.get("VOLUME_THRESHOLD", 1.5),
        },
        "metricsInterpretationNotes": _metrics_interpretation,
        # ── Curves & trades (CRITICAL — JS crashes without these) ─────────────
        "equityCurve": equity_curve,
        "trades": trades,
    }

    liquidity_pct = sum(1 for t in trades if t.get("liquidity_sweep")) / len(trades) * 100
    zone_touch_pct = sum(1 for t in trades if t.get("zone_touched")) / len(trades) * 100
    breakout_pct = (
        sum(
            1
            for t in trades
            if t.get("trigger_pattern") in ("INSIDE_BREAK", "STRONG_CLOSE", "ENGULFING")
        )
        / len(trades)
        * 100
    )
    rejection_pct = (
        sum(1 for t in trades if t.get("trigger_pattern") == "REJECTION")
        / len(trades)
        * 100
    )
    trigger_counts = {}
    for t in trades:
        _pattern = str(t.get("trigger_pattern") or "NONE")
        trigger_counts[_pattern] = trigger_counts.get(_pattern, 0) + 1
    dominant_trigger = (
        max(trigger_counts.items(), key=lambda kv: kv[1])[0] if trigger_counts else "NONE"
    )

    # ── Forex-specific Engine B metrics ───────────────────────────────────────
    if pair.get("type") == "forex":
        fvg_avg = sum(t.get("fvg_bonus", 0) for t in trades) / len(trades)
        volume_avg = sum(t.get("volume_strength", 0) for t in trades) / len(trades)
        fvg_overlap_pct = (
            sum(1 for t in trades if t.get("fvg_overlap")) / len(trades) * 100
        )
        result.update(
            {
                "fvg_avg_bonus": round(fvg_avg, 3),
                "avg_volume_strength": round(volume_avg, 3),
                "fvg_overlap_pct": round(fvg_overlap_pct, 1),
                "engine": "ENGINE_B_FOREX",
            }
        )
    else:
        result.update(
            {
                "liquidity_sweep_pct": round(liquidity_pct, 1),
                "zone_touch_pct": round(zone_touch_pct, 1),
                "breakout_entry_pct": round(breakout_pct, 1),
                "rejection_entry_pct": round(rejection_pct, 1),
                "dominant_trigger_pattern": dominant_trigger,
            }
        )


    # --- REGIME SEGMENTED REPORTING (R from resultR/r_multiple via _r) ---
    regimes = {}
    for t in trades:
        rgm = t.get("regime", "UNKNOWN")
        r_mult = float(_r(t))
        if rgm not in regimes:
            regimes[rgm] = {"trades": 0, "wins": 0, "r_sum": 0.0}
        regimes[rgm]["trades"] += 1
        regimes[rgm]["r_sum"] += r_mult
        if r_mult > 0:
            regimes[rgm]["wins"] += 1

    for k, v in regimes.items():
        v["win_rate"] = round(v["wins"] / max(1, v["trades"]), 4)
        v["expectancy"] = round(v["r_sum"] / max(1, v["trades"]), 4)

    result["regime_performance"] = regimes
    result["validation_mode"] = validation_mode

    _tpy_trade = trades_per_year_from_parallel_trade_days(r_values, _trade_day_hints)
    return enrich_backtest_summary(
        result,
        returns=r_values,
        num_trials=int(CONFIG.get("BT_NUM_VARIANTS_TRIED", 1) or 1),
        bootstrap_iterations=int(CONFIG.get("BT_BOOTSTRAP_CI_ITERATIONS", 1000) or 0),
        trades_per_year=_tpy_trade,
    )



# ── NEW: Engine B (Naked) Backtest Function ───────────────────────────────
def backtest_pair_naked(pair: dict, style: str = "naked", validation_mode="standard", purge_gap=200, folds=3):
    """Separate backtest loop for NakedEngine (Engine B).
    Completely isolated from Engine A backtest_pair()."""
    from market_structure import engine as naked_engine, engine_b_confidence_passes
    from indicators import calc_atr

    log.info(f"[ENGINE B BT] {pair['display']} fetching data... style={style}")

    # Use same extended data fetch as Engine A backtest — live cache only holds ~180d.
    if pair.get("source") == "binance":
        # Crypto: paginated Binance fetch — 730 days: D1=750, H4=4400, H1=17600
        candles_d1 = _crypto_bt_signal_candles(
            pair,
            engine="B",
            tf="D1",
            limit=750,
            min_bars=230,
        )
        candles_h4 = _crypto_bt_signal_candles(
            pair,
            engine="B",
            tf="H4",
            limit=4400,
            min_bars=500,
        )
        candles_h1 = _crypto_bt_signal_candles(
            pair,
            engine="B",
            tf="H1",
            limit=17600,
            min_bars=500,
        )
    elif pair.get("source") == "mt5":
        # MT5 pairs: Engine B price candles must remain MT5-sourced.
        candles_d1 = _bt_cached_fetch(
            pair,
            "D1",
            750,
            lambda lim: _rt().fetch_candles(pair, "D1", lim),
            provider="mt5",
            min_bars=230,
        )
        candles_h4 = _bt_cached_fetch(
            pair,
            "H4",
            4400,
            lambda lim: _rt().fetch_candles(pair, "H4", lim),
            provider="mt5",
            min_bars=500,
        )
        candles_h1 = _bt_cached_fetch(
            pair,
            "H1",
            17600,
            lambda lim: _rt().fetch_candles(pair, "H1", lim),
            provider="mt5",
            min_bars=500,
        )
    else:
        # Non-MT5, non-Binance pairs: use the source-specific live candle router.
        _provider = str(pair.get("source") or "source_router")
        candles_d1 = _bt_cached_fetch(
            pair,
            "D1",
            750,
            lambda lim: _rt().fetch_candles(pair, "D1", lim),
            provider=_provider,
            min_bars=230,
        )
        candles_h4 = _bt_cached_fetch(
            pair,
            "H4",
            4400,
            lambda lim: _rt().fetch_candles(pair, "H4", lim),
            provider=_provider,
            min_bars=500,
        )
        candles_h1 = _bt_cached_fetch(
            pair,
            "H1",
            17600,
            lambda lim: _rt().fetch_candles(pair, "H1", lim),
            provider=_provider,
            min_bars=500,
        )

    if not candles_d1 or not candles_h4 or not candles_h1:
        log.warning(
            f"[ENGINE B BT] {pair['display']} insufficient candle data "
            f"(D1={len(candles_d1 or [])}, H4={len(candles_h4 or [])}, H1={len(candles_h1 or [])})"
        )
        _cv, _mw = normalize_validation_mode(validation_mode)
        _tv = temporal_validation_mode(_cv)
        _early = {
            "success": False,
            "error": "Insufficient candle data (D1, H4, or H1 missing).",
            "trades": [],
            "totalTrades": 0,
        }
        _attach_research_validation_payload(
            _early,
            [],
            canonical_vm=_cv,
            temporal_vm=_tv,
            purge_gap=purge_gap,
            folds=folds,
            mode_warning=_mw,
        )
        return _early

    candles_d1 = candles_d1[:-1] if len(candles_d1) > 1 else candles_d1
    candles_h4 = candles_h4[:-1] if len(candles_h4) > 1 else candles_h4
    candles_h1 = candles_h1[:-1] if len(candles_h1) > 1 else candles_h1

    _canonical_vm, _vm_mode_warning = normalize_validation_mode(validation_mode)
    _temporal_vm = temporal_validation_mode(_canonical_vm)
    _min_entry_bt = 50
    if _vm_mode_warning:
        log.warning("[ENGINE B BT] %s: %s", pair.get("display"), _vm_mode_warning)

    requested_style = "auto" if style == "naked" else style
    _pair_score_group = get_pair_score_group(pair)
    resolved_style, style_profile = _rt().naked_scan_style_profile(
        requested_style,
        score_group=_pair_score_group,
        asset_type=pair.get("type", ""),
    )
    _bt_structure_gate_enabled = bool(CONFIG.get("ENGINE_B_BT_STRUCTURE_GATE_ENABLED", True))
    if not _bt_structure_gate_enabled:
        style_profile = dict(style_profile)
        style_profile["disable_structure_gate"] = True
        log.warning(
            "[ENGINE B BT] %s structure gate DISABLED for backtest experiment "
            "(ENGINE_B_BT_STRUCTURE_GATE_ENABLED=false)",
            pair.get("display"),
        )
    _pair_type = pair.get("type", "stock")
    _zone_tf = style_profile.get("zone_tf", "H4")
    _entry_tf = style_profile.get("entry_tf", "H1")
    _atr_tf = style_profile.get("atr_tf", "H4")
    _bt_enable_profile_context = bool(CONFIG.get("ENGINE_B_PROFILE_SCORING_ENABLED", False))
    _b_funnel = {
        "bars_evaluated": 0,
        "fail_verdict":   0,
        "fail_structure": 0,
        "fail_location":  0,
        "fail_entry":     0,
        "fail_rr":        0,
        "fail_score":     0,
        "fail_passed":    0,
        "fail_room":      0,
        "fail_macro":     0,
        "structure_original_miss": 0,
        "passed_gate":    0,
    }
    log.info(
        f"[ENGINE B BT] {pair['display']} running: "
        f"D1={len(candles_d1)} H4={len(candles_h4)} H1={len(candles_h1)} bars, style={resolved_style}"
    )
    d1_times = [c.get("time", c.get("datetime", "")) for c in candles_d1]
    h4_times = [c.get("time", c.get("datetime", "")) for c in candles_h4]
    h1_times = pd.to_datetime(
        [c.get("time", c.get("datetime", "")) for c in candles_h1],
        utc=True,
        errors="coerce",
    )

    # NEW: Loop over the specific Entry Timeframe (H1 vs H4) configured for this style
    # This matches live discovery where signals are detected and filled on the entry candle.
    entry_raw = candles_h1 if _entry_tf == "H1" else candles_h4
    entry_times = [c.get("time", c.get("datetime", "")) for c in entry_raw]

    # PRECOMPUTE ATR SERIES: Compute the full ATR array once per timeframe to avoid O(N^2) complexity.
    # We compute for all three timeframes to ensure we can resolve the _atr_tf at any scan index.
    def _full_atr(cnds):
        if not cnds: return []
        return calc_atr(
            [c["high"] for c in cnds],
            [c["low"] for c in cnds],
            [c["close"] for c in cnds],
            14
        )
    atr_map = {
        "D1": _full_atr(candles_d1),
        "H4": _full_atr(candles_h4),
        "H1": _full_atr(candles_h1)
    }

    bt_vectorized = bool(CONFIG.get("BT_VECTORIZED", False))
    d1_indicator_cache: dict[tuple[int, int, str], dict] = {}
    zone_indicator_cache: dict[tuple[int, int, str], dict] = {}
    _indicator_cache = {}

    def _cached_calc_indicators(
        candles,
        asset_type,
        cache_key,
        *,
        full_candles=None,
        end_idx=None,
        window=None,
    ):
        if (
            bt_vectorized
            and full_candles is not None
            and end_idx is not None
            and window is not None
            and window >= 50
        ):
            cache = d1_indicator_cache if cache_key == "d1" else zone_indicator_cache
            return _bt_indicators_from_cache(
                full_candles,
                end_idx=end_idx,
                window=window,
                asset_type=asset_type,
                cache=cache,
            )
        key = (cache_key, len(candles))
        if key in _indicator_cache:
            return _indicator_cache[key]
        result = calc_indicators_with_normalized(candles, asset_type)
        _indicator_cache[key] = result
        return result

    COOLDOWN = 8 if _entry_tf == "H1" else 2  # entries to skip after a trade (H1 vs H4 bars)
    trades = []
    same_bar_both_hit = 0
    i = 50
    while i < len(entry_raw) - 5:
        entry_time = entry_times[i]
        if not entry_time:
            i += 1
            continue
        # Forex: optional Asian session skip (live scan uses same gate via
        # engine_b_forex_asian_session_blocks_bar).
        if engine_b_forex_asian_session_blocks_bar(
            [entry_raw[i]] if i < len(entry_raw) else [],
            pair.get("type", ""),
        ):
            i += 1
            continue

        _vf = backtest_bar_validation_state(
            i,
            min_bars=_min_entry_bt,
            total_bars=len(entry_raw),
            temporal_mode=_temporal_vm,
            purge_gap=purge_gap,
            folds=folds,
        )
        if _vf["skip"]:
            i += 1
            continue

        _h4_cut_idx = bisect.bisect_left(h4_times, entry_time)
        _d1_cut_idx = bisect.bisect_left(d1_times, entry_time)
        h4_ctx = candles_h4[:_h4_cut_idx]
        d1_ctx = candles_d1[:_d1_cut_idx]

        # entry_ctx is exactly where we are in the entry loop
        entry_ctx = entry_raw[:i + 1]

        if len(d1_ctx) < 20 or len(h4_ctx) < 20 or len(entry_ctx) < 20:
            i += 1
            continue
        current_price = float(entry_raw[i]["close"])
        
        # O(1) ATR LOOKUP: Select precomputed ATR value based on the current bar and _atr_tf
        _atr_full = atr_map.get(_atr_tf, atr_map["H4"])
        if _atr_tf == "D1":
            _idx = _d1_cut_idx
        elif _atr_tf == "H4":
            _idx = _h4_cut_idx

        else: # H1 (entry_tf usually)
            _idx = i + 1 # Align to the context end bar

        # Pull ATR and the slice needed for the volatility gate
        atr = _atr_full[_idx - 1] if _idx > 0 and _idx <= len(_atr_full) else None
        if atr is None:
            atr = (current_price * 0.01)
            atr_list_50 = []
        else:
            # Reconstruct the last 50 bars from the precomputed series for the volatility gate
            atr_list_50 = _atr_full[max(0, _idx - 50) : _idx]

        atr, _eb_bt_atr_feed = _engine_b_level_atr_for_bt(
            atr, pair, resolved_style, as_of=entry_time
        )
        if atr is None or float(atr) <= 0:
            i += 1
            continue

        # Volatility gate: skip trades when ATR is below 60% of its 50-bar average
        # Matches live logic but uses the precomputed slice.
        if len(atr_list_50) >= 50:
            _valid_atrs = [a for a in atr_list_50 if a]
            _atr_avg_50 = sum(_valid_atrs) / len(_valid_atrs) if _valid_atrs else 0
            if _atr_avg_50 > 0 and atr < _atr_avg_50 * 0.6:
                i += 1
                continue

        # Zone context always uses the configured zone_tf (usually H4)

        # Zone context always uses the configured zone_tf (usually H4)
        zone_ctx = h4_ctx if _zone_tf == "H4" else d1_ctx
        _d1_end_idx = _d1_cut_idx - 1
        if _zone_tf == "H4":
            _zone_full_candles = candles_h4
            _zone_end_idx = _h4_cut_idx - 1

        else:
            _zone_full_candles = candles_d1
            _zone_end_idx = _d1_end_idx
        regime_label = _rt().engine_b_regime_label(zone_ctx, pair.get("type", "stock"))
        _bt_b_d1_snap = {}
        _bt_b_zone_snap = {}
        try:
            _bt_b_d1_snap = (
                _cached_calc_indicators(
                    d1_ctx,
                    pair.get("type", "stock"),
                    "d1",
                    full_candles=candles_d1,
                    end_idx=_d1_end_idx,
                    window=len(d1_ctx),
                ) or {}
            ).get("snap") or {}
            _bt_b_zone_snap = (
                _cached_calc_indicators(
                    zone_ctx,
                    pair.get("type", "stock"),
                    "zone",
                    full_candles=_zone_full_candles,
                    end_idx=_zone_end_idx,
                    window=len(zone_ctx),
                ) or {}
            ).get("snap") or {}
        except Exception:
            pass
        # Precompute direction-independent structure data once per bar
        _bt_pre = naked_engine.precompute_structure_data(
            d1_ctx,
            zone_ctx,
            entry_ctx,
            current_price,
            atr,
            regime=regime_label,
            fallback_rr=style_profile.get("fallback_rr", 2.0),
            asset_type=pair.get("type", ""),
            enable_profile_context=_bt_enable_profile_context,
            d1_snap=_bt_b_d1_snap,
            h4_snap=_bt_b_zone_snap,
            style=resolved_style,
            pair=pair,
        )
        candidates = []
        for direction in ["LONG", "SHORT"]:
            res = naked_engine.analyze_structure_direction(
                _bt_pre,
                current_price,
                direction,
            )
            _b_funnel["bars_evaluated"] += 1
            if res.get("structural_verdict") != "CLEAR":
                _b_funnel["fail_verdict"] += 1
                continue
            conf_data = naked_engine.calculate_confidence(
                res,
                current_price,
                direction,
                entry_candles=entry_ctx,
                style_profile=style_profile,
            )
            _gate_ok, _min_score_scaled = engine_b_confidence_passes(
                conf_data,
                style_profile,
                regime_label,
                pair.get("type", ""),
            )
            if conf_data.get("structure_gate_original_ok") is False:
                _b_funnel["structure_original_miss"] += 1
            if not _gate_ok:
                _cd = conf_data
                if not _cd.get("structure_ok"):
                    _b_funnel["fail_structure"] += 1
                elif not _cd.get("location_ok"):
                    _b_funnel["fail_location"] += 1
                elif not _cd.get("entry_ok"):
                    _b_funnel["fail_entry"] += 1
                elif not _cd.get("rr_ok"):
                    _b_funnel["fail_rr"] += 1
                elif not _cd.get("passed"):
                    _b_funnel["fail_passed"] += 1
                else:
                    _b_funnel["fail_score"] += 1
                if not _cd.get("room_ok"):
                    _b_funnel["fail_room"] += 1
                if not _cd.get("macro_ok", True):
                    _b_funnel["fail_macro"] += 1
                continue
            _b_funnel["passed_gate"] += 1

            entry = current_price
            # Use the same execution levels that gated this signal — eliminates the
            # historical live/BT divergence where BT recomputed SL/TP from calc_levels
            # while live used resolve_engine_b_execution_levels (tighter-of-both SL +
            # structural-or-ATR TP). Fallback to calc_levels only if conf_data did not
            # produce execution levels (e.g. zero ATR / invalid entry).
            sl = conf_data.get("execution_sl")
            tp = conf_data.get("execution_tp")
            level_source = conf_data.get("rr_source") or "engine_b_execution"
            if sl is None or tp is None:
                _bt_regime = res.get("regime_state")
                _eb_lvl_class = resolve_engine_b_asset_class(
                    pair.get("type", ""), _pair_score_group
                )
                _lvl = calc_levels(
                    entry,
                    atr,
                    direction,
                    _eb_lvl_class,
                    regime_state=_bt_regime,
                    style=resolved_style,
                )
                sl = sl if sl is not None else _lvl.get("sl")
                tp = tp if tp is not None else _lvl.get("tp1")
                level_source = "calc_levels_fallback"
            if sl is None or tp is None:
                continue

            _rr_sl_dist = abs(entry - sl)
            _rr_tp_dist = abs(tp - entry)
            rr = (_rr_tp_dist / _rr_sl_dist) if _rr_sl_dist > 0 else 0.0
            if rr <= 0 or rr < float(style_profile.get("min_rr", 1.0)):
                continue

            candidates.append(
                {
                    "direction": direction,
                    "score": conf_data["score"],
                    "pct": conf_data["pct"],
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "rr": rr,
                    "res": res,
                    "conf": conf_data,
                    "regime_label": regime_label,
                    "level_mode": level_source,
                    "selected_tp_source": level_source,
                    "selected_sl_source": level_source,
                }
            )

        if not candidates:
            i += 1
            continue

        best = max(candidates, key=lambda x: x["score"])
        direction = best["direction"]
        selected_tp_source = best.get("selected_tp_source", "unknown")
        selected_sl_source = best.get("selected_sl_source", "unknown")

        # Execute at the open of the very next candle in the entry timeframe.
        # This matches live discovery where fill is immediate, not delayed to next H4.
        entry_bar = entry_raw[i + 1]
        raw_entry = float(entry_bar.get("open", entry_bar["close"]))
        _ptype = pair.get("type", "stock")
        _slip_mult = 3.0 if _canonical_vm == "live_parity" else 1.0
        slip = raw_entry * _get_slippage_for_bar(
            _bar_with_slippage_context(entry_bar, atr=atr), _ptype
        ) * _slip_mult
        entry = raw_entry + slip if direction == "LONG" else raw_entry - slip
        
        # Synchronize future_window to the correct H4 starting position
        _h4_fill_index = bisect.bisect_left(h4_times, entry_bar["time"])

        # Use the candidate's pre-resolved execution SL/TP — these came from
        # resolve_engine_b_execution_levels (live parity). Cap TP at fallback_rr
        # so structural targets don't run beyond the style's risk contract.
        sl = best["sl"]
        tp = best["tp"]
        _sl_dist = abs(entry - sl)
        _tp_dist = abs(tp - entry)
        target_rr = (_tp_dist / _sl_dist) if _sl_dist > 0 else 0.0
        _fallback_rr = float(style_profile.get("fallback_rr", 0.0) or 0.0)
        if _fallback_rr > 0 and target_rr > _fallback_rr and _sl_dist > 0:
            tp = (
                entry + (_sl_dist * _fallback_rr)
                if direction == "LONG"
                else entry - (_sl_dist * _fallback_rr)
            )
            target_rr = _fallback_rr
            selected_tp_source = "capped_to_fallback_rr"

        if sl is None or tp is None:
            i += 1
            continue
        if target_rr <= 0:
            _sl_dist = abs(entry - sl)
            _tp_dist = abs(tp - entry)
            target_rr = (_tp_dist / _sl_dist) if _sl_dist > 0 else 0.0
        if target_rr <= 0:
            i += 1
            continue
        if target_rr < float(style_profile.get("min_rr", 1.0)):
            i += 1
            continue

        # Post-fill structural targets stay bounded by the style fallback RR contract.
        actual_rr = target_rr

        # MAX_SL_PCT rejection — Ensuring backtest results reflect the same risk thresholds as live trading.
        _max_sl_pct_b = CONFIG.get("MAX_SL_PCT", {}).get(_ptype, 0.05)
        _sl_dist_pct_b = abs(float(entry) - float(sl)) / float(entry)
        if _sl_dist_pct_b > _max_sl_pct_b:
            log.debug(
                f"[SL-CAP] {pair['display']} {direction} SL {_sl_dist_pct_b:.1%} "
                f"exceeds cap {_max_sl_pct_b:.1%} — REJECTED"
            )
            i += 1
            continue

        outcome = "TIMEOUT"
        r_multiple = 0.0
        exit_bar_offset = 0
        _bt_exit_config = CONFIG.get("ENGINE_B_BT_EXIT", {}) or CONFIG.get("ENGINE_C_BT_EXIT", {})
        _asset_config = _bt_exit_config.get(_ptype, _bt_exit_config.get("stock", {}))
        _style_config = _asset_config.get(resolved_style, _asset_config.get("intraday", {}))
        max_hold_bars = _style_config.get("max_hold_bars", 24)
        _be_arm_rr = _style_config.get("be_arm_rr", 1.5)
        _be_min_rr = _style_config.get("be_min_target_rr", 2.0)

        risk = abs(entry - sl)
        _active_sl = sl
        _be_triggered = False

        # PHASE 1E: Track additive diagnostics
        max_favorable_excursion_r = 0.0
        max_adverse_excursion_r = 0.0
        bars_to_mfe = None
        bars_to_mae = None
        highest_r_seen = 0.0
        lowest_r_seen = 0.0
        price_never_reached_tp = True
        price_never_reached_sl = True
        be_armed = False
        be_trigger_r = None

        # PHASE 3A/B: Use entry_tf for monitoring and convert max_hold_bars from H4 to monitoring TF
        # Config max_hold_bars is defined in H4 bars; convert to monitoring TF
        _monitor_tf = _entry_tf  # Use entry_tf from style_profile (H1 for intraday, H4 for swing)
        _monitor_candles = candles_h1 if _monitor_tf == "H1" else candles_h4
        _monitor_times = h1_times if _monitor_tf == "H1" else h4_times
        _monitor_fill_index = 0
        if _monitor_tf == "H4":
            # entry_bar is candles_h4[i + 1] in this loop, so monitoring starts there directly.
            _monitor_fill_index = i + 1
        elif h1_times is not None and len(h1_times) > 0:
            _entry_bar_ts = pd.Timestamp(entry_bar["time"])
            if pd.notna(_entry_bar_ts):
                if _entry_bar_ts.tzinfo is None:
                    _entry_bar_ts = _entry_bar_ts.tz_localize("UTC")
                _monitor_fill_index = int(bisect.bisect_left(h1_times, _entry_bar_ts))
        
        # Convert H4-based max_hold to monitoring TF (H4->H1 = 4x, H4->H4 = 1x)
        _tf_multiplier = 4 if _monitor_tf == "H1" else 1
        _max_hold_monitor_bars = max_hold_bars * _tf_multiplier
        
        # Forward monitoring on the correct timeframe based on entry_tf
        future_window = _monitor_candles[_monitor_fill_index : min(_monitor_fill_index + _max_hold_monitor_bars + 1, len(_monitor_candles))]
        for fi, future in enumerate(future_window):
            exit_bar_offset = fi
            _bar_outcome, _both_hit = _resolve_barrier_exit(
                future,
                direction=direction,
                sl=_active_sl,
                tp1=tp,
                sl_outcome="BE" if _be_triggered else "SL",
            )
            if _both_hit:
                same_bar_both_hit += 1
            if _bar_outcome == "TP1":
                outcome = "TP1"
                r_multiple = round(target_rr, 2)
                break
            if _bar_outcome in ("SL", "BE"):
                outcome = _bar_outcome
                r_multiple = 0.0 if outcome == "BE" else -1.0
                break
            
            # Use H1 granularity for barrier checks if we need more precision than H4 (optional enhancement)
            # For now, H4 мониторинг on H1 entry is a reasonable compromise for performance.
            f_high = float(future["high"])
            f_low = float(future["low"])
            f_close = float(future["close"])
            
            # PHASE 1E: Track MFE/MAE and TP/SL reach status
            if risk > 0:
                if direction == "LONG":
                    bar_r_high = (f_high - entry) / risk
                    bar_r_low = (f_low - entry) / risk
                    bar_r_close = (f_close - entry) / risk
                else:
                    bar_r_high = (entry - f_low) / risk
                    bar_r_low = (entry - f_high) / risk
                    bar_r_close = (entry - f_close) / risk
                
                if bar_r_high > max_favorable_excursion_r:
                    max_favorable_excursion_r = bar_r_high
                    if bars_to_mfe is None:
                        bars_to_mfe = fi
                if bar_r_low < max_adverse_excursion_r:
                    max_adverse_excursion_r = bar_r_low
                    if bars_to_mae is None:
                        bars_to_mae = fi
                
                highest_r_seen = max(highest_r_seen, bar_r_high)
                lowest_r_seen = min(lowest_r_seen, bar_r_low)
                
                # Track if TP/SL were ever reached
                if direction == "LONG":
                    if f_high >= tp:
                        price_never_reached_tp = False
                    if f_low <= sl:
                        price_never_reached_sl = False
                else:
                    if f_low <= tp:
                        price_never_reached_tp = False
                    if f_high >= sl:
                        price_never_reached_sl = False

            if direction == "LONG":
                # BE trigger — only activate if RR >= be_min_rr
                if not _be_triggered and risk > 0 and target_rr >= _be_min_rr:
                    if f_high >= entry + (risk * _be_arm_rr):
                        _active_sl = entry
                        _be_triggered = True
                        be_armed = True
                        be_trigger_r = _be_arm_rr
            else:
                # BE trigger — only if RR >= be_min_rr
                if not _be_triggered and risk > 0 and target_rr >= _be_min_rr:
                    if f_low <= entry - (risk * _be_arm_rr):
                        _active_sl = entry
                        _be_triggered = True
                        be_armed = True
                        be_trigger_r = _be_arm_rr

        if outcome == "TIMEOUT" and future_window:
            last_close = float(future_window[-1]["close"])
            if risk > 0:
                open_r = ((last_close - entry) / risk) if direction == "LONG" else ((entry - last_close) / risk)
                r_multiple = round(max(-1.0, min(target_rr, open_r)), 2)

        # Deduct round-trip transaction costs from every closed result, including forced TIMEOUT exits.
        _fee_r = _bt_transaction_cost_r(entry, sl, pair.get("type", "stock"))
        if _fee_r > 0:
            r_multiple = round(r_multiple - _fee_r, 4)

        bar_date = entry_bar.get("time", "")[:10] if entry_bar.get("time") else ""
        
        _vol_str = best["res"].get("bos_data", {}).get("volume_strength", 0.0)
        if not _vol_str:
            _nz = best["res"].get("nearest_support_zone") if direction == "LONG" else best["res"].get("nearest_resistance_zone")
            if _nz:
                _vol_str = _nz.get("volume_strength", 0.0)
        _fvg_bonus = 1.0 if best["res"].get("fvg_overlap") else 0.0

        trades.append(
            {
                "date": bar_date,
                "pair": pair["display"],
                "direction": direction,
                "score": best["score"],
                "entry": round(float(entry), 6),
                "sl": round(float(sl), 6),
                "tp1": round(float(tp), 6),
                "tp2": round(float(tp), 6),
                "outcome": outcome,
                "resultR": round(r_multiple, 2),
                "regime": best.get("regime_label", "RANGING"),
                **build_strategy_lab_telemetry(
                    engine="ENGINE_B",
                    strategy_family="ENGINE_B_ONLY",
                    regime=best.get("regime_label", "RANGING"),
                    setup_type="naked",
                    timeframe=_entry_tf,
                    failure_reason=outcome if outcome not in ["TP1", "TP2"] else None,
                    entry_reason=None,
                    exit_reason=outcome,
                    source_module="backtest_runner",
                    source_function="backtest_pair"
                ),
                "oos": _vf["oos_label"],
                "wf_fold": _vf["wf_fold"],
                "validation_mode": _canonical_vm,
                "volAdj": 1.0,
                "r_multiple": r_multiple,
                "liquidity_sweep": best["res"].get("liquidity_sweep", False),
                "fvg_overlap": best["res"].get("fvg_overlap", False),
                "trigger_pattern": best["conf"].get("trigger_pattern", "NONE"),
                "zone_touched": best["res"].get("zone_touched", False),
                "rr_target": round(target_rr, 2),
                "actual_rr": round(actual_rr, 2),
                "selected_tp": round(float(tp), 6),
                "selected_sl": round(float(sl), 6),
                "bt_exit_policy": CONFIG.get("ENGINE_B_BT_EXIT_POLICY", "fixed_target_be"),
                "bos_volume_confirmed": best["res"].get("bos_volume_confirmed", True),
                "choch_confirmed": best["res"].get("choch_confirmed", False),
                "ob_at_zone": best["res"].get("ob_at_zone", False),
                "bos_mtf_confirmed": best["res"].get("bos_mtf_confirmed", False),
                "breaker_active": best["conf"].get("breaker_active", False),
                "ob_strength": max((ob.get("strength", 0) for ob in best["res"].get("order_blocks", [])), default=0),
                # Enhanced item-level telemetry
                "fvg_bonus": _fvg_bonus,
                "volume_strength": round(_vol_str, 2),
                "checklist_struct": best["conf"].get("structure_ok", False),
                "checklist_loc": best["conf"].get("location_ok", False),
                "checklist_trigger": best["conf"].get("entry_ok", False),
                "checklist_rr": best["conf"].get("rr_ok", False),
                "checklist_room": best["conf"].get("room_ok", False),
                "bos_confirmed": best["res"].get("bos_confirmed", False),
                # PHASE 1C: Level source tracking
                "selected_tp_source": selected_tp_source,
                "selected_sl_source": selected_sl_source,
            }
        )
        # Advance past the resolved exit bar plus the configured cooldown gap.
        i = i + 2 + exit_bar_offset + COOLDOWN

    result = _format_backtest_results(
        trades,
        pair,
        engine_type="NAKED",
        same_bar_both_hit=same_bar_both_hit,
        validation_mode=_canonical_vm,
    )
    _attach_research_validation_payload(
        result,
        trades,
        canonical_vm=_canonical_vm,
        temporal_vm=_temporal_vm,
        purge_gap=purge_gap,
        folds=folds,
        mode_warning=_vm_mode_warning,
    )
    _tp_count = sum(1 for t in trades if t.get("outcome") == "TP1")
    _sl_count = sum(1 for t in trades if t.get("outcome") == "SL")
    _be_count = sum(1 for t in trades if t.get("outcome") == "BE")
    _to_count = sum(1 for t in trades if t.get("outcome") == "TIMEOUT")
    log.warning(
        f"[ENGINE B BT] {pair['display']} done: {result.get('totalTrades', 0)} trades "
        f"(TP1={_tp_count} SL={_sl_count} BE={_be_count} TIMEOUT={_to_count}), "
        f"WR {result.get('winRate', 0):.1f}%, PF {result.get('profitFactor', 0):.2f}, "
        f"Expect {result.get('expectancy', 0):.2f}R, SQN {result.get('sqn', 0):.2f}, "
        f"style={resolved_style}"
    )
    _b_total = _b_funnel["bars_evaluated"] or 1
    log.warning(
        f"[ENGINE B BT FUNNEL] {pair['display']} "
        f"bars={_b_funnel['bars_evaluated']} "
        f"verdict_fail={_b_funnel['fail_verdict']} ({_b_funnel['fail_verdict']/_b_total*100:.1f}%) "
        f"| struct_fail={_b_funnel['fail_structure']} ({_b_funnel['fail_structure']/_b_total*100:.1f}%) "
        f"| loc_fail={_b_funnel['fail_location']} ({_b_funnel['fail_location']/_b_total*100:.1f}%) "
        f"| entry_fail={_b_funnel['fail_entry']} ({_b_funnel['fail_entry']/_b_total*100:.1f}%) "
        f"| rr_fail={_b_funnel['fail_rr']} ({_b_funnel['fail_rr']/_b_total*100:.1f}%) "
        f"| score_fail={_b_funnel['fail_score']} ({_b_funnel['fail_score']/_b_total*100:.1f}%) "
        f"| passed_fail={_b_funnel['fail_passed']} ({_b_funnel['fail_passed']/_b_total*100:.1f}%) "
        f"| room_miss={_b_funnel['fail_room']} ({_b_funnel['fail_room']/_b_total*100:.1f}%) "
        f"| struct_original_miss={_b_funnel['structure_original_miss']} "
        f"({_b_funnel['structure_original_miss']/_b_total*100:.1f}%) "
        f"| passed_gate={_b_funnel['passed_gate']} ({_b_funnel['passed_gate']/_b_total*100:.1f}%)"
    )
    if "error" not in result:
        result["engineBFunnel"] = _b_funnel
    if "error" not in result:
        result["btStyle"] = resolved_style
        result["btStyleRequested"] = requested_style
        result["btStructureGateEnabled"] = _bt_structure_gate_enabled
        try:
            import sqlite3 as _sq

            _wf = result.get("wfSplit", {}) or {}
            _is_sqn = _wf.get("is_sqn")
            _oos_sqn = _wf.get("oos_sqn")
            with _sq.connect(_rt().AUDIT_DB, timeout=15.0) as _con:
                _con.execute(
                    "INSERT INTO backtest_results "
                    "(run_date,pair,asset_type,engine,trades,win_rate,profit_factor,"
                    "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,bt_min,atr_source,notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        pair["display"],
                        pair.get("type", ""),
                        "naked_engine",
                        result.get("totalTrades", 0),
                        result.get("winRate"),
                        result.get("profitFactor"),
                        result.get("expectancy"),
                        result.get("sqn"),
                        result.get("sharpe"),
                        result.get("sortino"),
                        round(_is_sqn, 4) if _is_sqn is not None else None,
                        round(_oos_sqn, 4) if _oos_sqn is not None else None,
                        result.get("maxDrawdownPct"),
                        style_profile.get("min_score"),
                        f"{_atr_tf}_ATR",
                        (
                            f"style={resolved_style};requested={requested_style}"
                            f";bos_vol_confirmed={sum(1 for t in trades if t.get('bos_volume_confirmed', True))}"
                            f";choch_confirmed={sum(1 for t in trades if t.get('choch_confirmed', False))}"
                        ),
                    ),
                )
                _con.commit()
        except Exception as _e:
            log.warning(f"[ENGINE B BT] backtest_results write failed: {_e}")
        try:
            record_backtest_summary(
                engine="engine_b",
                pass_rate=None,
                expectancy_r=result.get("expectancy"),
                score=min(1.0, max(0.0, (result.get("winRate", 0) or 0) / 100.0)),
                max_score=1.0,
                feature_map={
                    "win_rate": (result.get("winRate", 0) or 0) / 100.0,
                    "profit_factor": result.get("profitFactor"),
                    "sqn": result.get("sqn"),
                    "min_score": style_profile.get("min_score"),
                },
                db_path=_rt().AUDIT_DB,
                meta={
                    "pair": pair.get("display"),
                    "style": resolved_style,
                    "runtime_only_metric": False,
                },
            )
        except Exception as _ssi_err:
            log.debug(f"[SSI] Engine B backtest sample skipped: {_ssi_err}")
        result["calibrationReport"] = calibration_report(
            records=_records_for_calibration(
                trades,
                engine="engine_b",
                asset_class=pair.get("type", ""),
                style=resolved_style,
                default_max_score=5.0,
            ),
            engine="engine_b",
            asset_class=pair.get("type", ""),
            style=resolved_style,
            default_max_score=5.0,
        )
        result["metaReport"] = meta_report(
            {
                "engine": "engine_b",
                "type": pair.get("type"),
                "style": resolved_style,
                "regime": trades[-1].get("regime") if trades else None,
            },
            records=_records_for_meta(
                trades,
                engine="engine_b",
                asset_class=pair.get("type", ""),
                style=resolved_style,
            ),
        )
    return result





def backtest_pair_consensus(
    pair: dict,
    style: str = "intraday",
    validation_mode: str = "standard",
    purge_gap: int = 200,
    folds: int = 3,
) -> dict:
    """Engine C backtest — runs Engine A + Engine B point-in-time at each H4 bar,
    feeds both into compute_consensus(), and tracks outcomes using the same
    exit logic as backtest_pair_naked.

    Style is always intraday (H4 walk). ai_vision is never called.
    """
    from market_structure import NakedEngine, engine_b_confidence_passes
    from engine_c import compute_consensus
    from indicators import calc_atr

    _ptype = pair.get("type", "stock")
    log.info(f"[ENGINE C BT] {pair['display']} fetching data...")

    if pair.get("source") == "binance":
        sym = pair["symbol"]
        candles_d1 = _bt_cached_fetch(
            pair,
            "D1",
            1000,
            lambda lim: _rt().fetch_binance(sym, "1d", lim),
            provider="binance_futures",
            min_bars=230,
        )
        candles_h4 = _bt_cached_fetch(
            pair,
            "H4",
            5000,
            lambda lim: _rt().fetch_binance_paginated(sym, "4h", lim),
            provider="binance_futures",
            min_bars=500,
        )
        candles_h1 = _bt_cached_fetch(
            pair,
            "H1",
            20000,
            lambda lim: _rt().fetch_binance_paginated(sym, "1h", lim),
            provider="binance_futures",
            min_bars=500,
        )
    elif pair.get("source") == "mt5":
        candles_d1 = _bt_cached_fetch(
            pair,
            "D1",
            600,
            lambda lim: _rt().fetch_candles(pair, "D1", lim),
            provider="mt5",
            min_bars=230,
        )
        candles_h4 = _bt_cached_fetch(
            pair,
            "H4",
            5000,
            lambda lim: _rt().fetch_candles(pair, "H4", lim),
            provider="mt5",
            min_bars=500,
        )
        candles_h1 = _bt_cached_fetch(
            pair,
            "H1",
            5000,
            lambda lim: _rt().fetch_candles(pair, "H1", lim),
            provider="mt5",
            min_bars=500,
        )
    else:
        candles_d1 = _bt_cached_fetch(
            pair,
            "D1",
            600,
            lambda lim: _rt().extract_candles(_rt().fetch_eodhd(pair, "D1", lim)),
            provider="eodhd",
            min_bars=230,
        ) or _bt_cached_fetch(
            pair,
            "D1",
            600,
            lambda lim: _rt().fetch_candles(pair, "D1", lim),
            provider=str(pair.get("source") or "fallback"),
            min_bars=230,
        )
        candles_h4, candles_h1 = _bt_cached_eodhd_intraday(pair, days=730, h4_limit=5000, h1_limit=20000)
        if not candles_h4 or not candles_h1:
            candles_h4 = candles_h4 or _bt_cached_fetch(
                pair,
                "H4",
                5000,
                lambda lim: _rt().fetch_candles(pair, "H4", lim),
                provider=str(pair.get("source") or "fallback"),
                min_bars=500,
            )
            candles_h1 = candles_h1 or _bt_cached_fetch(
                pair,
                "H1",
                5000,
                lambda lim: _rt().fetch_candles(pair, "H1", lim),
                provider=str(pair.get("source") or "fallback"),
                min_bars=500,
            )

    if not candles_d1 or not candles_h4 or not candles_h1:
        log.warning(f"[ENGINE C BT] {pair['display']} insufficient candle data")
        _cv, _mw = normalize_validation_mode(validation_mode)
        _tv = temporal_validation_mode(_canonical_vm if False else _cv)
        early = {"success": False, "error": "Insufficient candle data.", "trades": [], "totalTrades": 0}
        _attach_research_validation_payload(early, [], canonical_vm=_cv, temporal_vm=_tv,
                                            purge_gap=purge_gap, folds=folds, mode_warning=_mw)
        return early

    candles_d1 = candles_d1[:-1] if len(candles_d1) > 1 else candles_d1
    candles_h4 = candles_h4[:-1] if len(candles_h4) > 1 else candles_h4
    candles_h1 = candles_h1[:-1] if len(candles_h1) > 1 else candles_h1

    _canonical_vm, _vm_mode_warning = normalize_validation_mode(validation_mode)
    _temporal_vm = temporal_validation_mode(_canonical_vm)
    if _vm_mode_warning:
        log.warning("[ENGINE C BT] %s: %s", pair.get("display"), _vm_mode_warning)

    requested_style = _normalize_style(style)
    _pair_score_group = get_pair_score_group(pair)
    _level_atr_class = get_pair_level_atr_class(pair)
    # Engine C BT uses the same style as Engine A (resolved via auto), and reads TFs
    # from the asset+style matrix — no more forex-specific structure TF flag.
    _ec_bt_style = requested_style if requested_style != "auto" else (
        "intraday" if _ptype in ("crypto", "forex") else "swing"
    )
    resolved_style, style_profile = _rt().naked_scan_style_profile(
        _ec_bt_style,
        score_group=_pair_score_group,
        asset_type=_ptype,
    )
    _zone_tf = style_profile.get("zone_tf", "H4")
    _entry_tf = style_profile.get("entry_tf", "H1")
    _atr_tf = style_profile.get("atr_tf", "H4")
    _bt_enable_profile_context = bool(CONFIG.get("ENGINE_B_PROFILE_SCORING_ENABLED", False))

    _h4_need = max(50, CONFIG.get("H4_CANDLES", 1001))
    _h1_need = max(50, CONFIG.get("H1_CANDLES", 1001))
    _min_conviction = float(
        (CONFIG.get("AUTO_TRADE_MIN_CONVICTION") or {}).get("default", 0.50)
    )
    _enforce_min_conviction = bool(CONFIG.get("ENGINE_C_BT_ENFORCE_MIN_CONVICTION", False))

    h4_times = pd.to_datetime([c["time"] for c in candles_h4], utc=True, errors="coerce")
    d1_times = pd.to_datetime([c["time"] for c in candles_d1], utc=True, errors="coerce")
    h1_times = pd.to_datetime([c["time"] for c in candles_h1], utc=True, errors="coerce")

    def _full_atr(candles):
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        return calc_atr(highs, lows, closes, 14)

    atr_map = {"D1": _full_atr(candles_d1), "H4": _full_atr(candles_h4), "H1": _full_atr(candles_h1)}

    _bt_crypto_funding_rows = None
    _bt_crypto_oi_rows = None
    if _ptype == "crypto":
        try:
            from data_feeds import prepare_crypto_backtest_derivative_series
            _bt_crypto_funding_rows, _bt_crypto_oi_rows = prepare_crypto_backtest_derivative_series(
                pair, candles_d1, candles_h4, candles_h1
            )
        except Exception as _de:
            log.warning("[ENGINE C BT] %s: derivative series failed: %s", pair.get("display"), _de)

    naked_engine = NakedEngine()

    _c_funnel = {
        "bars_evaluated": 0, "a_no_signal": 0, "b_no_signal": 0,
        "direction_conflict": 0, "low_conviction": 0, "passed_gate": 0,
        "invalid_bt_levels": 0,
    }

    COOLDOWN = 2
    # PHASE 3: Use config for Engine C BT exit controls instead of hardcoded values
    _bt_exit_config = CONFIG.get("ENGINE_C_BT_EXIT", {})
    _asset_config = _bt_exit_config.get(_ptype, _bt_exit_config.get("stock", {}))
    _style_config = _asset_config.get(resolved_style, _asset_config.get("intraday", {}))
    MAX_HOLD = _style_config.get("max_hold_bars", 24)
    _be_min_rr = _style_config.get("be_min_target_rr", 2.0)
    _be_arm_rr = _style_config.get("be_arm_rr", 1.5)

    trades = []
    same_bar_both_hit = 0
    last_exit_bar = 0
    i = max(50, _h4_need)

    while i < len(candles_h4) - 5:
        if i - last_exit_bar < COOLDOWN:
            i += 1
            continue

        entry_time = h4_times[i]
        if pd.isna(entry_time):
            i += 1
            continue

        if engine_b_forex_asian_session_blocks_bar(
            [candles_h4[i]] if i < len(candles_h4) else [],
            _ptype,
        ):
            i += 1
            continue

        _vf = backtest_bar_validation_state(
            i, min_bars=max(50, _h4_need), total_bars=len(candles_h4),
            temporal_mode=_temporal_vm, purge_gap=purge_gap, folds=folds,
        )
        if _vf["skip"]:
            i += 1
            continue

        h4_idx = bisect.bisect_left(h4_times, entry_time)
        h1_idx = bisect.bisect_left(h1_times, entry_time)
        d1_idx = bisect.bisect_left(d1_times, entry_time)

        h4_window = candles_h4[max(0, h4_idx - _h4_need): h4_idx]
        h1_window = candles_h1[max(0, h1_idx - _h1_need): h1_idx]
        d1_ctx = candles_d1[max(0, d1_idx - 220): d1_idx]  # Fixed 220 bar lookback for indicators
        zone_ctx = h4_window

        if len(h4_window) < 50 or len(h1_window) < 50 or len(d1_ctx) < 50:
            i += 1
            continue

        current_price = float(candles_h4[i]["close"])

        atr, atr_list_50, _atr_idx = _engine_b_indexed_atr(
            atr_map,
            _atr_tf,
            d1_idx=d1_idx,
            h4_idx=h4_idx,
            h1_idx=h1_idx,
            current_price=current_price,
        )
        atr, _eb_bt_atr_feed_c = _engine_b_level_atr_for_bt(
            atr, pair, resolved_style, as_of=entry_time
        )
        if atr is None or float(atr) <= 0:
            i += 1
            continue
        if len(atr_list_50) >= 50:
            _valid_atrs = [a for a in atr_list_50 if a]
            _atr_avg = sum(_valid_atrs) / len(_valid_atrs) if _valid_atrs else 0
            if _atr_avg > 0 and atr < _atr_avg * 0.6:
                i += 1
                continue

        regime_label = _rt().engine_b_regime_label(zone_ctx, _ptype)

        try:
            h4i = calc_indicators_with_normalized(h4_window, _ptype)
            try:
                _bt_fib = calc_fib(h4_window)
                _bt_h4_close = h4_window[-1]["close"] if h4_window else None
                if _bt_fib and _bt_h4_close:
                    h4i["snap"]["fib_proximity"] = calc_fib_proximity(float(_bt_h4_close), _bt_fib)
            except Exception:
                pass
            h1i = calc_indicators_with_normalized(h1_window, _ptype)
            d1i = calc_indicators_with_normalized(d1_ctx, _ptype)

            vols = [c["vol"] for c in h1_window]
            vsma = calc_sma(vols, 20)
            vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0
            stoch = calc_stochastic(h1_window, 5, 3, 3)
            _pair_ctx = dict(pair)
            btc_bias = _bt_btc_bias(d1_ctx, _pair_ctx)

            _bt_funding_rate = None
            _bt_oi_data = None
            if _ptype == "crypto":
                h4_ts_i = h4_times[i]
                _prev_h4_ts = h4_times[i - 1] if i >= 1 else None
                _bt_funding_rate, _bt_oi_data = _bt_crypto_funding_oi_for_bar(
                    _ptype, _bt_crypto_funding_rows, _bt_crypto_oi_rows, h4_ts_i, _prev_h4_ts
                )
            _bt_oi_ctx = build_oi_context_for_factor_scoring(_bt_oi_data, d1_ctx, h1i.get("snap"))

            # Engine A v2: all asset classes (including forex) use calc_confluence
            res_a = calc_confluence(
                d1i, h4i, h1i, vr, stoch, _pair_ctx, btc_bias,
                d1_candles=d1_ctx, h4_candles=h4_window, h1_candles=h1_window,
                funding_rate=_bt_funding_rate, oi_data=_bt_oi_data, oi_context=_bt_oi_ctx,
                bar_time=candles_h4[i].get("time") if candles_h4 else None,
            )
            _lvl_a = calc_levels(current_price, atr, res_a["direction"], _level_atr_class,
                                 regime_state=res_a.get("regime", {}).get("state"),
                                 style=resolved_style)
            signal_a = {
                "confluenceScore": res_a["score"], "maxScore": res_a.get("maxScoreOverride", 3.0),
                "direction": res_a["direction"], "score": res_a["score"],
                "regime": res_a.get("regime", {"label": regime_label}),
                "sl": _lvl_a.get("sl"), "tp1": _lvl_a.get("tp1"),
                "tp2": _lvl_a.get("tp2"), "rr1": _lvl_a.get("rr1", 0),
                "factor_scores": res_a.get("factor_scores", {}),
            }
            a_direction = res_a["direction"]

        except Exception as _ae:
            log.debug("[ENGINE C BT] %s bar %d Engine A failed: %s", pair.get("display"), i, _ae)
            i += 1
            continue

        best_b = None
        try:
            res_b = naked_engine.analyze_structure(
                d1_ctx,
                zone_ctx,
                h1_window,
                current_price,
                a_direction,
                atr,
                regime_label,
                fallback_rr=style_profile.get("fallback_rr", 2.0),
                asset_type=_ptype,
                enable_profile_context=_bt_enable_profile_context,
                d1_snap=(d1i or {}).get("snap") or {},
                h4_snap=(h4i or {}).get("snap") or {},
                style=resolved_style,
                pair=pair,
            )
            if res_b.get("structural_verdict") == "CLEAR":
                conf_b = naked_engine.calculate_confidence(
                    res_b, current_price, a_direction,
                    entry_candles=h1_window, style_profile=style_profile,
                )
                _b_gate_ok, _b_scaled_min = engine_b_confidence_passes(
                    conf_b,
                    style_profile,
                    regime_label,
                    asset_type=_ptype,
                )
                conf_b = dict(conf_b)
                conf_b["passed"] = _b_gate_ok
                conf_b["min_score_scaled"] = _b_scaled_min
                signal_b = {
                    "structural_verdict": "CLEAR", "direction": a_direction,
                    "score": conf_b["score"], "pct": conf_b["pct"],
                    "passed": conf_b.get("passed", False),
                    "recommended_stop_loss": res_b.get("recommended_stop_loss"),
                    "recommended_take_profit": res_b.get("recommended_take_profit"),
                    "structure_ok": conf_b.get("structure_ok", False),
                    "zone_ok": conf_b.get("zone_ok", False),
                    "trigger_ok": conf_b.get("trigger_ok", False),
                    "entry_ok": conf_b.get("entry_ok", False),
                    "rr_ok": conf_b.get("rr_ok", False),
                    "rr": conf_b.get("rr", 0.0),
                    "bos_mtf": conf_b.get("bos_mtf_confirmed", False),
                    "ob_at_zone": conf_b.get("ob_at_zone", False),
                    "ob_strength": max((ob.get("strength", 0) for ob in res_b.get("order_blocks", [])), default=0),
                }
                best_b = (signal_b, conf_b, res_b)
        except Exception as _be:
            log.debug("[ENGINE C BT] %s bar %d Engine B failed: %s", pair.get("display"), i, _be)

        _c_funnel["bars_evaluated"] += 1
        if best_b is None:
            _c_funnel["b_no_signal"] += 1
            signal_b_use = {"structural_verdict": "NONE", "direction": a_direction,
                            "score": 0, "passed": False}
            conf_b_use = {}
        else:
            signal_b_use = best_b[0]
            conf_b_use = best_b[1]

        try:
            consensus = compute_consensus(
                signal_a=signal_a, signal_b=signal_b_use, confidence_b=conf_b_use,
                ai_vision=None, asset_type=_ptype, regime=regime_label,
                entry_price=current_price, atr=atr,
            )
        except Exception as _ce:
            log.debug("[ENGINE C BT] %s bar %d consensus failed: %s", pair.get("display"), i, _ce)
            i += 1
            continue

        if not consensus.get("trade"):
            _c_funnel["low_conviction"] += 1
            i += 1
            continue

        conviction = float(consensus.get("conviction", 0.0))
        if _enforce_min_conviction and conviction < _min_conviction:
            _c_funnel["low_conviction"] += 1
            i += 1
            continue

        _c_funnel["passed_gate"] += 1
        direction = consensus["direction"]
        if not direction:
            i += 1
            continue

        if i + 1 >= len(candles_h4):
            i += 1
            continue

        entry_bar = candles_h4[i + 1]
        raw_entry = float(entry_bar.get("open", entry_bar["close"]))
        _slip_mult = 3.0 if _canonical_vm == "live_parity" else 1.0
        slip = raw_entry * _get_slippage_for_bar(
            _bar_with_slippage_context(entry_bar, atr=atr), _ptype
        ) * _slip_mult
        entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

        _max_sl_pct = CONFIG.get("MAX_SL_PCT", {}).get(_ptype, 0.05)
        _bt_levels = _resolve_engine_c_bt_levels_after_fill(
            actual_entry=entry,
            consensus=consensus,
            style_profile=style_profile,
            resolved_style=resolved_style,
            direction=direction,
            pair_type=_ptype,
            atr=atr,
            max_sl_pct=_max_sl_pct,
            regime_state=(consensus.get("regime").get("state") if isinstance(consensus.get("regime"), dict) else {"TRENDING": 0, "RANGING": 1, "HIGH_VOLATILITY": 2, "LOW_VOLATILITY": 3}.get(str(consensus.get("regime")).upper(), 0)) if consensus.get("regime") else 0,
        )
        sl = _bt_levels.get("final_sl")
        tp = _bt_levels.get("final_tp")
        target_rr = _bt_levels.get("final_rr", 0.0)
        selected_tp_source = _bt_levels.get("selected_tp_source", "unknown")
        selected_sl_source = _bt_levels.get("selected_sl_source", "unknown")

        if sl is None or tp is None or target_rr <= 0:
            _c_funnel["invalid_bt_levels"] += 1
            i += 1
            continue
        if abs(entry - sl) / entry > _max_sl_pct:
            i += 1
            continue

        # PHASE 3A/B: Use entry_tf for monitoring and convert max_hold_bars from H4 to monitoring TF
        # Config MAX_HOLD is defined in H4 bars; convert to monitoring TF
        _monitor_tf = _entry_tf  # Use entry_tf from style_profile (H1 for intraday, H4 for swing)
        _monitor_candles = candles_h1 if _monitor_tf == "H1" else candles_h4
        _monitor_times = h1_times if _monitor_tf == "H1" else h4_times
        _monitor_fill_index = 0
        if _monitor_tf == "H4":
            # entry_bar is candles_h4[i + 1] in this loop, so monitoring starts there directly.
            _monitor_fill_index = i + 1
        elif h1_times is not None and len(h1_times) > 0:
            _entry_bar_ts = pd.Timestamp(entry_bar["time"])
            if pd.notna(_entry_bar_ts):
                if _entry_bar_ts.tzinfo is None:
                    _entry_bar_ts = _entry_bar_ts.tz_localize("UTC")
                _monitor_fill_index = int(bisect.bisect_left(h1_times, _entry_bar_ts))
        
        # Convert H4-based MAX_HOLD to monitoring TF (H4->H1 = 4x, H4->H4 = 1x)
        _tf_multiplier = 4 if _monitor_tf == "H1" else 1
        _max_hold_monitor_bars = MAX_HOLD * _tf_multiplier
        
        # Forward monitoring on the correct timeframe based on entry_tf
        future_window = _monitor_candles[_monitor_fill_index: min(_monitor_fill_index + _max_hold_monitor_bars + 1, len(_monitor_candles))]

        outcome = "TIMEOUT"
        r_multiple = 0.0
        exit_bar_offset = 0
        risk = abs(entry - sl)
        _active_sl = sl
        _be_triggered = False
        max_hold_bars = MAX_HOLD

        # Additive diagnostics defaults (must exist for all trade payloads)
        max_favorable_excursion_r = 0.0
        max_adverse_excursion_r = 0.0
        highest_r_seen = 0.0
        lowest_r_seen = 0.0
        bars_to_mfe = None
        bars_to_mae = None
        be_armed = False
        be_trigger_r = None
        price_never_reached_tp = True
        price_never_reached_sl = True

        for fi, future in enumerate(future_window):
            exit_bar_offset = fi
            _bar_outcome, _both_hit = _resolve_barrier_exit(
                future, direction=direction, sl=_active_sl, tp1=tp,
                sl_outcome="BE" if _be_triggered else "SL",
            )
            if _both_hit:
                same_bar_both_hit += 1
            if _bar_outcome == "TP1":
                outcome = "TP1"
                r_multiple = round(target_rr, 2)
                price_never_reached_tp = False
                break
            if _bar_outcome in ("SL", "BE"):
                outcome = _bar_outcome
                r_multiple = 0.0 if outcome == "BE" else -1.0
                price_never_reached_sl = True if outcome == "BE" else False
                break
            if risk > 0:
                if direction == "LONG":
                    bar_r_high = (float(future["high"]) - entry) / risk
                    bar_r_low = (float(future["low"]) - entry) / risk
                else:
                    bar_r_high = (entry - float(future["low"])) / risk
                    bar_r_low = (entry - float(future["high"])) / risk
                if bar_r_high > max_favorable_excursion_r:
                    max_favorable_excursion_r = bar_r_high
                    bars_to_mfe = fi + 1
                if bar_r_low < max_adverse_excursion_r:
                    max_adverse_excursion_r = bar_r_low
                    bars_to_mae = fi + 1
                highest_r_seen = max(highest_r_seen, bar_r_high)
                lowest_r_seen = min(lowest_r_seen, bar_r_low)
            if not _be_triggered and risk > 0 and target_rr >= _be_min_rr:
                if direction == "LONG" and float(future["high"]) >= entry + risk * _be_arm_rr:
                    _active_sl = entry
                    _be_triggered = True
                    be_armed = True
                    be_trigger_r = _be_arm_rr
                elif direction == "SHORT" and float(future["low"]) <= entry - risk * _be_arm_rr:
                    _active_sl = entry
                    _be_triggered = True
                    be_armed = True
                    be_trigger_r = _be_arm_rr

        # Timeout sub-classification for analytics (Phase 1A)
        timeout_class = None
        forced_exit_pnl_sign = None
        forced_exit_result_r = None
        
        if outcome == "TIMEOUT" and future_window:
            last_close = float(future_window[-1]["close"])
            if risk > 0:
                open_r = ((last_close - entry) / risk) if direction == "LONG" else ((entry - last_close) / risk)
                r_multiple = round(max(-1.0, min(target_rr, open_r)), 2)
                
                # Classify timeout outcome
                if r_multiple > 0.01:  # Positive timeout
                    timeout_class = "TIMEOUT_PROFIT"
                    forced_exit_pnl_sign = "PROFIT"
                elif r_multiple < -0.01:  # Negative timeout
                    timeout_class = "TIMEOUT_LOSS"
                    forced_exit_pnl_sign = "LOSS"
                else:  # Near-zero timeout
                    timeout_class = "TIMEOUT_FLAT"
                    forced_exit_pnl_sign = "FLAT"
                
                forced_exit_result_r = r_multiple

        _fee_r = _bt_transaction_cost_r(entry, sl, _ptype)
        if _fee_r > 0:
            r_multiple = round(r_multiple - _fee_r, 4)

        bar_date = entry_bar.get("time", "")[:10] if entry_bar.get("time") else ""
        bars_held = exit_bar_offset + 1
        
        trades.append({
            "date": bar_date, "pair": pair["display"], "direction": direction,
            "score": round(conviction, 4), "entry": round(float(entry), 6),
            "sl": round(float(sl), 6), "tp1": round(float(tp), 6), "tp2": round(float(tp), 6),
            "outcome": outcome, "resultR": round(r_multiple, 2), "regime": regime_label,
            **build_strategy_lab_telemetry(
                engine="ENGINE_C",
                strategy_family="ENGINE_C_CONSENSUS",
                regime=regime_label,
                setup_type=consensus.get("setup_type"),
                timeframe="H4",
                failure_reason=outcome if outcome not in ["TP1", "TP2"] else None,
                entry_reason=None,
                exit_reason=outcome,
                source_module="backtest_runner",
                source_function="backtest_pair"
            ),
            "oos": _vf["oos_label"], "wf_fold": _vf["wf_fold"],
            "validation_mode": _canonical_vm, "volAdj": consensus.get("sizing_override", 1.0),
            "r_multiple": r_multiple, "verdict": consensus.get("verdict", ""),
            "tier": consensus.get("tier", ""), "conviction": round(conviction, 4),
            "rr_target": round(target_rr, 2),
            # PHASE 1A: Add timeout sub-classification fields
            "timeout_class": timeout_class,
            "forced_exit_pnl_sign": forced_exit_pnl_sign,
            "forced_exit_result_r": forced_exit_result_r,
            # PHASE 1E: Add additive diagnostics
            "resolved_style": resolved_style,
            "zone_tf_used": _zone_tf,
            "entry_tf_used": _entry_tf,
            "atr_tf_used": _atr_tf,
            "selected_tp_source": selected_tp_source,
            "selected_sl_source": selected_sl_source,
            "selected_target_rr": round(target_rr, 2),
            "selected_target_price": round(float(tp), 6),
            "selected_sl_price": round(float(sl), 6),
            "bars_held": bars_held,
            "max_favorable_excursion_r": round(max_favorable_excursion_r, 2),
            "max_adverse_excursion_r": round(max_adverse_excursion_r, 2),
            "be_armed": be_armed,
            "be_trigger_r": be_trigger_r,
            "max_hold_bars": max_hold_bars,
            "price_never_reached_tp": price_never_reached_tp,
            "price_never_reached_sl": price_never_reached_sl,
            "highest_r_seen": round(highest_r_seen, 2),
            "lowest_r_seen": round(lowest_r_seen, 2),
            "bars_to_mfe": bars_to_mfe,
            "bars_to_mae": bars_to_mae,
        })

        last_exit_bar = i + 1 + exit_bar_offset
        i = last_exit_bar + COOLDOWN

    result = _format_backtest_results(
        trades, pair, engine_type="ENGINE_C",
        same_bar_both_hit=same_bar_both_hit, validation_mode=_canonical_vm,
    )
    _attach_research_validation_payload(
        result, trades, canonical_vm=_canonical_vm, temporal_vm=_temporal_vm,
        purge_gap=purge_gap, folds=folds, mode_warning=_vm_mode_warning,
    )
    
    # PHASE 2: Override btStyle with actual resolved style for metadata parity
    if "error" not in result:
        result["btStyle"] = resolved_style
        result["btStyleRequested"] = requested_style

    _tp_count = sum(1 for t in trades if t.get("outcome") == "TP1")
    _sl_count = sum(1 for t in trades if t.get("outcome") == "SL")
    _be_count = sum(1 for t in trades if t.get("outcome") == "BE")
    _to_count = sum(1 for t in trades if t.get("outcome") == "TIMEOUT")
    
    # PHASE 1B: Timeout sub-counts for detailed reporting
    _to_profit = sum(1 for t in trades if t.get("timeout_class") == "TIMEOUT_PROFIT")
    _to_loss = sum(1 for t in trades if t.get("timeout_class") == "TIMEOUT_LOSS")
    _to_flat = sum(1 for t in trades if t.get("timeout_class") == "TIMEOUT_FLAT")
    
    log.warning(
        f"[ENGINE C BT] {pair['display']} done: {result.get('totalTrades', 0)} trades "
        f"(TP1={_tp_count} SL={_sl_count} BE={_be_count} TIMEOUT={_to_count} "
        f"PROFIT={_to_profit} LOSS={_to_loss} FLAT={_to_flat}), "
        f"WR {result.get('winRate', 0):.1f}%, PF {result.get('profitFactor', 0):.2f}, "
        f"SQN {result.get('sqn', 0):.2f}, style={resolved_style}"
    )
    log.warning(
        f"[ENGINE C BT FUNNEL] {pair['display']} "
        f"bars={_c_funnel['bars_evaluated']} "
        f"b_skip={_c_funnel['b_no_signal']} "
        f"low_conv={_c_funnel['low_conviction']} "
        f"passed={_c_funnel['passed_gate']}"
    )

    if "error" not in result:
        result["engine"] = "ENGINE_C"
        result["engineCFunnel"] = _c_funnel
        try:
            import sqlite3 as _sq
            _wf = result.get("wfSplit", {}) or {}
            with _sq.connect(_rt().AUDIT_DB, timeout=15.0) as _con:
                _con.execute(
                    "INSERT INTO backtest_results "
                    "(run_date,pair,asset_type,engine,trades,win_rate,profit_factor,"
                    "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,bt_min,atr_source,notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        pair["display"], pair.get("type", ""), "engine_c",
                        result.get("totalTrades", 0), result.get("winRate"),
                        result.get("profitFactor"), result.get("expectancy"),
                        result.get("sqn"), result.get("sharpe"), result.get("sortino"),
                        round(_wf.get("is_sqn"), 4) if _wf.get("is_sqn") is not None else None,
                        round(_wf.get("oos_sqn"), 4) if _wf.get("oos_sqn") is not None else None,
                        result.get("maxDrawdownPct"),
                        _min_conviction if _enforce_min_conviction else None,
                        f"{_atr_tf}_ATR",
                        (
                            f"engine=c;style={resolved_style};conviction_gate={_min_conviction}"
                            if _enforce_min_conviction
                            else f"engine=c;style={resolved_style};conviction_gate=none"
                        ),
                    ),
                )
                _con.commit()
        except Exception as _dbe:
            log.warning("[ENGINE C BT] backtest_results write failed: %s", _dbe)

    return result

# ── Engine D (Scalp — Fabio VP+OrderFlow) Backtest ────────────────────────────
def backtest_pair_scalp(pair: dict, validation_mode: str = "standard") -> dict | None:
    """Backtest Engine D scalp setups with a stable M15 execution proxy.

    Walk-forward approach:
      1. Fetch M15 structure candles
      2. At each closed M15 bar: build VP context, then run the Engine D setup pipeline
      3. If setup valid: enter at the next M15 bar, resolve SL/TP on M15 bars
      4. Collect trades, compute stats using _format_backtest_results()

    Live Engine D still executes on M1/M5. The backtest keeps the M15 proxy until
    lower-timeframe historical execution has been validated against known-good runs.
    """
    from scalp_engine import (
        _build_volume_profile,
        _build_trade_bucket_volume_profile,
        _check_aaa_sequence,
        _check_absorption,
        _check_cvd,
        _check_trade_bucket_cvd,
        _check_vwap_lean,
        _classify_market_state,
        _classify_setup,
        _calc_m15_atr,
        _coerce_utc_datetime,
        _crypto_sym_info,
        _guess_asset_type,
        _locate_price_vs_vp,
        _overlay_eodhd_volume_for_scalp,
        _scalp_cost_assumptions,
        _scalp_min_rr_for_group,
        calculate_scalp_levels,
        get_grade_sessions_for_mode,
        infer_bias_from_ema_stack,
        scalp_session_window,
        ai_quality_grade as _ai_quality_grade,
    )

    display = pair.get("display", pair.get("symbol", "UNKNOWN"))
    asset_type = pair.get("type", _guess_asset_type(display))
    cfg = CONFIG.get("SCALP_ENGINE", {})
    _bt_volume_source = "binance_candle" if asset_type == "crypto" else "mt5_tick"
    _scalp_pair_ctx = dict(pair) if isinstance(pair, dict) else {}
    _scalp_pair_ctx["display"] = display
    _scalp_pair_ctx["type"] = asset_type
    _scalp_score_group = get_pair_score_group(_scalp_pair_ctx)
    _grade_sess_backtest_mode = not bool(cfg.get("BT_GRADE_SESSION_LIVE_PARITY", False))

    if not cfg.get("BT_ENABLED", True):
        return {"error": f"Scalp backtest disabled in config for {display}"}

    walk_bars = int(cfg.get("BT_WALK_BARS", 12))
    max_concurrent = int(cfg.get("BT_MAX_CONCURRENT", 2))
    vp_bins = int(cfg.get("VP_BINS", 64))
    abs_vol_mult = float(cfg.get("ABSORPTION_VOL_MULT", 2.0))
    slippage_ticks = int(cfg.get("BT_SLIPPAGE_TICKS", 3))
    _grade_order = ["A", "B", "C", "D"]
    _min_grade_str = str(
        cfg.get("EXECUTION_MIN_GRADE", cfg.get("MIN_GRADE_AUTO_EXECUTE", cfg.get("MIN_GRADE", "C")))
    ).upper()
    _min_grade_idx = _grade_order.index(_min_grade_str) if _min_grade_str in _grade_order else 2
    scratch_enabled = bool(cfg.get("BT_SCRATCH_ENABLED", True))
    scratch_bars = max(1, int(cfg.get("BT_SCRATCH_BARS", 3)))
    scratch_min_r = max(0.0, float(cfg.get("BT_SCRATCH_MIN_R", 0.10)))

    log.info(f"[SCALP-BT] Starting backtest for {display} (type={asset_type})")

    # ── Fetch M15 candles ───────────────────────────────────────────────────
    try:
        if asset_type == "crypto":
            from scalp_engine import _scalp_fetch_candles
            pair_dict = {
                "display": display, "symbol": display.replace("/", ""),
                "type": "crypto", "source": "binance",
            }
            m15_fetch = _scalp_fetch_candles(pair_dict, "M15", 2000)
            if isinstance(m15_fetch, tuple):
                m15_raw = m15_fetch[0]
                if len(m15_fetch) > 1 and m15_fetch[1]:
                    _bt_volume_source = str(m15_fetch[1])
            else:
                m15_raw = m15_fetch
            # Live crypto fetches may include the current forming candle.  The
            # backtest must only walk closed bars, matching MT5 include_forming=False.
            if m15_raw and len(m15_raw) > 1:
                m15_raw = list(m15_raw[:-1])
        else:
            from scalp_engine import mt5_fetch_scalp_candles
            from mt5_executor import mt5_map_symbol
            mt5_sym = mt5_map_symbol(display)
            if not mt5_sym:
                return {"error": f"No MT5 symbol mapping for {display}"}
            m15_raw = mt5_fetch_scalp_candles(mt5_sym, "M15", 2000, include_forming=False)
            m15_raw, _bt_volume_source = _overlay_eodhd_volume_for_scalp(display, asset_type, "M15", m15_raw, live=False)
    except Exception as e:
        log.error(f"[SCALP-BT] Candle fetch failed for {display}: {e}")
        return {"error": f"Candle fetch failed: {e}"}

    if not m15_raw or len(m15_raw) < 100:
        return {"error": f"Insufficient M15 data for {display}: {len(m15_raw) if m15_raw else 0} bars (need 100+)"}

    def _normalize_bt_candles(raw: list | None, tf_minutes: int) -> list:
        out = []
        for seq, c in enumerate(raw or []):
            dt = _coerce_utc_datetime(c.get("time"))
            row = {
                "time": c.get("time"),
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "vol": float(c.get("vol", 0)),
                "_seq": seq,
                "_dt": dt,
                "_close_dt": (dt + timedelta(minutes=tf_minutes)) if dt else None,
            }
            out.append(row)
        return out

    # Normalize candles to ensure float types and attach optional bar timestamps.
    candles = _normalize_bt_candles(m15_raw, 15)

    def _resample_closed_m15_context(context: list, tf: str) -> list:
        tf_norm = str(tf or "M15").upper()
        factor_map = {"M15": 1, "H1": 4, "H4": 16}
        factor = factor_map.get(tf_norm)
        if factor is None:
            log.warning("[SCALP-BT] Unsupported BIAS_TIMEFRAME=%s; falling back to M15 bias", tf_norm)
            factor = 1
        if factor <= 1:
            return context
        closed_len = (len(context) // factor) * factor
        out = []
        for start in range(0, closed_len, factor):
            chunk = context[start:start + factor]
            if len(chunk) < factor:
                continue
            first = chunk[0]
            last = chunk[-1]
            out.append({
                "time": first.get("time"),
                "open": first["open"],
                "high": max(c["high"] for c in chunk),
                "low": min(c["low"] for c in chunk),
                "close": last["close"],
                "vol": sum(float(c.get("vol", 0) or 0) for c in chunk),
                "_seq": len(out),
                "_dt": first.get("_dt"),
                "_close_dt": last.get("_close_dt"),
            })
        return out

    bias_tf = str(cfg.get("BIAS_TIMEFRAME", "H1")).upper()

    # ── Volume quality check ────────────────────────────────────────────────
    raw_vols = [c["vol"] for c in candles]
    nonzero_vols = [v for v in raw_vols if v > 0]
    vol_quality_pct = round(len(nonzero_vols) / len(candles) * 100, 1) if candles else 0
    if vol_quality_pct < 30:
        log.warning(
            f"[SCALP-BT] {display}: LOW VOLUME QUALITY — only {vol_quality_pct}% of "
            f"M15 bars have non-zero tick volume. Absorption/CVD signals may be unreliable. "
            f"Consider using a crypto pair for orderflow backtesting."
        )

    # ── Pre-compute ATR for the full series ─────────────────────────────────
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    atr_full = calc_atr(highs, lows, closes, 14)

    # ── Walk-forward backtest loop ──────────────────────────────────────────
    trades = []
    active_exit_indices = []
    same_bar_both_hit_count = 0
    vp_lookback = max(
        20,
        int(cfg.get("BT_VP_LOOKBACK_BARS", cfg.get("SCALP_VP_LOOKBACK_BARS", cfg.get("VP_LOOKBACK_BARS", 50)))),
    )
    min_lookback = max(80, vp_lookback + 30)  # need enough bars for VP + bias/context

    def _bt_scalp_sym_info(ref_price: float) -> dict:
        """Point/digits/spread for BT — matches live crypto tiering + MT5 when available."""
        if asset_type == "crypto":
            return _crypto_sym_info(float(ref_price))
        try:
            from mt5_executor import mt5_get_symbol_info

            si = mt5_get_symbol_info(display)
            if isinstance(si, dict) and not si.get("error"):
                return {
                    "point": float(si.get("point") or 0.00001),
                    "digits": int(si.get("digits") or 5),
                    "spread": float(si.get("spread") or 0),
                }
        except Exception:
            pass
        return _crypto_sym_info(float(ref_price))

    # OOS split: last 30% of trades flagged as out-of-sample
    total_bars = len(candles)

    for i in range(min_lookback, total_bars - walk_bars - 1):
        active_exit_indices = [exit_idx for exit_idx in active_exit_indices if exit_idx > i]
        # Skip if at max concurrent positions
        if len(active_exit_indices) >= max_concurrent:
            continue

        # Current M15 structure context. A setup is only known after this M15
        # bar is closed; lower-TF execution starts after signal_close_dt.
        structure_bar = candles[i]
        candle_dt = _coerce_utc_datetime(structure_bar.get("time"))
        signal_close_dt = structure_bar.get("_close_dt") or candle_dt
        m15_context = candles[: i + 1]
        # Backtest execution intentionally remains on the M15 proxy. The M1/M5
        # live path is not used here because it changed known-good historical
        # results by pairing later entries with stale M15 VP targets.
        exec_context = m15_context[-100:]
        context_for_vwap = m15_context[-vp_lookback:]

        current_price = structure_bar["close"]
        current_atr = atr_full[i] if i < len(atr_full) else 0
        if current_atr <= 0:
            continue
        session_ok, session_label = scalp_session_window(asset_type, when=signal_close_dt, backtest=True)
        if not session_ok:
            continue

        # ── Step 1: Run the same VP/setup pipeline as live scan ─────────────
        if len(m15_context) < vp_lookback:
            continue
        vp_window = m15_context[-vp_lookback:]
        vp = (
            _build_trade_bucket_volume_profile(display, reference_ts=signal_close_dt, require_fresh=False)
            if asset_type == "crypto" and cfg.get("TRADE_BUCKET_VP_ENABLED", True)
            else {"valid": False}
        )
        if not vp.get("valid"):
            if asset_type in ("forex", "index", "stock", "commodity") and cfg.get("VP_SESSION_AWARE", True):
                try:
                    from volume_profile import split_completed_sessions
                    _sessions = split_completed_sessions(
                        m15_context,
                        asset_type,
                        session_mode=str(cfg.get("SESSION_MODE", "london_ny")),
                    )
                    _prev = _sessions.get("prev_session_candles", [])
                    if len(_prev) >= 20:
                        vp_window = _prev
                except Exception:
                    pass
            try:
                vp = _build_volume_profile(vp_window, asset_type=asset_type)
            except TypeError:
                vp = _build_volume_profile(vp_window)
            if vp.get("valid"):
                vp.setdefault("volume_source", "candles")
        if not vp.get("valid") or vp.get("poc") is None:
            continue

        poc = vp["poc"]
        vah = vp["vah"]
        val = vp["val"]
        va_width = abs(vah - val)
        if va_width <= 0:
            continue

        market_state = _classify_market_state(vp)
        # Compute ATR for proximity + buffer scaling in backtest
        _bt_atr_m15 = _calc_m15_atr(m15_context, period=int(cfg.get("ATR_PERIOD", 14)))
        try:
            price_loc = _locate_price_vs_vp(
                current_price,
                vp,
                atr_m15=_bt_atr_m15,
                asset_type=asset_type,
                score_group=_scalp_score_group,
            )
        except TypeError:
            price_loc = _locate_price_vs_vp(current_price, vp, atr_m15=_bt_atr_m15)
        try:
            absorption = _check_absorption(exec_context, asset_type=asset_type, score_group=_scalp_score_group)
        except TypeError:
            absorption = _check_absorption(exec_context)
        cvd = (
            _check_trade_bucket_cvd(display, reference_ts=signal_close_dt, require_fresh=False)
            if asset_type == "crypto" and cfg.get("TRADE_BUCKET_CVD_ENABLED", True)
            else {"source": "disabled"}
        )
        if not cvd.get("direction"):
            try:
                cvd = _check_cvd(exec_context, asset_type=asset_type, score_group=_scalp_score_group)
            except TypeError:
                cvd = _check_cvd(exec_context)
            cvd["source"] = "candles"
        if (
            asset_type == "crypto"
            and cfg.get("STRICT_FABIO_GATE_ENABLED", True)
            and cfg.get("REQUIRE_AGGTRADE_FOR_CRYPTO_STRICT", True)
            and (vp.get("volume_source") != "binance_aggtrade" or cvd.get("source") != "binance_aggtrade")
        ):
            continue
        if (
            asset_type == "stock"
            and cfg.get("REQUIRE_REAL_VOLUME_FOR_STOCKS", True)
            and _bt_volume_source not in {"eodhd_1m", "eodhd_1h", "eodhd_hist", "ws_tick"}
        ):
            continue
        if cfg.get("AAA_ENABLED", True):
            try:
                aaa = _check_aaa_sequence(
                    exec_context,
                    absorption,
                    cvd,
                    asset_type=asset_type,
                    score_group=_scalp_score_group,
                )
            except TypeError:
                try:
                    aaa = _check_aaa_sequence(exec_context, absorption, cvd, asset_type=asset_type)
                except TypeError:
                    aaa = _check_aaa_sequence(exec_context, absorption, cvd)
        else:
            aaa = {"complete": False, "phase": "disabled"}
        vwap = _check_vwap_lean(context_for_vwap, current_price) if cfg.get("VWAP_ENABLED", True) else {"lean": None, "vwap_value": 0}
        bias_context = _resample_closed_m15_context(m15_context, bias_tf)
        htf_bias = infer_bias_from_ema_stack(bias_context) if len(bias_context) >= 200 else None
        setup = _classify_setup(
            market_state,
            price_loc,
            absorption,
            cvd,
            aaa,
            vwap,
            htf_bias,
            asset_type=asset_type,
            candles=exec_context,
        )
        if not setup.get("valid"):
            continue
        direction = setup["direction"]
        setup_type = setup["setup_type"]

        # Grade gate: compute grade from pipeline outputs BEFORE entering the trade.
        # Mirrors live run_scalp_scan() which skips grades below MIN_GRADE_AUTO_EXECUTE.
        _pre_grade_sessions = get_grade_sessions_for_mode(
            asset_type, when=signal_close_dt, backtest=_grade_sess_backtest_mode
        )
        _pre_quality = _ai_quality_grade(
            vp=vp, price_loc=price_loc, absorption=absorption, cvd=cvd,
            aaa=aaa, vwap=vwap, setup=setup, sessions=_pre_grade_sessions,
            spread_pips=0.0, htf_bias=htf_bias,
            asset_type=asset_type,
            pair=display,
        )
        _pre_grade = _pre_quality.get("grade", "D")
        if _grade_order.index(_pre_grade) > _min_grade_idx:
            continue

        if absorption.get("detected"):
            trigger_type = "absorption"
        elif cvd.get("direction") == direction:
            trigger_type = "cvd_shift"
        elif aaa.get("complete"):
            trigger_type = "aaa"
        else:
            trigger_type = "vwap" if vwap.get("lean") == direction else "setup"

        loc = price_loc.get("location")
        if loc == "at_val":
            zone_level = float(val)
        elif loc == "at_vah":
            zone_level = float(vah)
        elif loc == "at_poc":
            zone_level = float(poc)
        else:
            zone_level = float(price_loc.get("nearest_level") or (val if direction == "LONG" else vah))

        # ── Step 4: Calculate entry, SL, TP1, TP2 ───────────────────────────
        exit_tf = "M15"
        if i + 1 >= total_bars:
            continue
        entry_bar = candles[i + 1]
        walk_rows = candles[i + 2:i + 2 + walk_bars]
        entry_bar_idx = i + 1

        _sym_ref = _bt_scalp_sym_info(float(entry_bar["open"]))
        _point_bt = float(_sym_ref.get("point") or 1e-6)
        slippage = slippage_ticks * _point_bt

        if direction == "LONG":
            entry = float(entry_bar["open"]) + slippage
        else:
            entry = float(entry_bar["open"]) - slippage

        sym_info = _bt_scalp_sym_info(float(entry))
        _min_rr_bt = _scalp_min_rr_for_group(asset_type, _scalp_score_group)
        try:
            levels = calculate_scalp_levels(
                direction,
                entry,
                vp,
                setup_type,
                sym_info,
                asset_type,
                min_rr_override=_min_rr_bt,
                atr_m15=_bt_atr_m15,
                score_group=_scalp_score_group,
            )
        except TypeError:
            levels = calculate_scalp_levels(
                direction,
                entry,
                vp,
                setup_type,
                sym_info,
                asset_type,
                min_rr_override=_min_rr_bt,
                atr_m15=_bt_atr_m15,
            )
        if levels.get("rr_below_min"):
            continue
        sl = float(levels["sl"])
        tp1 = float(levels["tp1"])
        _tp2_raw = levels.get("tp2")
        tp2 = float(_tp2_raw) if _tp2_raw is not None else None
        tp_partial = float(levels.get("tp_partial") or tp1)
        sl_distance = abs(entry - sl)
        actual_rr = float(levels.get("rr") or 0)

        if sl_distance <= 0:
            continue

        if not math.isfinite(tp1) or (direction == "LONG" and tp1 <= entry) or (direction == "SHORT" and tp1 >= entry):
            continue

        # ── Step 5: Walk forward — Engine D Partial-exit model ──────────────────────────
        # Sequence: hit TP1 -> close ENGINE_D_TP1_SIZE (default 50%) -> move SL to BE -> trail to TP2/SL/TIMEOUT
        exit_reason = None
        exit_price = None
        exit_bar_idx = None
        best_favorable_r = 0.0
        
        tp1_hit = False
        tp2_hit = False
        sl_hit = False
        timeout_hit = False
        scratch_hit = False
        be_hit = False

        partial_enabled = bool(cfg.get("ENGINE_D_PARTIAL_EXIT_ENABLED", True))
        tp1_size = float(cfg.get("ENGINE_D_TP1_SIZE", 0.5)) if partial_enabled else 1.0
        move_sl_to_be = bool(cfg.get("ENGINE_D_MOVE_SL_TO_BE_AFTER_TP1", True))
        runner_enabled = bool(cfg.get("ENGINE_D_RUNNER_ENABLED", True))
        same_candle_policy = str(cfg.get("ENGINE_D_SAME_CANDLE_POLICY", "conservative_sl_first"))
        
        # If partials are disabled, TP1 is the terminal exit (100% size)
        if not partial_enabled or not runner_enabled:
            tp1_size = 1.0

        live_sl = sl
        be_armed = False
        be_stop = entry

        for w, wbar in enumerate(walk_rows, start=1):
            walk_idx = int(wbar.get("_seq", entry_bar_idx + w))
            if direction == "LONG":
                bar_high = wbar["high"]
                bar_low = wbar["low"]
                best_favorable_r = max(best_favorable_r, max(0.0, (bar_high - entry) / sl_distance))
                
                # Check for same-candle hits
                hit_sl_now = bar_low <= live_sl
                hit_tp1_now = bar_high >= tp1
                
            else:
                bar_high = wbar["high"]
                bar_low = wbar["low"]
                best_favorable_r = max(best_favorable_r, max(0.0, (entry - bar_low) / sl_distance))
                
                # Check for same-candle hits
                hit_sl_now = bar_high >= live_sl
                hit_tp1_now = bar_low <= tp1

            # Same candle resolution
            if hit_sl_now and hit_tp1_now and same_candle_policy == "conservative_sl_first":
                hit_tp1_now = False # assume SL hit first

            # 1. Check SL (or BE)
            if hit_sl_now:
                sl_hit = True
                exit_price = live_sl
                exit_bar_idx = walk_idx
                if be_armed:
                    be_hit = True
                break

            # 2. Check TP1
            if hit_tp1_now and not tp1_hit:
                tp1_hit = True
                if move_sl_to_be:
                    live_sl = be_stop
                    be_armed = True
                if tp1_size >= 1.0:
                    # Full exit
                    exit_price = tp1
                    exit_bar_idx = walk_idx
                    break

            # 3. Check TP2 (only if TP1 hit and runner enabled)
            if tp1_hit and tp1_size < 1.0 and tp2 is not None:
                hit_tp2_now = (direction == "LONG" and bar_high >= tp2) or (direction == "SHORT" and bar_low <= tp2)
                if hit_tp2_now:
                    tp2_hit = True
                    exit_price = tp2
                    exit_bar_idx = walk_idx
                    break

            # 4. Check Scratch
            if scratch_enabled and not tp1_hit and w >= scratch_bars and best_favorable_r < scratch_min_r:
                scratch_hit = True
                exit_price = wbar["close"]
                exit_bar_idx = walk_idx
                break

        # Timeout / End of Data fallback
        if not exit_bar_idx:
            timeout_bar = walk_rows[-1] if walk_rows else entry_bar
            timeout_idx = int(timeout_bar.get("_seq", entry_bar_idx))
            exit_price = timeout_bar["close"]
            timeout_hit = True
            exit_bar_idx = timeout_idx

        # Calculate Gross R
        def _calc_r(px):
            if direction == "LONG":
                return (px - entry) / sl_distance
            else:
                return (entry - px) / sl_distance

        if tp1_hit:
            if tp1_size >= 1.0:
                gross_R = _calc_r(tp1)
            else:
                runner_price = tp2 if tp2_hit else exit_price
                gross_R = (tp1_size * _calc_r(tp1)) + ((1.0 - tp1_size) * _calc_r(runner_price))
        else:
            gross_R = _calc_r(exit_price)

        # Path exit reason
        if tp1_hit:
            if tp1_size >= 1.0:
                path_exit_reason = "DIRECT_TP1_TERMINAL"
                primary_exit_reason = "TP1"
            elif tp2_hit:
                path_exit_reason = "TP1_THEN_TP2"
                primary_exit_reason = "TP2"
            elif sl_hit or be_hit:
                path_exit_reason = "TP1_THEN_BE" if be_hit else "TP1_THEN_SL"
                primary_exit_reason = "BE" if be_hit else "SL"
            elif timeout_hit:
                path_exit_reason = "TP1_THEN_TIMEOUT"
                primary_exit_reason = "TIMEOUT"
            else:
                path_exit_reason = "TP1_THEN_SCRATCH"
                primary_exit_reason = "SCRATCH"
        elif sl_hit:
            path_exit_reason = "DIRECT_SL"
            primary_exit_reason = "SL"
        elif scratch_hit:
            path_exit_reason = "SCRATCH_NO_TP1"
            primary_exit_reason = "SCRATCH_NO_FOLLOW_THROUGH"
        else:
            path_exit_reason = "TIMEOUT_NO_TP1"
            primary_exit_reason = "TIMEOUT"

        exit_reason = primary_exit_reason
        final_exit_reason = path_exit_reason

        # Fee & Slippage calculation
        # fee_R = estimated_fee_pct / risk_pct
        risk_pct = sl_distance / entry if entry > 0 else 0
        estimated_fee_pct, estimated_slippage_pct = _scalp_cost_assumptions(cfg, asset_type)
        fee_R = estimated_fee_pct / risk_pct if risk_pct > 0 else 0
        tick_slippage_R = slippage_ticks * _point_bt / sl_distance if sl_distance > 0 else 0
        asset_slippage_R = estimated_slippage_pct / risk_pct if risk_pct > 0 else 0
        slippage_R = max(tick_slippage_R, asset_slippage_R)
        
        # Only apply fees if we entered the trade
        net_R = round(gross_R - fee_R - slippage_R, 4)
        r_multiple = net_R

        # Grade through the same scorer as live scan
        _grade_sessions = get_grade_sessions_for_mode(
            asset_type, when=signal_close_dt, backtest=_grade_sess_backtest_mode
        )
        _quality = _ai_quality_grade(
            vp=vp, price_loc=price_loc, absorption=absorption, cvd=cvd, aaa=aaa,
            vwap=vwap, setup=setup, sessions=_grade_sessions, spread_pips=0.0, htf_bias=htf_bias,
            asset_type=asset_type,
            pair=display,
        )
        grade = _quality["grade"]
        grade_score = _quality["score"]

        # Determine OOS flag
        oos = i > total_bars * 0.70
        exit_structure_idx = exit_bar_idx if exit_bar_idx is not None else (i + 1)

        trade = {
            "bar_index": i,
            "entry_bar_index": entry_bar_idx,
            "exit_bar_index": exit_bar_idx,
            "structure_entry_bar_index": i + 1,
            "structure_exit_bar_index": exit_structure_idx,
            "execution_tf": exit_tf,
            "context_tf": "M15",
            "structure_tf": "M15",
            "symbol": display.replace("/", "") if asset_type == "crypto" else display,
            "pair": display,
            "asset_group": asset_type,
            "engine": "ENGINE_D",
            "strategy_family": "ENGINE_D_SCALP",
            "direction": direction,
            "setup_type": setup_type,
            "trigger_type": trigger_type,
            "trigger_pattern": trigger_type.upper() if trigger_type else "NONE",
            "grade": grade,
            "ai_grade": grade,
            "ai_score": grade_score,
            "ai_reasons": _quality.get("reasons", []),
            "size_multiplier": _quality.get("size_multiplier"),
            "entry": round(entry, 6),
            "sl": round(sl, 6),
            "tp_partial": round(tp_partial, 6),
            "tp1": round(tp1, 6),
            "tp2": round(tp2, 6) if tp2 else round(tp1, 6),
            "tp1_hit": bool(tp1_hit),
            "partial_taken_1r": bool(tp1_hit),
            "runner_be_armed": bool(be_armed),
            "exit_price": round(exit_price, 6),
            "exit_reason": exit_reason,
            "primary_exit_reason": primary_exit_reason,
            "final_exit_reason": final_exit_reason,
            "path_exit_reason": path_exit_reason,
            "gross_R": round(gross_R, 4),
            "fee_R": round(fee_R, 4),
            "slippage_R": round(slippage_R, 4),
            "net_R": net_R,
            "resultR": r_multiple,
            "r_multiple": r_multiple,
            "rr_planned": round(actual_rr, 2),
            "sl_distance": round(sl_distance, 6),
            "poc": round(poc, 6),
            "vah": round(vah, 6),
            "val": round(val, 6),
            "zone_level": round(zone_level, 6),
            "atr": round(current_atr, 6),
            "session": session_label,
            "oos": oos,
            "regime": "SCALP",
            "bars_held": (exit_bar_idx - entry_bar_idx) if exit_bar_idx else 0,
            **build_strategy_lab_telemetry(
                engine="ENGINE_D",
                strategy_family="ENGINE_D_SCALP",
                regime="breakout" if "breakout" in (setup_type or "").lower() else "unknown",
                setup_type=setup_type,
                timeframe=exit_tf,
                failure_reason=exit_reason if exit_reason not in ["TP1", "TP2", "TP_PARTIAL"] else None,
                entry_reason=None,
                exit_reason=exit_reason,
                source_module="backtest_runner",
                source_function="backtest_pair"
            ),
            "market_state": market_state,
            "price_location": price_loc.get("location"),
            "vp_volume_source": vp.get("volume_source", "candles"),
            "vp_bucket_count": vp.get("bucket_count"),
            "absorption_count": int(absorption.get("count", 0) or 0),
            "cvd_direction": cvd.get("direction"),
            "cvd_source": cvd.get("source", "candles"),
            "cvd_bucket_count": cvd.get("bucket_count"),
            "aaa_complete": bool(aaa.get("complete")),
            "vwap_lean": vwap.get("lean"),

            # Compatibility fields for _format_backtest_results
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "breaker_active": False,
            "fvg_overlap": False,
            "liquidity_sweep": False,
            "zone_touched": True,
            "bos_volume_confirmed": trigger_type == "absorption",
            "choch_confirmed": False,
        }
        trades.append(trade)
        if exit_bar_idx is not None:
            active_exit_indices.append(exit_structure_idx)

    # ── Format results ──────────────────────────────────────────────────────
    if not trades:
        return {"error": f"No scalp setups found for {display} in {total_bars} M15 bars"}

    log.info(
        f"[SCALP-BT] {display}: {len(trades)} trades from {total_bars} M15 bars "
        f"(same_bar_both_hit={same_bar_both_hit_count})"
    )

    result = _format_backtest_results(
        trades, pair, engine_type="SCALP_VP",
        same_bar_both_hit=same_bar_both_hit_count,
        validation_mode=validation_mode,
    )

    # Override style fields
    result["btStyle"] = "scalp"
    result["btStyleRequested"] = "scalp"
    result["engine"] = "scalp_vp"
    result["vol_quality_pct"] = vol_quality_pct
    result["vol_quality_warning"] = vol_quality_pct < 30
    result["structure_tf"] = "M15"
    result["context_tf"] = "M15"
    result["execution_tf"] = "M15"
    result["vp_lookback_bars"] = vp_lookback
    result["lower_tf_fallback"] = True
    result["backtest_model"] = "M15_STABLE_PROXY"

    # ── Scalp-specific analysis ─────────────────────────────────────────────
    absorption_trades = [t for t in trades if t.get("trigger_type") == "absorption"]
    cvd_trades = [t for t in trades if t.get("trigger_type") == "cvd_shift"]
    rejection_trades = [t for t in trades if t.get("trigger_type") == "rejection"]
    mr_trades = [t for t in trades if t.get("setup_type") == "mean_reversion"]
    trend_trades = [t for t in trades if str(t.get("setup_type", "")).startswith("trend")]

    def _subset_wr(subset):
        if not subset:
            return None
        wins = len([t for t in subset if t.get("r_multiple", 0) > 0])
        return round(wins / len(subset) * 100, 1)

    def _subset_avg_r(subset):
        if not subset:
            return None
        return round(sum(t.get("r_multiple", 0) for t in subset) / len(subset), 3)

    result["scalp_analysis"] = {
        "absorption": {"count": len(absorption_trades), "wr": _subset_wr(absorption_trades), "avg_r": _subset_avg_r(absorption_trades)},
        "cvd_shift": {"count": len(cvd_trades), "wr": _subset_wr(cvd_trades), "avg_r": _subset_avg_r(cvd_trades)},
        "rejection": {"count": len(rejection_trades), "wr": _subset_wr(rejection_trades), "avg_r": _subset_avg_r(rejection_trades)},
        "mean_reversion": {"count": len(mr_trades), "wr": _subset_wr(mr_trades), "avg_r": _subset_avg_r(mr_trades)},
        "trend": {"count": len(trend_trades), "wr": _subset_wr(trend_trades), "avg_r": _subset_avg_r(trend_trades)},
        "grade_A": {"count": len([t for t in trades if t.get("grade") == "A"]), "wr": _subset_wr([t for t in trades if t.get("grade") == "A"])},
        "grade_B": {"count": len([t for t in trades if t.get("grade") == "B"]), "wr": _subset_wr([t for t in trades if t.get("grade") == "B"])},
        "grade_C": {"count": len([t for t in trades if t.get("grade") == "C"]), "wr": _subset_wr([t for t in trades if t.get("grade") == "C"])},
    }

    # ── Save to DB ──────────────────────────────────────────────────────────
    try:
        import sqlite3 as _sq
        _wf = result.get("wfSplit", {}) or {}
        _is_sqn = _wf.get("is_sqn")
        _oos_sqn = _wf.get("oos_sqn")
        with _sq.connect(_rt().AUDIT_DB, timeout=15.0) as _con:
            _con.execute(
                "INSERT INTO backtest_results "
                "(run_date,pair,asset_type,engine,trades,win_rate,profit_factor,"
                "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,bt_min,atr_source,notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    display,
                    asset_type,
                    "scalp_vp",
                    result.get("totalTrades", 0),
                    result.get("winRate"),
                    result.get("profitFactor"),
                    result.get("expectancy"),
                    result.get("sqn"),
                    result.get("sharpe"),
                    result.get("sortino"),
                    round(_is_sqn, 4) if _is_sqn is not None else None,
                    round(_oos_sqn, 4) if _oos_sqn is not None else None,
                    result.get("maxDrawdownPct"),
                    _scalp_min_rr_for_group(asset_type, _scalp_score_group),
                    "M15_ATR",
                    f"vp_bins={vp_bins};vp_lookback={vp_lookback};exec_tf={result['execution_tf']};walk={walk_bars};abs_mult={abs_vol_mult};slippage={slippage_ticks}",
                ),
            )
            _con.commit()
        log.info(f"[SCALP-BT-DB] Saved: {display} SQN={result.get('sqn', 0):.2f} ({len(trades)} trades)")
    except Exception as _dbe:
        log.warning(f"[SCALP-BT-DB] Failed to save: {_dbe}")

    try:
        record_backtest_summary(
            engine="scalp_vp",
            pass_rate=None,
            expectancy_r=result.get("expectancy"),
            score=result.get("sqn"),
            max_score=10.0,
            meta={"pair": display, "trades": len(trades)},
        )
    except Exception:
        pass

    return result




def run_full_backtest(
    style="auto",
    asset_class: str | None = None,
    validation_mode: str = "standard",
    purge_gap: int = 200,
    folds: int = 3,
):
    """Run backtest_pair in parallel. Optional asset_class filter (crypto/forex/stock/commodity/index)."""

    from concurrent.futures import ThreadPoolExecutor, as_completed

    _valid_classes = {"crypto", "forex", "stock", "commodity", "index"}

    _ac = asset_class.lower().strip() if asset_class else None

    if _ac and _ac not in _valid_classes:
        return {
            "success": False,
            "error": f"Invalid asset_class '{asset_class}'. Valid: {sorted(_valid_classes)}",
            "results": [],
            "errors": [],
            "totalPairs": 0,
        }

    _jse_syms = {p["symbol"] for p in _rt().JSE_PAIRS}

    pairs_to_test = [p for p in _rt().ALL_PAIRS if p["symbol"] not in _jse_syms]

    if _ac:
        pairs_to_test = [p for p in pairs_to_test if p.get("type") == _ac]

    results = []
    _best_per_pair = {}
    errors = []

    def _bt(pair):

        try:
            return pair, backtest_pair(
                pair,
                style=style,
                validation_mode=validation_mode,
                purge_gap=purge_gap,
                folds=folds,
            )

        except Exception as e:
            return pair, {"error": str(e)}

    _bt_workers = int(CONFIG.get("BACKTEST_MAX_WORKERS", 6))
    with ThreadPoolExecutor(max_workers=_bt_workers) as pool:
        futures = {pool.submit(_bt, p): p for p in pairs_to_test}

        for fut in as_completed(futures):
            pair, r = fut.result()

            if "error" in r:
                errors.append({"pair": pair["display"], "error": r["error"]})

            else:
                results.append(r)

    results.sort(
        key=lambda x: x.get("sqn") if x.get("sqn") is not None else -999,
        reverse=True,
    )

    return {
        "success": True,
        "results": results,
        "errors": errors,
        "totalPairs": len(pairs_to_test),
        "assetClass": _ac or "all",
    }
