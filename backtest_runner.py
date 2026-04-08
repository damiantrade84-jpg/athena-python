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

from athena_runtime import rt as _art_rt
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
    get_pair_score_group,
)
from meta_learner import meta_report
from research_metrics import build_research_metrics, enrich_backtest_summary
from research_validation import (
    backtest_bar_validation_state,
    build_validation_report,
    normalize_validation_mode,
    temporal_validation_mode,
    volume_threshold_for_backtest,
)
from stability_monitor import record_backtest_summary


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


def _live_base_risk_pct(asset_type: str) -> float:
    """Mirror the live risk_engine base per-asset risk percentages."""
    return {
        "forex": 0.005,
        "crypto": 0.010,
        "stock": 0.010,
        "commodity": 0.010,
        "index": 0.010,
    }.get(asset_type, CONFIG["RISK_PCT"])


def _engine_a_bt_gate_note() -> str:
    """Explain Engine A backtest threshold routing for API payload (matches scoring.get_score_threshold)."""
    use_bt = bool(
        CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
        or CONFIG.get("RESEARCH_MODE", False)
    )
    sq_on = bool(CONFIG.get("SCAN_QUANTILE_ENABLED", False))
    if not use_bt:
        return (
            "Engine A backtest mirrors live thresholds (pair min_confluence → "
            "MIN_CONFLUENCE_GROUP → MIN_CONFLUENCE_CLASS). "
            "Set BACKTEST_USE_BT_MIN_THRESHOLDS to use BT_MIN / BT_MIN_GROUP / pair bt_min for backtest only. "
            "Live scan: get_min_confluence_threshold plus optional SCAN_QUANTILE_ENABLED."
            + ("" if sq_on else " SCAN_QUANTILE_ENABLED is off.")
        )
    return (
        "Backtest gates on pair bt_min → BT_MIN_GROUP → BT_MIN. "
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
            return base * 0.7

    return base


def _resolve_barrier_exit(
    bar: dict,
    *,
    direction: str,
    sl: float,
    tp1: float | None = None,
    tp2: float | None = None,
    sl_outcome: str = "SL",
) -> tuple[str | None, bool]:
    """Resolve TP/SL touches on a single OHLC bar conservatively."""
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
        return sl_outcome, True
    if hit_tp2:
        return "TP2", False
    if hit_tp1:
        return "TP1", False
    if hit_sl:
        return sl_outcome, False
    return None, False


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

        sym = pair["symbol"]

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
            # Crypto: paginated H4/H1 for 2+ years of data

            d1_raw = _rt().fetch_binance(sym, "1d", 750)

            h4_raw = _rt().fetch_binance_paginated(sym, "4h", 4400)

            h1_raw = _rt().fetch_binance_paginated(sym, "1h", 17600)

        elif pair["source"] == "mt5":
            # MT5 pairs (forex, commodities): MT5 is PRIMARY for D1/H4/H1, EODHD is fallback only
            d1_raw = _rt().fetch_candles(pair, "D1", 750)
            h4_raw = _rt().fetch_candles(pair, "H4", 4400)
            h1_raw = _rt().fetch_candles(pair, "H1", 17600)
            
            # Fallback to EODHD only if MT5 data is insufficient
            _d1_thin = not d1_raw or len(d1_raw or []) < 230
            _h4_thin = not h4_raw or len(h4_raw or []) < 500
            _h1_thin = not h1_raw or len(h1_raw or []) < 500
            
            if _d1_thin or _h4_thin or _h1_thin:
                log.info(
                    f"[BT] {pair['display']}: MT5 data thin (D1={len(d1_raw or [])}, H4={len(h4_raw or [])}, H1={len(h1_raw or [])}), trying EODHD fallback"
                )
                if _d1_thin:
                    _eodhd_d1 = _rt().extract_candles(_rt().fetch_eodhd(pair, "D1", 750))
                    if _eodhd_d1 and len(_eodhd_d1) > len(d1_raw or []):
                        d1_raw = _eodhd_d1
                if _h4_thin or _h1_thin:
                    _eodhd_h4, _eodhd_h1 = _rt().fetch_eodhd_intraday_bt(pair, days=730)
                    if _h4_thin and _eodhd_h4 and len(_eodhd_h4) > len(h4_raw or []):
                        h4_raw = _eodhd_h4
                    if _h1_thin and _eodhd_h1 and len(_eodhd_h1) > len(h1_raw or []):
                        h1_raw = _eodhd_h1
            
            # Final fallback to yfinance for D1 if still thin
            if not d1_raw or len(d1_raw or []) < 230:
                _yf_sym = _rt().yfinance_symbol_for_pair(pair)
                if _yf_sym:
                    log.info(f"[BT] {pair['display']}: trying yfinance D1 fallback")
                    _yf_d1 = _rt().fetch_yfinance(_yf_sym, "D1", 750)
                    if _yf_d1 and len(_yf_d1) > len(d1_raw or []):
                        d1_raw = _yf_d1

        elif _ptype in ("stock", "commodity", "index"):
            # Stocks/Commodities/Indices: EODHD D1 + EODHD intraday (730d)
            # Fallback chain: EODHD → Polygon (commodities) → yfinance
            d1_raw = _rt().extract_candles(_rt().fetch_eodhd(pair, "D1", 750)) or _rt().fetch_candles(
                pair, "D1", 750
            )

            h4_raw, h1_raw = _rt().fetch_eodhd_intraday_bt(pair, days=730)

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
            d1_raw = _rt().fetch_candles(pair, "D1", 750)

            h4_raw = _rt().fetch_candles(pair, "H4", 4400)

            h1_raw = _rt().fetch_candles(pair, "H1", 17600)

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

    # Engine A backtest gate: pair profile → group → class hierarchy.
    # Default: same as live (MIN_CONFLUENCE_*). If BACKTEST_USE_BT_MIN_THRESHOLDS or
    # RESEARCH_MODE is true in config, uses BT_MIN / BT_MIN_GROUP / pair bt_min instead.
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

        MIN_BARS = max(50, CONFIG.get("D1_CANDLES", 1001))
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
                d1i = calc_indicators_with_normalized(
                    d1_window, pair.get("type", "stock")
                )

                h4i = calc_indicators_with_normalized(
                    h4_window, pair.get("type", "stock")
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

                h1i = calc_indicators_with_normalized(
                    h1_window, pair.get("type", "stock")
                )

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

                # Route forex pairs to dedicated forex scoring engine in backtest
                if pair.get("type") == "forex":
                    from forex_scoring import compute_forex_score

                    _fx = compute_forex_score(
                        d1_snap=d1i["snap"],
                        h4_snap=h4i["snap"],
                        h1_snap=h1i["snap"],
                        h1_candles=h1_window,
                        pair=_pair_ctx,
                        bar_time=_bt_forex_d1_bar_time(d1_raw[i]["time"]),
                        backtest_mode=True,  # respect session gate (parity)
                        h4_candles=h4_window,
                        score_group=_pair_score_group,
                    )
                    # Derive proper regime label for forex backtest (not signal_type)
                    try:
                        from regime import detect_regime
                        _fx_regime_det = detect_regime(h4i["snap"], "forex")
                        _fx_trend_state = _fx_regime_det.get("label", "RANGING")
                    except Exception:
                        _fx_regime_det = {"state": 1}
                        _fx_trend_state = "RANGING"
                    res = {
                        "final_score": _fx.final_score,
                        "direction": _fx.direction,
                        "factor_scores": _fx.components,
                        "regime": {
                            "state": _fx_regime_det.get("state", 1),
                            "label": _fx_trend_state,
                        },  # Match calc_confluence format — use detected regime state, not hardcoded RANGING
                        "signal_type": _fx.signal_type,
                        "score": _fx.final_score,  # Add compatibility field for backtest
                        "trendState": _fx_trend_state,  # Add compatibility field
                    }
                    _fx_im = apply_confirmation_to_score(
                        float(res.get("score", 0.0) or 0.0),
                        str(res.get("direction") or "LONG"),
                        _pair_ctx,
                        _bt_intermarket_ctx,
                        max_score=FOREX_ENGINE_A_MAX_SCORE,
                        config=CONFIG,
                    )
                    res["score"] = float(_fx_im.get("adjusted_score", res["score"]))
                    res["intermarketConfirmation"] = _fx_im.get("confirmation") or {}
                    direction = _fx.direction
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

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            direction = res["direction"]

            # Realistic entry: signal on bar i close, enter at bar i+1 open (no lookahead)

            if i + 1 >= total_bars:
                i += 1
                continue

            entry_bar = d1_raw[i + 1]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            _slip_mult = 3.0 if _canonical_vm == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            atr = _rt().atr_for_levels(d1i, h4i, h1i, pair=pair, style=effective_style)

            if not atr or atr == 0:
                i += 1
                continue

            # C4: Use shared calc_levels (deduplicates with analyze_pair)

            _bt_regime_state = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _ptype, regime_state=_bt_regime_state,
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

            for j in range(i + 1, min(i + 21, total_bars)):
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
                    result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "TP1":
                    outcome = "TP1"
                    result_r = rr1 - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "SL":
                    _sl_slip_r = (
                        _get_slippage_for_bar(bar, _ptype) * sl / (atr * sl_mult)
                        if atr and sl_mult
                        else 0
                    )
                    outcome = "SL"
                    result_r = round(-1.0 - _sl_slip_r, 4)
                    exit_bar = j
                    break
            if outcome == "OPEN":
                # Force-close at last forward bar â€" record actual P&L vs recording a ghost 0R

                _last_fwd = d1_raw[min(i + 20, total_bars - 1)]

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
                    "oos": _vf["oos_label"],
                    "wf_fold": _vf["wf_fold"],
                    "validation_mode": _canonical_vm,
                    "volAdj": _vol_adj,
                }
            )

            if outcome not in ("OPEN",):
                last_exit_bar = exit_bar

                open_positions -= 1

            i = exit_bar + 1 if outcome != "OPEN" else i + 1

    elif effective_style == "intraday":  # walk H4 bars
        MIN_H4 = max(50, CONFIG.get("H4_CANDLES", 1001))
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
            d1_ctx = d1_raw[max(0, d1_idx - max(50, CONFIG.get("D1_CANDLES", 1001))) : d1_idx]

            if len(h1_window) < 50 or len(d1_ctx) < 50:
                i += 1
                continue

            try:
                h4i = calc_indicators_with_normalized(
                    h4_window, pair.get("type", "stock")
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

                h1i = calc_indicators_with_normalized(
                    h1_window, pair.get("type", "stock")
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

                # Route forex pairs to dedicated forex scoring engine (matches D1 BT + live scan)
                if pair.get("type") == "forex":
                    from forex_scoring import compute_forex_score

                    _fx = compute_forex_score(
                        d1_snap=d1i_ctx["snap"],
                        h4_snap=h4i["snap"],
                        h1_snap=h1i["snap"],
                        h1_candles=h1_window,
                        pair=_pair_ctx,
                        bar_time=h4_raw[i][
                            "time"
                        ],  # use actual current H4 bar datetime
                        backtest_mode=True,  # respect session gate (parity)
                        h4_candles=h4_window,
                        score_group=_pair_score_group,
                    )
                    try:
                        from regime import detect_regime
                        _fx_regime_det = detect_regime(h4i["snap"], "forex")
                        _fx_trend_state = _fx_regime_det.get("label", "RANGING")
                    except Exception:
                        _fx_regime_det = {"state": 1}
                        _fx_trend_state = "RANGING"
                    res = {
                        "final_score": _fx.final_score,
                        "direction": _fx.direction,
                        "factor_scores": _fx.components,
                        "regime": {
                            "state": _fx_regime_det.get("state", 1),
                            "label": _fx_trend_state,
                        },
                        "signal_type": _fx.signal_type,
                        "score": _fx.final_score,
                        "trendState": _fx_trend_state,
                    }
                    _fx_im = apply_confirmation_to_score(
                        float(res.get("score", 0.0) or 0.0),
                        str(res.get("direction") or "LONG"),
                        _pair_ctx,
                        _bt_intermarket_ctx,
                        max_score=FOREX_ENGINE_A_MAX_SCORE,
                        config=CONFIG,
                    )
                    res["score"] = float(_fx_im.get("adjusted_score", res["score"]))
                    res["intermarketConfirmation"] = _fx_im.get("confirmation") or {}
                    direction = _fx.direction
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

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            direction = res["direction"]

            # Realistic entry: signal on bar i close, enter at bar i+1 open (no lookahead)

            if i + 1 >= total_h4:
                i += 1
                continue

            entry_bar = h4_raw[i + 1]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            _slip_mult = 3.0 if _canonical_vm == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            atr = _rt().atr_for_levels(
                d1i_ctx, h4i, h1i, pair=pair, style=effective_style
            )

            if not atr or atr == 0:
                i += 1
                continue

            _bt_regime_state2 = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _ptype, regime_state=_bt_regime_state2,
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

            for j in range(i + 1, min(i + MAX_HOLD + 1, total_h4)):
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
                    result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "TP1":
                    outcome = "TP1"
                    result_r = rr1 - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "SL":
                    _sl_slip_r = (
                        _get_slippage_for_bar(bar, _ptype) * sl / (atr * sl_mult)
                        if atr and sl_mult
                        else 0
                    )
                    outcome = "SL"
                    result_r = round(-1.0 - _sl_slip_r, 4)
                    exit_bar = j
                    break
            if outcome == "OPEN":
                _last_fwd = h4_raw[min(i + MAX_HOLD, total_h4 - 1)]

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
                    "oos": _vf["oos_label"],
                    "wf_fold": _vf["wf_fold"],
                    "validation_mode": _canonical_vm,
                    "volAdj": _vol_adj,
                }
            )

            if outcome not in ("OPEN",):
                last_exit_bar = exit_bar

                open_positions -= 1

            i = exit_bar + 1 if outcome != "OPEN" else i + 1

    elif effective_style == "scalp":  # H1 bar walk-forward
        MIN_H1 = max(50, CONFIG.get("H1_CANDLES", 1001))
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
            h4_ctx = h4_raw[max(0, h4_idx - max(50, CONFIG.get("H4_CANDLES", 1001))) : h4_idx]

            # D1 context: real D1 data up to this point for Weinstein/regime

            d1_idx = bisect.bisect_left(d1_ts_sc, entry_ts)
            d1_ctx = d1_raw[max(0, d1_idx - max(50, CONFIG.get("D1_CANDLES", 1001))) : d1_idx]

            if len(h4_ctx) < 50 or len(d1_ctx) < 50:
                i += 1
                continue

            try:
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

                # Route forex pairs to dedicated forex scoring engine (matches D1 BT + live scan)
                if pair.get("type") == "forex":
                    from forex_scoring import compute_forex_score

                    _fx = compute_forex_score(
                        d1_snap=d1i_ctx["snap"],
                        h4_snap=h4i_ctx["snap"],
                        h1_snap=h1i["snap"],
                        h1_candles=h1_window,
                        pair=_pair_ctx,  # HIGH-01 fix: was missing pair arg
                        bar_time=h1_raw[i][
                            "time"
                        ],  # use actual current H1 bar datetime
                        backtest_mode=True,  # respect session gate (parity)
                        h4_candles=h4_ctx,
                        score_group=_pair_score_group,
                    )
                    try:
                        from regime import detect_regime
                        _fx_regime_det = detect_regime(h4i_ctx["snap"], "forex")
                        _fx_trend_state = _fx_regime_det.get("label", "RANGING")
                    except Exception:
                        _fx_regime_det = {"state": 1}
                        _fx_trend_state = "RANGING"
                    res = {
                        "final_score": _fx.final_score,
                        "direction": _fx.direction,
                        "factor_scores": _fx.components,
                        "regime": {
                            "state": _fx_regime_det.get("state", 1),
                            "label": _fx_trend_state,
                        },
                        "signal_type": _fx.signal_type,
                        "score": _fx.final_score,
                        "trendState": _fx_trend_state,
                    }
                    _fx_im = apply_confirmation_to_score(
                        float(res.get("score", 0.0) or 0.0),
                        str(res.get("direction") or "LONG"),
                        _pair_ctx,
                        _bt_intermarket_ctx,
                        max_score=FOREX_ENGINE_A_MAX_SCORE,
                        config=CONFIG,
                    )
                    res["score"] = float(_fx_im.get("adjusted_score", res["score"]))
                    res["intermarketConfirmation"] = _fx_im.get("confirmation") or {}
                    direction = _fx.direction
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

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            direction = res["direction"]

            if i + 1 >= total_h1:
                i += 1
                continue

            entry_bar = h1_raw[i + 1]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            _slip_mult = 3.0 if _canonical_vm == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            atr = _rt().atr_for_levels(
                d1i_ctx, h4i_ctx, h1i, pair=pair, style=effective_style
            )

            if not atr or atr == 0:
                i += 1
                continue

            _bt_regime_state3 = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _ptype, regime_state=_bt_regime_state3,
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

            for j in range(i + 1, min(i + MAX_HOLD + 1, total_h1)):
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
                    result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "TP1":
                    outcome = "TP1"
                    result_r = rr1 - (slip / (atr * sl_mult))
                    exit_bar = j
                    break
                if _bar_outcome == "SL":
                    _sl_slip_r = (
                        _get_slippage_for_bar(bar, _ptype) * sl / (atr * sl_mult)
                        if atr and sl_mult
                        else 0
                    )
                    outcome = "SL"
                    result_r = round(-1.0 - _sl_slip_r, 4)
                    exit_bar = j
                    break
            if outcome == "OPEN":
                _last_fwd = h1_raw[min(i + MAX_HOLD, total_h1 - 1)]

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
                    "oos": _vf["oos_label"],
                    "wf_fold": _vf["wf_fold"],
                    "validation_mode": _canonical_vm,
                    "volAdj": _vol_adj,
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

    # Sharpe ratio (annualized): mean(R) / std(R) * sqrt(trades_per_year)

    # Sortino ratio: mean(R) / downside_std(R) * sqrt(trades_per_year)

    if len(trades) >= 2:
        try:
            _dur_days = (
                pd.to_datetime(trades[-1]["date"]) - pd.to_datetime(trades[0]["date"])
            ).days

            _trades_per_year = len(trades) / max(1, _dur_days) * 365

        except Exception:
            _trades_per_year = float(len(trades))

    else:
        _trades_per_year = 1.0

    _std_r = _var**0.5 if _var > 0 else 0

    sharpe = round(avg_r / _std_r * (_trades_per_year**0.5), 2) if _std_r > 0 else 0

    _downside = [r for r in r_values if r < 0]

    _down_var = (
        sum(r**2 for r in _downside) / (len(_downside) - 1) if len(_downside) > 1 else 0
    )

    _down_std = _down_var**0.5

    sortino = (
        round(avg_r / _down_std * (_trades_per_year**0.5), 2) if _down_std > 0 else 0
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
                "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,eval_threshold,atr_source) "
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

    _bt_result = {
        "pair": pair["display"],
        "symbol": pair["symbol"],
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
        "scanQuantileEnabled": CONFIG.get("SCAN_QUANTILE_ENABLED", True),
        "bhReturn": bh_return,
        "calibrationReport": calibration_summary,
        "researchMetrics": research_metrics,
        "metaReport": meta_summary,
        "volumeThreshold": {
            "bt": CONFIG.get("VOLUME_THRESHOLD_BACKTEST", 1.2),
            "live": CONFIG.get("VOLUME_THRESHOLD", 1.5),
        },
        "same_bar_both_hit": same_bar_both_hit,
        "pairMaxScore": _pair_max_score,
        "equityCurve": equity_curve,
        "trades": trades[-50:],
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

    # Sharpe / Sortino (daily-ish approximation)
    _std = _var**0.5
    sharpe = round(avg_r / _std, 3) if _std > 0 else 0
    neg_dev = sum((r - avg_r) ** 2 for r in r_values if r < 0)
    sortino_denom = (neg_dev / len(r_values)) ** 0.5 if neg_dev > 0 else 0
    sortino = round(avg_r / sortino_denom, 3) if sortino_denom > 0 else 0

    # Simple equity curve (cumulative R)
    equity_curve = []
    eq = 1.0
    for rv in r_values:
        eq = round(eq * (1 + rv * 0.01), 4)
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
                _mc_eq = _mc_eq * (1 + rv * 0.01)
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

    result = {
        # ── Core identity (matches Engine A) ──────────────────────────────────
        "pair": pair["display"],
        "symbol": pair["symbol"],
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
        # ── Curves & trades (CRITICAL — JS crashes without these) ─────────────
        "equityCurve": equity_curve,
        "trades": trades[-50:],
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


    # --- REGIME SEGMENTED REPORTING & RESEARCH FOLDS ---
    regimes = {}
    is_vals, oos_vals = [], []
    for t in trades:
        rgm = t.get("regime", "UNKNOWN")
        r_mult = t.get("r_multiple", 0)
        if rgm not in regimes:
            regimes[rgm] = {"trades": 0, "wins": 0, "r_sum": 0.0}
        regimes[rgm]["trades"] += 1
        regimes[rgm]["r_sum"] += r_mult
        if r_mult > 0:
            regimes[rgm]["wins"] += 1

        if t.get("oos"):
            oos_vals.append(r_mult)
        else:
            is_vals.append(r_mult)

    for k, v in regimes.items():
        v["win_rate"] = round(v["wins"] / max(1, v["trades"]), 4)
        v["expectancy"] = round(v["r_sum"] / max(1, v["trades"]), 4)

    result["regime_performance"] = regimes
    result["validation_mode"] = validation_mode

    return enrich_backtest_summary(
        result,
        returns=r_values,
        in_sample_scores=is_vals,
        out_of_sample_scores=oos_vals,
        chosen_index=0,
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
        sym = pair["symbol"]
        candles_d1 = _rt().fetch_binance(sym, "1d", 750)
        candles_h4 = _rt().fetch_binance_paginated(sym, "4h", 4400)
        candles_h1 = _rt().fetch_binance_paginated(sym, "1h", 17600)
    elif pair.get("source") == "mt5":
        # MT5 pairs (forex, commodities): MT5 is PRIMARY for D1/H4/H1, EODHD is fallback only
        candles_d1 = _rt().fetch_candles(pair, "D1", 750)
        candles_h4 = _rt().fetch_candles(pair, "H4", 4400)
        candles_h1 = _rt().fetch_candles(pair, "H1", 17600)
        
        # Fallback to EODHD only if MT5 data is insufficient
        _d1_thin = not candles_d1 or len(candles_d1 or []) < 230
        _h4_thin = not candles_h4 or len(candles_h4 or []) < 500
        _h1_thin = not candles_h1 or len(candles_h1 or []) < 500
        
        if _d1_thin or _h4_thin or _h1_thin:
            log.info(
                f"[ENGINE B BT] {pair['display']}: MT5 data thin (D1={len(candles_d1 or [])}, H4={len(candles_h4 or [])}, H1={len(candles_h1 or [])}), trying EODHD fallback"
            )
            if _d1_thin:
                _eodhd_d1 = _rt().extract_candles(_rt().fetch_eodhd(pair, "D1", 750))
                if _eodhd_d1 and len(_eodhd_d1) > len(candles_d1 or []):
                    candles_d1 = _eodhd_d1
            if _h4_thin or _h1_thin:
                _eodhd_h4, _eodhd_h1 = _rt().fetch_eodhd_intraday_bt(pair, days=730)
                if _h4_thin and _eodhd_h4 and len(_eodhd_h4) > len(candles_h4 or []):
                    candles_h4 = _eodhd_h4
                if _h1_thin and _eodhd_h1 and len(_eodhd_h1) > len(candles_h1 or []):
                    candles_h1 = _eodhd_h1
        
        # Final fallback to yfinance for D1 if still thin
        if not candles_d1 or len(candles_d1 or []) < 230:
            _yf_sym = _rt().yfinance_symbol_for_pair(pair)
            if _yf_sym:
                log.info(f"[ENGINE B BT] {pair['display']}: trying yfinance D1 fallback")
                _yf_d1 = _rt().fetch_yfinance(_yf_sym, "D1", 750)
                if _yf_d1 and len(_yf_d1) > len(candles_d1 or []):
                    candles_d1 = _yf_d1
    else:
        # Non-MT5, non-Binance pairs (stocks, indices, etc.): EODHD primary
        candles_d1 = _rt().extract_candles(_rt().fetch_eodhd(pair, "D1", 750)) or _rt().fetch_candles(
            pair, "D1", 750
        )
        candles_h4, candles_h1 = _rt().fetch_eodhd_intraday_bt(pair, days=730)
        if not candles_h4 or not candles_h1:
            log.warning(f"[ENGINE B BT] {pair['display']} EODHD intraday failed, trying live cache")
            candles_h4 = _rt().fetch_candles(pair, "H4", 4400)
            candles_h1 = _rt().fetch_candles(pair, "H1", 17600)

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
        requested_style, score_group=_pair_score_group
    )
    _pair_type = pair.get("type", "stock")
    _forex_struct_tf = CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
    if _pair_type == "forex" and _forex_struct_tf == "D1" and resolved_style == "intraday" and requested_style in ("auto", "naked"):
        resolved_style, style_profile = _rt().naked_scan_style_profile(
            "swing", score_group=_pair_score_group
        )
        log.info(f"[ENGINE B BT] {pair['display']}: forex D1 structure → auto-promoted to swing style")
    _zone_tf = style_profile.get("zone_tf", "H4")
    _entry_tf = style_profile.get("entry_tf", "H1")
    _atr_tf = style_profile.get("atr_tf", "H4")
    _bt_sl_mode = str(CONFIG.get("ENGINE_B_BT_SL_MODE", "atr") or "atr").lower()
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

    _indicator_cache = {}

    def _cached_calc_indicators(candles, asset_type, cache_key):
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
        # Session filter: skip forex trades outside London (07-16 UTC) and NY (13-22 UTC)
        if pair.get("type") == "forex" and entry_time:
            try:
                from datetime import datetime as _dt
                _bar_dt = _dt.fromisoformat(str(entry_time).replace("Z", "+00:00"))
                _bar_hour = _bar_dt.hour
                # Skip Asian session (22:00 - 07:00 UTC) — low liquidity, wide spreads
                if _bar_hour >= 22 or _bar_hour < 7:
                    i += 1
                    continue
            except Exception:
                pass  # if time parsing fails, don't block the trade

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

        h4_ctx = candles_h4[:bisect.bisect_left(h4_times, entry_time)]
        d1_ctx = candles_d1[:bisect.bisect_left(d1_times, entry_time)]
        # entry_ctx is exactly where we are in the entry loop
        entry_ctx = entry_raw[:i + 1]

        if len(d1_ctx) < 20 or len(h4_ctx) < 20 or len(entry_ctx) < 20:
            i += 1
            continue
        current_price = float(entry_raw[i]["close"])
        
        # O(1) ATR LOOKUP: Select precomputed ATR value based on the current bar and _atr_tf
        _atr_full = atr_map.get(_atr_tf, atr_map["H4"])
        if _atr_tf == "D1":
            _idx = bisect.bisect_left(d1_times, entry_time)
        elif _atr_tf == "H4":
            _idx = bisect.bisect_left(h4_times, entry_time)
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
        regime_label = _rt().engine_b_regime_label(zone_ctx, pair.get("type", "stock"))
        _bt_b_d1_snap = {}
        _bt_b_zone_snap = {}
        try:
            _bt_b_d1_snap = (
                _cached_calc_indicators(d1_ctx, pair.get("type", "stock"), "d1") or {}
            ).get("snap") or {}
            _bt_b_zone_snap = (
                _cached_calc_indicators(zone_ctx, pair.get("type", "stock"), "zone") or {}
            ).get("snap") or {}
        except Exception:
            pass
        candidates = []
        for direction in ["LONG", "SHORT"]:
            res = naked_engine.analyze_structure(
                d1_ctx,
                zone_ctx,
                entry_ctx,
                current_price,
                direction,
                atr,
                regime_label,
                fallback_rr=style_profile.get("fallback_rr", 2.0),
                asset_type=pair.get("type", ""),
                enable_profile_context=_bt_enable_profile_context,
                d1_snap=_bt_b_d1_snap,
                h4_snap=_bt_b_zone_snap,
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
            _bt_regime = None
            try:
                _bt_regime = res.get("regime_state")
            except Exception:
                pass
            # PHASE 1C: Track actual BT level source fields
            selected_tp_source = "structural" if _bt_sl_mode == "structural" else "calc_levels"
            selected_sl_source = "structural" if _bt_sl_mode == "structural" else "calc_levels"
            
            if _bt_sl_mode == "structural":
                sl = res.get("recommended_stop_loss")
                tp = res.get("recommended_take_profit")
                if not tp and sl:
                    sl_dist = abs(entry - sl)
                    if direction == "LONG":
                        tp = entry + (sl_dist * style_profile.get("fallback_rr", 2.0))
                    else:
                        tp = entry - (sl_dist * style_profile.get("fallback_rr", 2.0))
                    selected_tp_source = "structural_fallback_rr"
            else:
                _lvl = calc_levels(
                    entry,
                    atr,
                    direction,
                    pair.get("type", "stock"),
                    regime_state=_bt_regime,
                    style=resolved_style,
                )
                sl = _lvl["sl"]
                tp = _lvl["tp1"]

            if sl is None or tp is None:
                continue

            rr = conf_data.get("rr", 0.0)
            if rr <= 0:
                sl_dist = abs(entry - sl)
                tp_dist = abs(tp - entry)
                rr = (tp_dist / sl_dist) if sl_dist > 0 else 0.0
            if rr <= 0:
                continue
            if rr < float(style_profile.get("min_rr", 1.0)):
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
                    "level_mode": _bt_sl_mode,
                    "selected_tp_source": selected_tp_source,
                    "selected_sl_source": selected_sl_source,
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
        slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
        entry = raw_entry + slip if direction == "LONG" else raw_entry - slip
        
        # Synchronize future_window to the correct H4 starting position
        _h4_fill_index = bisect.bisect_left(h4_times, entry_bar["time"])

        if best.get("level_mode") == "structural":
            sl = best["sl"]
            tp = best["tp"]
            _sl_dist = abs(entry - sl)
            _tp_dist = abs(tp - entry)
            target_rr = (_tp_dist / _sl_dist) if _sl_dist > 0 else 0.0
        else:
            _lvl = calc_levels(
                entry,
                atr,
                direction,
                _ptype,
                regime_state=best["res"].get("regime_state"),
                style=resolved_style,
            )
            sl = _lvl["sl"]
            tp = _lvl["tp1"]
            target_rr = _lvl.get("rr1", 0.0)

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

        # Engine B BT-only target relevance: cap post-fill TP to the style fallback_rr ceiling.
        _fallback_rr = style_profile.get("fallback_rr", 2.0)
        if target_rr > _fallback_rr:
            _sl_dist = abs(entry - sl)
            if _sl_dist > 0:
                if direction == "LONG":
                    tp = entry + (_sl_dist * _fallback_rr)
                else:
                    tp = entry - (_sl_dist * _fallback_rr)
                target_rr = _fallback_rr
                selected_tp_source = "capped_to_fallback_rr"

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
        # PHASE 3: Use config for Engine C BT exit controls instead of hardcoded values
        _bt_exit_config = CONFIG.get("ENGINE_C_BT_EXIT", {})
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
                # Check TP first — if price reached TP on this bar, it wins
                if f_high >= tp:
                    outcome = "TP1"
                    r_multiple = round(target_rr, 2)
                    price_never_reached_tp = False
                    break
                # Then check SL (before any BE modification)
                if f_low <= _active_sl:
                    outcome = "BE" if _be_triggered else "SL"
                    r_multiple = 0.0 if outcome == "BE" else -1.0
                    price_never_reached_sl = False
                    break
                # BE trigger — only activate if RR >= be_min_rr
                if not _be_triggered and risk > 0 and target_rr >= _be_min_rr:
                    if f_high >= entry + (risk * _be_arm_rr):
                        _active_sl = entry
                        _be_triggered = True
                        be_armed = True
                        be_trigger_r = _be_arm_rr
            else:
                # Check TP first
                if f_low <= tp:
                    outcome = "TP1"
                    r_multiple = round(target_rr, 2)
                    price_never_reached_tp = False
                    break
                # Then check SL
                if f_high >= _active_sl:
                    outcome = "BE" if _be_triggered else "SL"
                    r_multiple = 0.0 if outcome == "BE" else -1.0
                    price_never_reached_sl = False
                    break
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

        # Deduct round-trip transaction costs from r_multiple
        _fee_pct = CONFIG.get("FEE_PCT", {}).get(pair.get("type", "stock"), 0.0004)
        _sl_dist_fee = abs(entry - sl)
        if _sl_dist_fee > 0 and outcome != "TIMEOUT":
            _fee_r = _fee_pct * entry / _sl_dist_fee
            r_multiple = round(r_multiple - _fee_r, 4)

        bar_date = entry_bar.get("time", "")[:10] if entry_bar.get("time") else ""
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
                "bos_volume_confirmed": best["res"].get("bos_volume_confirmed", True),
                "choch_confirmed": best["res"].get("choch_confirmed", False),
                "ob_at_zone": best["res"].get("ob_at_zone", False),
                "bos_mtf_confirmed": best["res"].get("bos_mtf_confirmed", False),
                "breaker_active": best["conf"].get("breaker_active", False),
                "ob_strength": max((ob.get("strength", 0) for ob in best["res"].get("order_blocks", [])), default=0),
                # Forex-specific fields
                "fvg_bonus": best["res"].get("fvg_bonus", 0.0),
                "volume_strength": best["res"].get("volume_strength", 0.0),
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
        f"| passed_gate={_b_funnel['passed_gate']} ({_b_funnel['passed_gate']/_b_total*100:.1f}%)"
    )
    if "error" not in result:
        result["engineBFunnel"] = _b_funnel
    if "error" not in result:
        result["btStyle"] = resolved_style
        result["btStyleRequested"] = requested_style
        try:
            import sqlite3 as _sq

            _wf = result.get("wfSplit", {}) or {}
            _is_sqn = _wf.get("is_sqn")
            _oos_sqn = _wf.get("oos_sqn")
            with _sq.connect(_rt().AUDIT_DB, timeout=15.0) as _con:
                _con.execute(
                    "INSERT INTO backtest_results "
                    "(run_date,pair,asset_type,engine,trades,win_rate,profit_factor,"
                    "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,eval_threshold,atr_source,notes) "
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
        candles_d1 = _rt().fetch_binance(sym, "1d", 1000)
        candles_h4 = _rt().fetch_binance_paginated(sym, "4h", 5000)
        candles_h1 = _rt().fetch_binance_paginated(sym, "1h", 2000)
    elif pair.get("source") == "mt5":
        candles_d1 = _rt().fetch_candles(pair, "D1", 600)
        _yf_sym = _rt().yfinance_symbol_for_pair(pair)
        if (not candles_d1 or len(candles_d1 or []) < 230) and _yf_sym:
            _yf_d1 = _rt().fetch_yfinance(_yf_sym, "D1", 600)
            if _yf_d1 and len(_yf_d1) > len(candles_d1 or []):
                candles_d1 = _yf_d1
        candles_h4, candles_h1 = _rt().fetch_eodhd_intraday_bt(pair, days=730)
        if not candles_h4 or not candles_h1:
            log.info(f"[ENGINE C BT] {pair['display']}: EODHD failed, fetching from MT5")
            candles_h4 = candles_h4 or _rt().fetch_candles(pair, "H4", 5000)
            candles_h1 = candles_h1 or _rt().fetch_candles(pair, "H1", 5000)
    else:
        candles_d1 = _rt().extract_candles(_rt().fetch_eodhd(pair, "D1", 600)) or _rt().fetch_candles(pair, "D1", 600)
        candles_h4, candles_h1 = _rt().fetch_eodhd_intraday_bt(pair, days=730)
        if not candles_h4 or not candles_h1:
            candles_h4 = candles_h4 or _rt().fetch_candles(pair, "H4", 5000)
            candles_h1 = candles_h1 or _rt().fetch_candles(pair, "H1", 5000)

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
    _forex_struct_tf = CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
    resolved_style, style_profile = _rt().naked_scan_style_profile(
        "intraday", score_group=_pair_score_group
    )
    if _ptype == "forex" and _forex_struct_tf == "D1":
        resolved_style, style_profile = _rt().naked_scan_style_profile(
            "swing", score_group=_pair_score_group
        )
    _zone_tf = style_profile.get("zone_tf", "H4")
    _entry_tf = style_profile.get("entry_tf", "H1")
    _atr_tf = style_profile.get("atr_tf", "H4")
    _bt_sl_mode = str(CONFIG.get("ENGINE_B_BT_SL_MODE", "atr") or "atr").lower()
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

        if _ptype == "forex":
            try:
                from datetime import datetime as _dt
                _bar_dt = _dt.fromisoformat(str(entry_time).replace("Z", "+00:00"))
                if _bar_dt.hour >= 22 or _bar_dt.hour < 7:
                    i += 1
                    continue
            except Exception:
                pass

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
        d1_ctx = candles_d1[max(0, d1_idx - max(50, CONFIG.get("D1_CANDLES", 1001))): d1_idx]
        zone_ctx = h4_window

        if len(h4_window) < 50 or len(h1_window) < 50 or len(d1_ctx) < 50:
            i += 1
            continue

        current_price = float(candles_h4[i]["close"])

        _atr_full = atr_map.get(_atr_tf, atr_map["H4"])
        _atr_idx = h4_idx
        atr = _atr_full[_atr_idx - 1] if _atr_idx > 0 and _atr_idx <= len(_atr_full) else current_price * 0.01
        atr_list_50 = _atr_full[max(0, _atr_idx - 50): _atr_idx]
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

            if _ptype == "forex":
                from forex_scoring import compute_forex_score
                # HIGH-06 fix: use actual H4 bar time, NOT _bt_forex_d1_bar_time.
                # _bt_forex_d1_bar_time forces 13:00 UTC which suppresses the
                # London breakout window (07-09 UTC) in Engine-C scan.
                _bt_bar_time = candles_h4[i].get("time", "") if candles_h4 else ""
                _fx = compute_forex_score(
                    d1_snap=d1i["snap"], h4_snap=h4i["snap"], h1_snap=h1i["snap"],
                    h1_candles=h1_window, pair=_pair_ctx,
                    bar_time=_bt_bar_time,
                    backtest_mode=True, h4_candles=h4_window,
                    score_group=_pair_score_group,
                )
                from regime import detect_regime
                _fx_regime = detect_regime(h4i["snap"], "forex")
                signal_a = {
                    "confluenceScore": _fx.final_score, "maxScore": 2.0,
                    "direction": _fx.direction, "score": _fx.final_score,
                    "regime": {"label": _fx_regime.get("label", "RANGING")},
                    "sl": None, "tp1": None, "tp2": None, "rr1": 0,
                    "factor_scores": _fx.components,
                }
                a_direction = _fx.direction
            else:
                res_a = calc_confluence(
                    d1i, h4i, h1i, vr, stoch, _pair_ctx, btc_bias,
                    d1_candles=d1_ctx, h4_candles=h4_window, h1_candles=h1_window,
                    funding_rate=_bt_funding_rate, oi_data=_bt_oi_data, oi_context=_bt_oi_ctx,
                    bar_time=candles_h4[i].get("time") if candles_h4 else None,
                )
                _atr_c = _rt().atr_for_levels(d1i, h4i, h1i, pair=pair, style=resolved_style)
                _lvl_a = calc_levels(current_price, _atr_c or atr, res_a["direction"], _ptype,
                                     regime_state=res_a.get("regime", {}).get("state"),
                                     style=resolved_style) if _atr_c else {}
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
            )
            if res_b.get("structural_verdict") == "CLEAR":
                conf_b = naked_engine.calculate_confidence(
                    res_b, current_price, a_direction,
                    entry_candles=h1_window, style_profile=style_profile,
                )
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
        slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
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
            regime_state=(consensus.get("regime") or {}).get("state"),
        )
        sl = _bt_levels.get("final_sl")
        tp = _bt_levels.get("final_tp")
        target_rr = _bt_levels.get("final_rr", 0.0)
        selected_tp_source = _bt_levels.get("selected_tp_source", "unknown")
        selected_sl_source = _bt_levels.get("selected_sl_source", "unknown")

        if sl is None or tp is None or target_rr <= 0:
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

        _fee_pct = CONFIG.get("FEE_PCT", {}).get(_ptype, 0.0004)
        _sl_dist_fee = abs(entry - sl)
        if _sl_dist_fee > 0 and outcome != "TIMEOUT":
            r_multiple = round(r_multiple - (_fee_pct * entry / _sl_dist_fee), 4)

        bar_date = entry_bar.get("time", "")[:10] if entry_bar.get("time") else ""
        bars_held = exit_bar_offset + 1
        
        trades.append({
            "date": bar_date, "pair": pair["display"], "direction": direction,
            "score": round(conviction, 4), "entry": round(float(entry), 6),
            "sl": round(float(sl), 6), "tp1": round(float(tp), 6), "tp2": round(float(tp), 6),
            "outcome": outcome, "resultR": round(r_multiple, 2), "regime": regime_label,
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
                    "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,eval_threshold,atr_source,notes) "
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

def run_full_backtest(style="auto", asset_class: str | None = None):
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
            return pair, backtest_pair(pair, style=style)

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
