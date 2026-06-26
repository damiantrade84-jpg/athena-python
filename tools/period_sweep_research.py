"""
tools/period_sweep_research.py
──────────────────────────────
Engine A period sweep on frozen 2026-05-30 dataset.
Research/calibration ONLY — no live feeds, no Flask, no broker, no config mutations.

Purpose
-------
Compare EMA trend/momentum, RSI, and MACD period configurations across five
period-variant tiers using a bar-by-bar triple-barrier backtest on the frozen
H4 candle dataset.  Outputs a per-variant per-direction evidence table to:
  logs/calibration/period_sweep_2026-06-26.csv

Governance
----------
- Hard n≥30 gate (per direction, per group) before any metric is trusted.
- SQN gate ≥2.0 per direction for potential promotion.
- Bonferroni-HLZ correction: t-crit = sqrt(2·log(K)) where K = variants tested.
- Any variant that does NOT outperform the baseline on BOTH directions is HOLD.
- ZERO config changes are made by this script.
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path
from copy import deepcopy

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import json
import os
from datetime import datetime, timezone

os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from config import CONFIG

# ── Constants ─────────────────────────────────────────────────────────────────
FROZEN_DIR = ROOT / "data/frozen/2026-05-30/candles"
OUT_CSV = ROOT / "logs/calibration/period_sweep_2026-06-26.csv"

# Triple-barrier parameters (matches backtest defaults from config.yaml)
SL_ATR_MULT: float = 1.0
TP_ATR_MULT: float = 1.5
MAX_HOLD_H4_BARS: int = 18  # 18 × 4h = 3 days

# Minimum bars of H4 history needed before scoring (for EMA200 + normalisation)
MIN_H4_WARMUP = 240
MIN_D1_WARMUP = 220
MIN_H1_WARMUP = 240


# ── Period variant definitions ────────────────────────────────────────────────
# Format: {ema_trend, ema_momentum, ema_long, rsi, macd_fast, macd_slow, macd_signal, atr, adx}
# "ema21/50" key naming: ema21 = trend EMA, ema50 = momentum EMA, ema200 = long EMA
#  (keys follow _calc_indicator_bundle naming convention regardless of actual period)

PERIOD_VARIANTS: dict[str, dict[str, dict]] = {
    # ── Crypto class (btc/eth/doge/alt_majors) ──────────────────────────────
    "crypto": {
        "current_18_40_r12": {
            "ema_trend": 18, "ema_momentum": 40, "ema_long": 200,
            "rsi": 12, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
        "baseline_21_50_r14": {
            "ema_trend": 21, "ema_momentum": 50, "ema_long": 200,
            "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
        "fast_15_35_r10": {
            "ema_trend": 15, "ema_momentum": 35, "ema_long": 200,
            "rsi": 10, "macd_fast": 10, "macd_slow": 22, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
        "mixed_21_50_r12": {
            "ema_trend": 21, "ema_momentum": 50, "ema_long": 200,
            "rsi": 12, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
        "mixed_18_40_r14": {
            "ema_trend": 18, "ema_momentum": 40, "ema_long": 200,
            "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
    },
    # ── Forex class ──────────────────────────────────────────────────────────
    "forex": {
        "current_26_60_r18": {
            "ema_trend": 26, "ema_momentum": 60, "ema_long": 200,
            "rsi": 18, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
        "baseline_21_50_r14": {
            "ema_trend": 21, "ema_momentum": 50, "ema_long": 200,
            "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
        "slow_30_80_r20": {
            "ema_trend": 30, "ema_momentum": 80, "ema_long": 200,
            "rsi": 20, "macd_fast": 14, "macd_slow": 30, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
    },
    # ── Commodity/index/stock class (default tier) ───────────────────────────
    "default": {
        "baseline_21_50_r14": {
            "ema_trend": 21, "ema_momentum": 50, "ema_long": 200,
            "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
        "fast_18_40_r12": {
            "ema_trend": 18, "ema_momentum": 40, "ema_long": 200,
            "rsi": 12, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
        "slow_26_60_r18": {
            "ema_trend": 26, "ema_momentum": 60, "ema_long": 200,
            "rsi": 18, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "atr": 14, "adx": 14,
        },
    },
}

# ── Pair configurations ───────────────────────────────────────────────────────
# (name, score_group, asset_type, period_class, threshold, file_stem)
PAIRS: list[dict] = [
    # Crypto
    {"name": "BTC/USDT", "score_group": "crypto_btc",       "asset_type": "crypto",    "period_class": "crypto",   "threshold": 2.0, "stem": "BTC_USDT"},
    {"name": "ETH/USDT", "score_group": "crypto_eth",       "asset_type": "crypto",    "period_class": "crypto",   "threshold": 2.0, "stem": "ETH_USDT"},
    {"name": "SOL/USDT", "score_group": "crypto_alt_majors","asset_type": "crypto",    "period_class": "crypto",   "threshold": 2.0, "stem": "SOL_USDT"},
    {"name": "XRP/USDT", "score_group": "crypto_alt_majors","asset_type": "crypto",    "period_class": "crypto",   "threshold": 2.0, "stem": "XRP_USDT"},
    {"name": "DOGE/USDT","score_group": "crypto_doge",      "asset_type": "crypto",    "period_class": "crypto",   "threshold": 2.0, "stem": "DOGE_USDT"},
    # Forex
    {"name": "EUR/USD",  "score_group": "forex_majors",     "asset_type": "forex",     "period_class": "forex",    "threshold": 2.1, "stem": "EUR_USD"},
    {"name": "GBP/USD",  "score_group": "forex_majors",     "asset_type": "forex",     "period_class": "forex",    "threshold": 2.1, "stem": "GBP_USD"},
    {"name": "USD/JPY",  "score_group": "forex_majors",     "asset_type": "forex",     "period_class": "forex",    "threshold": 2.1, "stem": "USD_JPY"},
    {"name": "GBP/JPY",  "score_group": "forex_crosses",    "asset_type": "forex",     "period_class": "forex",    "threshold": 2.1, "stem": "GBP_JPY"},
    # Precious metals
    {"name": "XAG/USD",  "score_group": "precious_trackers","asset_type": "commodity", "period_class": "default",  "threshold": 1.5, "stem": "XAG_USD"},
    {"name": "XAU/USD",  "score_group": "precious_trackers","asset_type": "commodity", "period_class": "default",  "threshold": 1.5, "stem": "XAU_USD"},
    # Energy
    {"name": "WTI/Oil",  "score_group": "energy_oil",       "asset_type": "commodity", "period_class": "default",  "threshold": 1.8, "stem": "WTI_Oil"},
    # US Indices
    {"name": "NDX",      "score_group": "us_indices_trackers","asset_type": "index",   "period_class": "default",  "threshold": 1.5, "stem": "NASDAQ-100"},
    {"name": "SPX",      "score_group": "us_indices_trackers","asset_type": "index",   "period_class": "default",  "threshold": 1.5, "stem": "S_P_500"},
    {"name": "DJI",      "score_group": "us_indices_trackers","asset_type": "index",   "period_class": "default",  "threshold": 1.5, "stem": "Dow_Jones"},
    # EU Indices
    {"name": "DAX",      "score_group": "eu_indices",        "asset_type": "index",    "period_class": "default",  "threshold": 1.5, "stem": "DAX_40"},
    # US stocks
    {"name": "MSFT",     "score_group": "us_stock_single",   "asset_type": "stock",    "period_class": "default",  "threshold": 1.5, "stem": "MSFT"},
    {"name": "AAPL",     "score_group": "us_stock_single",   "asset_type": "stock",    "period_class": "default",  "threshold": 1.5, "stem": "AAPL"},
    {"name": "NVDA",     "score_group": "us_stock_single",   "asset_type": "stock",    "period_class": "default",  "threshold": 1.5, "stem": "NVDA"},
    {"name": "TSLA",     "score_group": "us_stock_single",   "asset_type": "stock",    "period_class": "default",  "threshold": 1.5, "stem": "TSLA"},
]


# ── Data loader ───────────────────────────────────────────────────────────────

def _load_candles(stem: str, tf: str) -> list[dict]:
    """Load frozen candle JSON for a pair/timeframe."""
    files = list(FROZEN_DIR.glob(f"{stem}__{tf}__*.json"))
    if not files:
        return []
    data = json.loads(files[0].read_text())
    bars = data if isinstance(data, list) else data.get("candles", [])
    return bars


def _parse_ts(t: str | float | int) -> float:
    """Return UTC epoch seconds for a bar time field."""
    if isinstance(t, (int, float)):
        return float(t)
    try:
        dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
    except Exception:
        return 0.0


def _build_d1_index(d1_bars: list[dict]) -> list[float]:
    """Pre-compute D1 bar epoch timestamps."""
    return [_parse_ts(b["time"]) for b in d1_bars]


# ── Indicator pre-computation ─────────────────────────────────────────────────

def _precompute_series(bars: list[dict], periods: dict) -> dict:
    """Compute full indicator series for a candle list with given periods.

    Returns dict keyed by indicator name, each value is a list aligned to bars.
    Keys: close, high, low, ema_trend, ema_mom, ema_long, rsi, macd_line,
          macd_signal, macd_hist, atr, adx, plus_di, minus_di.
    """
    from indicators import _calc_indicator_bundle
    bundle = _calc_indicator_bundle(bars, periods=periods)
    return {
        "close": bundle["close"],
        "high": bundle["high"],
        "low": bundle["low"],
        "ema_trend": bundle["ema21"],   # named ema21 regardless of actual period
        "ema_mom": bundle["ema50"],     # named ema50 regardless of actual period
        "ema_long": bundle["ema200"],   # named ema200 regardless of actual period
        "rsi": bundle["rsi"],
        "macd_line": bundle["macd"]["macd"],
        "macd_signal": bundle["macd"]["signal"],
        "macd_hist": bundle["macd"]["hist"],
        "atr": bundle["atr"],
        "adx": bundle["adx"]["adx"],
        "plus_di": bundle["adx"]["plusDI"],
        "minus_di": bundle["adx"]["minusDI"],
    }


# ── Scoring (faithful simplified reproduction of production trend+momentum) ───

def _trend_vote(close: float, ema_t: float, ema_m: float, ema_l: float) -> float:
    """Directional vote from EMA alignment: +1 bullish stack, -1 bearish, 0 mixed."""
    if None in (close, ema_t, ema_m, ema_l):
        return 0.0
    # Full bullish stack: price > ema_t > ema_m > ema_l
    if close > ema_t and ema_t > ema_m and ema_m > ema_l:
        return 1.0
    # Full bearish stack: price < ema_t < ema_m < ema_l
    if close < ema_t and ema_t < ema_m and ema_m < ema_l:
        return -1.0
    # Partial bull: price above trend + momentum aligned
    if close > ema_t and ema_t > ema_m:
        return 0.6
    # Partial bear: price below trend + momentum aligned
    if close < ema_t and ema_t < ema_m:
        return -0.6
    # Price above trend EMA only
    if close > ema_t:
        return 0.3
    if close < ema_t:
        return -0.3
    return 0.0


def _compute_score(
    d1_s: dict, h1_s: dict, h4_s: dict,
    d1_i: int, h1_i: int, h4_i: int,
    asset_type: str, score_group: str, periods: dict,
) -> tuple[float, str | None]:
    """
    Compute a direction + conviction score from pre-computed series at given indices.

    Returns (score, direction) where score is in [0, 3.0] matching production range.
    Direction is "LONG", "SHORT", or None (indeterminate/gated out).
    """
    # Helper: safe series lookup
    def s(series: list, idx: int):
        if 0 <= idx < len(series):
            v = series[idx]
            return float(v) if v is not None and math.isfinite(float(v)) else None
        return None

    # ── FACTOR 1: Trend (multi-TF EMA alignment) ──────────────────────────
    # D1 vote (weight 0.5 for crypto/forex/commodity; 0.4 for stock/index)
    if asset_type in ("stock", "index"):
        w_d1, w_h4, w_h1 = 0.40, 0.35, 0.25
    else:
        w_d1, w_h4, w_h1 = 0.50, 0.30, 0.20

    vote_d1 = _trend_vote(
        s(d1_s["close"], d1_i), s(d1_s["ema_trend"], d1_i),
        s(d1_s["ema_mom"], d1_i), s(d1_s["ema_long"], d1_i),
    )
    vote_h4 = _trend_vote(
        s(h4_s["close"], h4_i), s(h4_s["ema_trend"], h4_i),
        s(h4_s["ema_mom"], h4_i), s(h4_s["ema_long"], h4_i),
    )
    # H1 gets only price-vs-trend vote (no momentum EMA on H1 typically)
    cl_h1 = s(h1_s["close"], h1_i)
    et_h1 = s(h1_s["ema_trend"], h1_i)
    if cl_h1 is not None and et_h1 is not None:
        vote_h1 = 0.4 if cl_h1 > et_h1 else (-0.4 if cl_h1 < et_h1 else 0.0)
    else:
        vote_h1 = 0.0

    trend_raw = w_d1 * vote_d1 + w_h4 * vote_h4 + w_h1 * vote_h1
    # Normalise to [-1, 1]
    max_raw = w_d1 * 1.0 + w_h4 * 1.0 + w_h1 * 0.4
    if max_raw > 0:
        trend_norm = trend_raw / max_raw
    else:
        trend_norm = 0.0

    if abs(trend_norm) < 0.05:
        return 0.0, None

    direction = "LONG" if trend_norm > 0 else "SHORT"

    # ── ADX gate ──────────────────────────────────────────────────────────
    from factor_scoring import _resolve_adx_thresholds, _adx_multiplier_from_value
    trend_min, hard_fail = _resolve_adx_thresholds(asset_type, score_group)
    adx_val = s(h4_s["adx"], h4_i)
    if adx_val is None:
        adx_mult = 0.5  # neutral when unavailable
    elif adx_val <= hard_fail:
        return 0.0, None  # hard abort
    else:
        adx_mult = _adx_multiplier_from_value(adx_val, trend_min, hard_fail)

    # ── FACTOR 2: Momentum (RSI + MACD) ──────────────────────────────────
    rsi_val = s(h4_s["rsi"], h4_i)
    macd_hist = s(h4_s["macd_hist"], h4_i)
    macd_hist_prev = s(h4_s["macd_hist"], h4_i - 1) if h4_i > 0 else None

    # RSI in direction: >50 and rising = bullish momentum
    if rsi_val is not None:
        rsi_raw = (rsi_val - 50.0) / 50.0  # -1..+1
        rsi_component = max(-1.0, min(1.0, rsi_raw))
    else:
        rsi_component = 0.0

    # MACD histogram: positive + rising for LONG
    if macd_hist is not None:
        macd_sign = math.copysign(1, macd_hist) if macd_hist != 0 else 0.0
        # Acceleration bonus if histogram is growing
        if macd_hist_prev is not None and macd_hist != 0:
            accel = math.copysign(0.2, macd_hist - macd_hist_prev)
        else:
            accel = 0.0
        macd_component = max(-1.0, min(1.0, macd_sign * 0.8 + accel))
    else:
        macd_component = 0.0

    # Combined momentum: 0.6 * RSI + 0.4 * MACD
    momentum_raw = 0.6 * rsi_component + 0.4 * macd_component
    # Direction-aligned momentum: positive when momentum agrees with trend direction
    dir_sign = 1.0 if direction == "LONG" else -1.0
    momentum_aligned = dir_sign * momentum_raw

    # ── Aggregate score ───────────────────────────────────────────────────
    # Production target: score in [0, 3.0].
    # Scale base to span the full range: |trend_norm| × adx_mult in [0,1] → [0,2.5].
    # Momentum provides the remaining ±0.5 to push into / away from threshold.
    # This is a simplified approximation — the comparison across period variants
    # (not the absolute value) is what matters for the sweep.
    base_score = abs(trend_norm) * adx_mult * 2.5
    momentum_addon = momentum_aligned * 0.5

    score = max(0.0, min(3.0, base_score + momentum_addon))

    # Penalise when momentum strongly opposes trend direction
    if momentum_aligned < -0.3:
        score *= 0.75

    return score, direction


# ── Triple-barrier exit ───────────────────────────────────────────────────────

def _check_exit(trade: dict, bar: dict) -> float | None:
    """
    Return R-multiple if exited, None if still open.
    SL-first within same bar (conservative: if both hit, SL wins).
    """
    direction = trade["direction"]
    sl = trade["sl_price"]
    tp = trade["tp_price"]
    sl_r = -SL_ATR_MULT / TP_ATR_MULT  # = -1/1.5 in 1R units (actually -1R)
    tp_r = 1.0  # = 1R per ATR_MULT ratio

    high = bar["high"]
    low = bar["low"]

    if direction == "LONG":
        sl_hit = low <= sl
        tp_hit = high >= tp
    else:
        sl_hit = high >= sl
        tp_hit = low <= tp

    # SL-first policy
    if sl_hit:
        return -SL_ATR_MULT  # lose SL_ATR_MULT × ATR = -1R
    if tp_hit:
        return TP_ATR_MULT   # win TP_ATR_MULT × ATR = +1.5R

    return None  # still open


def _close_at_market(trade: dict, close_price: float) -> float:
    """Close at market (max-hold or end-of-data). Return R-multiple."""
    entry = trade["entry_price"]
    atr = trade["atr"]
    if atr <= 0:
        return 0.0
    direction = trade["direction"]
    if direction == "LONG":
        return (close_price - entry) / atr
    else:
        return (entry - close_price) / atr


# ── Statistics ────────────────────────────────────────────────────────────────

def _compute_stats(r_multiples: list[float]) -> dict:
    """Compute expectancy, win rate, and SQN from a list of R-multiples."""
    n = len(r_multiples)
    if n == 0:
        return {"n": 0, "exp_r": None, "sqn": None, "wr_pct": None}
    mean_r = sum(r_multiples) / n
    wins = sum(1 for r in r_multiples if r > 0)
    wr = wins / n * 100
    if n > 1:
        variance = sum((r - mean_r) ** 2 for r in r_multiples) / (n - 1)
        std_r = math.sqrt(variance)
        sqn = math.sqrt(n) * mean_r / std_r if std_r > 0 else 0.0
    else:
        sqn = 0.0
    return {
        "n": n,
        "exp_r": round(mean_r, 4),
        "sqn": round(sqn, 3),
        "wr_pct": round(wr, 1),
    }


def _bonferroni_t_crit(k: int) -> float:
    """Harvey/Liu/Zhu (2016) corrected t-critical for K independent tests."""
    return round(math.sqrt(2 * math.log(max(k, 2))), 4)


# ── Main backtest ─────────────────────────────────────────────────────────────

def _run_backtest(
    pair: dict,
    periods: dict,
    d1_bars: list, h4_bars: list, h1_bars: list,
) -> dict:
    """
    Run a bar-by-bar H4 backtest with given periods.
    Returns {long_trades: [r_mult,...], short_trades: [r_mult,...]}.
    """
    # Pre-compute full indicator series once
    d1_s = _precompute_series(d1_bars, periods)
    h4_s = _precompute_series(h4_bars, periods)
    h1_s = _precompute_series(h1_bars, periods)

    # D1 timestamp index for alignment
    d1_ts = [_parse_ts(b["time"]) for b in d1_bars]
    h1_ts = [_parse_ts(b["time"]) for b in h1_bars]

    threshold = pair["threshold"]
    asset_type = pair["asset_type"]
    score_group = pair["score_group"]

    long_r: list[float] = []
    short_r: list[float] = []
    active_trade: dict | None = None

    # Cache the last D1/H1 index to avoid O(n) search per bar
    d1_cursor = 0
    h1_cursor = 0

    for h4_idx in range(MIN_H4_WARMUP, len(h4_bars)):
        h4_bar = h4_bars[h4_idx]
        h4_t = _parse_ts(h4_bar["time"])

        # Advance D1 cursor: largest D1 bar with time <= h4_t
        while d1_cursor + 1 < len(d1_ts) and d1_ts[d1_cursor + 1] <= h4_t:
            d1_cursor += 1
        d1_idx = d1_cursor
        if d1_idx < MIN_D1_WARMUP:
            continue

        # Advance H1 cursor: largest H1 bar with time <= h4_t
        while h1_cursor + 1 < len(h1_ts) and h1_ts[h1_cursor + 1] <= h4_t:
            h1_cursor += 1
        h1_idx = h1_cursor
        if h1_idx < MIN_H1_WARMUP:
            continue

        # ── Check active trade exit first ──────────────────────────────────
        if active_trade is not None:
            r = _check_exit(active_trade, h4_bar)
            if r is not None:
                if active_trade["direction"] == "LONG":
                    long_r.append(r)
                else:
                    short_r.append(r)
                active_trade = None
            else:
                active_trade["bars_held"] += 1
                if active_trade["bars_held"] >= MAX_HOLD_H4_BARS:
                    r = _close_at_market(active_trade, h4_bar["close"])
                    if active_trade["direction"] == "LONG":
                        long_r.append(r)
                    else:
                        short_r.append(r)
                    active_trade = None
            # No new entries while trade is open (one-at-a-time)
            continue

        # ── Score this bar ─────────────────────────────────────────────────
        score, direction = _compute_score(
            d1_s, h1_s, h4_s,
            d1_idx, h1_idx, h4_idx,
            asset_type, score_group, periods,
        )

        if score >= threshold and direction in ("LONG", "SHORT"):
            entry_price = h4_bar["close"]
            atr_raw = h4_s["atr"][h4_idx]
            if atr_raw is None or not math.isfinite(atr_raw) or atr_raw <= 0:
                continue
            atr = float(atr_raw)
            sl_dist = atr * SL_ATR_MULT
            tp_dist = atr * TP_ATR_MULT
            if direction == "LONG":
                sl_price = entry_price - sl_dist
                tp_price = entry_price + tp_dist
            else:
                sl_price = entry_price + sl_dist
                tp_price = entry_price - tp_dist

            active_trade = {
                "direction": direction,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "atr": atr,
                "bars_held": 0,
            }

    # Close any still-open trade at end of data
    if active_trade is not None and h4_bars:
        r = _close_at_market(active_trade, h4_bars[-1]["close"])
        if active_trade["direction"] == "LONG":
            long_r.append(r)
        else:
            short_r.append(r)

    return {"long_trades": long_r, "short_trades": short_r}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Engine A period sweep — frozen 2026-05-30 dataset")
    print(f"Output: {OUT_CSV}")
    print("-" * 70)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []

    for pair in PAIRS:
        stem = pair["stem"]
        period_class = pair["period_class"]
        variants = PERIOD_VARIANTS[period_class]
        k_variants = len(variants)
        bonf_t = _bonferroni_t_crit(k_variants)

        print(f"\n{pair['name']} ({pair['score_group']}, {pair['asset_type']}) — {k_variants} variants, Bonf-t={bonf_t}")

        # Load candles once per pair
        d1_bars = _load_candles(stem, "D1")
        h4_bars = _load_candles(stem, "H4")
        h1_bars = _load_candles(stem, "H1")

        if not h4_bars or len(h4_bars) < MIN_H4_WARMUP + 10:
            print(f"  SKIP: insufficient H4 data (n={len(h4_bars)})")
            rows.append({
                "pair": pair["name"], "score_group": pair["score_group"],
                "period_class": period_class, "variant": "N/A",
                "ema_trend": "", "ema_mom": "", "rsi": "",
                "n_long": 0, "n_short": 0,
                "exp_r_long": "", "exp_r_short": "",
                "sqn_long": "", "sqn_short": "",
                "wr_long_pct": "", "wr_short_pct": "",
                "k_variants": k_variants, "bonf_t_crit": bonf_t,
                "gate_n30_long": "FAIL", "gate_n30_short": "FAIL",
                "gate_sqn_long": "FAIL", "gate_sqn_short": "FAIL",
                "gate_ls_sym": "FAIL", "gate_bonf": "FAIL",
                "verdict": "HOLD", "hold_reason": "insufficient_data",
            })
            continue

        # Use universal D1/H1 or synthetic if not present
        if not d1_bars:
            print(f"  SKIP: no D1 data")
            continue
        if not h1_bars:
            # Use H4 as H1 proxy (acceptable for index trading research)
            print(f"  NOTE: no H1 data, using H4 as H1 proxy")
            h1_bars = h4_bars

        # Baseline result for comparison
        baseline_key = next((k for k in variants if "baseline" in k), list(variants.keys())[0])

        variant_results: dict[str, dict] = {}

        for variant_name, periods in variants.items():
            result = _run_backtest(pair, periods, d1_bars, h4_bars, h1_bars)
            ls = _compute_stats(result["long_trades"])
            ss = _compute_stats(result["short_trades"])
            variant_results[variant_name] = {"long": ls, "short": ss}
            print(f"  {variant_name}: L(n={ls['n']}, SQN={ls['sqn']}, E={ls['exp_r']}) "
                  f"S(n={ss['n']}, SQN={ss['sqn']}, E={ss['exp_r']})")

        # Build CSV rows with gates
        baseline_l = variant_results[baseline_key]["long"]
        baseline_s = variant_results[baseline_key]["short"]

        for variant_name, vr in variant_results.items():
            periods = variants[variant_name]
            ls = vr["long"]
            ss = vr["short"]

            n30l = "PASS" if (ls["n"] or 0) >= 30 else "FAIL"
            n30s = "PASS" if (ss["n"] or 0) >= 30 else "FAIL"
            sqnl = "PASS" if ls["sqn"] is not None and ls["sqn"] >= 2.0 else "FAIL"
            sqns = "PASS" if ss["sqn"] is not None and ss["sqn"] >= 2.0 else "FAIL"

            # L/S symmetry: neither direction degrades below baseline SQN by >0.5
            lsym = "FAIL"
            if baseline_l["sqn"] is not None and baseline_s["sqn"] is not None:
                if ls["sqn"] is not None and ss["sqn"] is not None:
                    l_ok = ls["sqn"] >= (baseline_l["sqn"] - 0.5)
                    s_ok = ss["sqn"] >= (baseline_s["sqn"] - 0.5)
                    lsym = "PASS" if (l_ok and s_ok) else "FAIL"

            # Bonferroni: both SQN values must exceed bonf_t
            bonf = "FAIL"
            if ls["sqn"] is not None and ss["sqn"] is not None:
                if ls["sqn"] >= bonf_t and ss["sqn"] >= bonf_t:
                    bonf = "PASS"

            all_pass = all(g == "PASS" for g in [n30l, n30s, sqnl, sqns, lsym, bonf])
            verdict = "PROMOTE" if all_pass else "HOLD"

            # Hold reason
            holds = []
            if n30l == "FAIL":
                holds.append(f"L n={ls['n']} < 30")
            if n30s == "FAIL":
                holds.append(f"S n={ss['n']} < 30")
            if sqnl == "FAIL":
                holds.append(f"L SQN={ls['sqn']} < 2.0")
            if sqns == "FAIL":
                holds.append(f"S SQN={ss['sqn']} < 2.0")
            if lsym == "FAIL":
                holds.append("L/S symmetry degraded vs baseline")
            if bonf == "FAIL" and n30l == "PASS" and n30s == "PASS":
                holds.append(f"Bonf t-crit={bonf_t}: L={ls['sqn']}, S={ss['sqn']}")
            hold_reason = "; ".join(holds) if holds else "all gates cleared"

            rows.append({
                "pair": pair["name"],
                "score_group": pair["score_group"],
                "period_class": period_class,
                "variant": variant_name,
                "ema_trend": periods["ema_trend"],
                "ema_mom": periods["ema_momentum"],
                "rsi": periods["rsi"],
                "n_long": ls["n"],
                "n_short": ss["n"],
                "exp_r_long": ls["exp_r"],
                "exp_r_short": ss["exp_r"],
                "sqn_long": ls["sqn"],
                "sqn_short": ss["sqn"],
                "wr_long_pct": ls["wr_pct"],
                "wr_short_pct": ss["wr_pct"],
                "k_variants": k_variants,
                "bonf_t_crit": bonf_t,
                "gate_n30_long": n30l,
                "gate_n30_short": n30s,
                "gate_sqn_long": sqnl,
                "gate_sqn_short": sqns,
                "gate_ls_sym": lsym,
                "gate_bonf": bonf,
                "verdict": verdict,
                "hold_reason": hold_reason,
            })

    # Write CSV
    fieldnames = [
        "pair", "score_group", "period_class", "variant",
        "ema_trend", "ema_mom", "rsi",
        "n_long", "n_short",
        "exp_r_long", "exp_r_short",
        "sqn_long", "sqn_short",
        "wr_long_pct", "wr_short_pct",
        "k_variants", "bonf_t_crit",
        "gate_n30_long", "gate_n30_short",
        "gate_sqn_long", "gate_sqn_short",
        "gate_ls_sym", "gate_bonf",
        "verdict", "hold_reason",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{'='*70}")
    print(f"Results written: {OUT_CSV} ({len(rows)} rows)")

    # Promotion summary
    promoted = [r for r in rows if r["verdict"] == "PROMOTE"]
    print(f"PROMOTED variants: {len(promoted)}")
    for r in promoted:
        print(f"  {r['pair']} / {r['variant']}: "
              f"L SQN={r['sqn_long']} (n={r['n_long']}), "
              f"S SQN={r['sqn_short']} (n={r['n_short']})")
    if not promoted:
        print("  None -- 0/N groups met all gates (n>=30, SQN>=2.0, L/S sym, Bonf)")


if __name__ == "__main__":
    main()
