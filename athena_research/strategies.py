"""
Athena Research Lab — Strategy Families
Pure pandas/numpy signal generators.  No live imports.

Each strategy function accepts an OHLCV DataFrame and a params dict.
Returns a dict with keys:
  entries       pd.Series[bool]  — long entry signals
  exits         pd.Series[bool]  — long exit signals
  short_entries pd.Series[bool]  — short entry signals
  short_exits   pd.Series[bool]  — short exit signals
  meta          dict             — strategy metadata for reporting
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ── Shared indicator helpers ──────────────────────────────────────────────────

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    ag = gain.ewm(alpha=1 / n, adjust=False).mean()
    al = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(s: pd.Series, fast=12, slow=26, sig=9):
    m = _ema(s, fast) - _ema(s, slow)
    signal = _ema(m, sig)
    return m, signal, m - signal


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_h, prev_l = high.shift(1), low.shift(1)
    up_move = high - prev_h
    down_move = prev_l - low
    dm_plus = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    dm_minus = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = _atr(high, low, close, n)
    di_plus = 100 * _wilder(dm_plus, n) / tr.replace(0, np.nan)
    di_minus = 100 * _wilder(dm_minus, n) / tr.replace(0, np.nan)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return _wilder(dx, n)


def _bb(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    return mid + k * sd, mid, mid - k * sd


def _vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].replace(0, np.nan)
    if isinstance(df.index, pd.DatetimeIndex):
        session_key = df.index.normalize()
        cum_tp_v = (typical * vol).groupby(session_key).cumsum()
        cum_v = vol.groupby(session_key).cumsum()
    else:
        cum_tp_v = (typical * vol).cumsum()
        cum_v = vol.cumsum()
    return cum_tp_v / cum_v


def _pct_rank(s: pd.Series, n: int = 100) -> pd.Series:
    """Rolling percentile rank of *s* over last *n* bars (0–100)."""
    def _rank(x):
        return (x < x[-1]).sum() / len(x) * 100
    return s.rolling(n, min_periods=n // 2).apply(_rank, raw=True)


def _crossed_above(a: pd.Series, b: pd.Series) -> pd.Series:
    valid = a.notna() & b.notna() & a.shift(1).notna() & b.shift(1).notna()
    return valid & (a > b) & (a.shift(1) <= b.shift(1))


def _crossed_below(a: pd.Series, b: pd.Series) -> pd.Series:
    valid = a.notna() & b.notna() & a.shift(1).notna() & b.shift(1).notna()
    return valid & (a < b) & (a.shift(1) >= b.shift(1))


def _bool(s: pd.Series) -> pd.Series:
    return s.fillna(False).astype(bool)


def _empty(idx) -> pd.Series:
    return pd.Series(False, index=idx)


def _signals(entries=None, exits=None, short_entries=None, short_exits=None, idx=None, meta=None):
    if idx is None and entries is not None:
        idx = entries.index
    return {
        "entries": _bool(entries) if entries is not None else _empty(idx),
        "exits": _bool(exits) if exits is not None else _empty(idx),
        "short_entries": _bool(short_entries) if short_entries is not None else _empty(idx),
        "short_exits": _bool(short_exits) if short_exits is not None else _empty(idx),
        "meta": meta or {},
    }


# ── Strategy dataclass ────────────────────────────────────────────────────────

@dataclass
class StrategySpec:
    family: str
    name: str
    params: dict[str, Any]
    direction: str = "both"      # "long", "short", "both"
    tags: list[str] = field(default_factory=list)


# ── A. Trend / Momentum ───────────────────────────────────────────────────────

def strategy_ema_cross(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    fast = int(params.get("fast_period", 20))
    slow = int(params.get("slow_period", 50))
    adx_min = float(params.get("adx_min", 0))
    rsi_confirm = bool(params.get("rsi_confirm", False))

    if fast >= slow:
        return _signals(idx=df.index, meta={"skip": "fast>=slow"})

    ef = _ema(close, fast)
    es = _ema(close, slow)

    long_entries = _crossed_above(ef, es)
    long_exits = _crossed_below(ef, es)
    short_entries = _crossed_below(ef, es)
    short_exits = _crossed_above(ef, es)

    if adx_min > 0:
        adx = _adx(df["high"], df["low"], close)
        adx_ok = adx >= adx_min
        long_entries &= adx_ok
        short_entries &= adx_ok

    if rsi_confirm:
        rsi = _rsi(close)
        long_entries &= rsi > 50
        short_entries &= rsi < 50

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "trend_momentum", "sub": "ema_cross"})


def strategy_ema_alignment(df: pd.DataFrame, params: dict) -> dict:
    """EMA stack alignment — enter on pullback to short EMA during aligned state."""
    close = df["close"]
    s = int(params.get("ema_short", 20))
    m = int(params.get("ema_mid", 50))
    l = int(params.get("ema_long", 200))
    adx_min = float(params.get("adx_min", 0))

    es, em, el = _ema(close, s), _ema(close, m), _ema(close, l)

    # Alignment state (persistent, not transition-only)
    bullish_aligned = (es > em) & (em > el)
    bearish_aligned = (es < em) & (em < el)

    # Entry: aligned + pullback touch of short EMA
    bull_entry = bullish_aligned & (df["low"] <= es) & (close > es)
    bear_entry = bearish_aligned & (df["high"] >= es) & (close < es)

    long_exits = _crossed_below(close, em)
    short_exits = _crossed_above(close, em)

    if adx_min > 0:
        adx = _adx(df["high"], df["low"], close)
        adx_ok = adx >= adx_min
        bull_entry &= adx_ok
        bear_entry &= adx_ok

    return _signals(bull_entry, long_exits, bear_entry, short_exits,
                    meta={"family": "trend_momentum", "sub": "ema_alignment"})


def strategy_macd_direction(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    sig = int(params.get("signal_period", 9))
    adx_min = float(params.get("adx_min", 0))

    _, _, hist = _macd(close, fast, slow, sig)

    long_entries = _crossed_above(hist, pd.Series(0, index=df.index))
    long_exits = _crossed_below(hist, pd.Series(0, index=df.index))
    short_entries = _crossed_below(hist, pd.Series(0, index=df.index))
    short_exits = _crossed_above(hist, pd.Series(0, index=df.index))

    if adx_min > 0:
        adx = _adx(df["high"], df["low"], close)
        adx_ok = adx >= adx_min
        long_entries &= adx_ok
        short_entries &= adx_ok

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "trend_momentum", "sub": "macd_direction"})


# ── B. Pullback Continuation ──────────────────────────────────────────────────

def strategy_pullback_ema(df: pd.DataFrame, params: dict) -> dict:
    """
    Trend filter: close > ema(trend_period).
    Entry: pullback touch of ema(pullback_period) then candle reclaims above.
    Exit: close crosses below ema(pullback_period) in opposite direction.
    """
    close = df["close"]
    trend_p = int(params.get("trend_period", 200))
    pb_p = int(params.get("pullback_period", 50))
    rsi_reclaim = bool(params.get("rsi_reclaim", False))
    rsi_thresh = float(params.get("rsi_threshold", 50))

    trend_ema = _ema(close, trend_p)
    pb_ema = _ema(close, pb_p)

    # Bullish pullback: trend up, price dips to pb_ema then closes back above
    bullish_trend = close > trend_ema
    touched_pb = df["low"] <= pb_ema
    long_entries = bullish_trend & touched_pb & (close > pb_ema) & (close.shift(1) <= pb_ema.shift(1))

    bearish_trend = close < trend_ema
    touched_pb_bear = df["high"] >= pb_ema
    short_entries = bearish_trend & touched_pb_bear & (close < pb_ema) & (close.shift(1) >= pb_ema.shift(1))

    long_exits = _crossed_below(close, pb_ema)
    short_exits = _crossed_above(close, pb_ema)

    if rsi_reclaim:
        rsi = _rsi(close)
        long_entries &= rsi > rsi_thresh
        short_entries &= rsi < (100 - rsi_thresh)

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "pullback", "sub": "pullback_ema"})


# ── C. Breakout ───────────────────────────────────────────────────────────────

def strategy_prev_day_hl(df: pd.DataFrame, params: dict) -> dict:
    """Breakout of previous day high/low with optional ATR expansion filter."""
    close = df["close"]
    atr_min = float(params.get("atr_expand_min", 0.0))

    # Resample actual high/low (not the close series) to get prior-day levels
    try:
        daily_high = df["high"].resample("1D").max()
        daily_low = df["low"].resample("1D").min()
        prev_high = daily_high.shift(1).reindex(df.index, method="ffill")
        prev_low = daily_low.shift(1).reindex(df.index, method="ffill")
    except Exception:
        # Fallback for non-datetime index: rolling 24-bar proxy
        n = 24
        prev_high = df["high"].rolling(n).max().shift(1)
        prev_low = df["low"].rolling(n).min().shift(1)

    long_entries = _crossed_above(close, prev_high)
    long_exits = close < prev_low

    short_entries = _crossed_below(close, prev_low)
    short_exits = close > prev_high

    if atr_min > 0:
        atr = _atr(df["high"], df["low"], close)
        bar_range = (df["high"] - df["low"])
        expand = bar_range >= atr * atr_min
        long_entries &= expand
        short_entries &= expand

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "breakout", "sub": "prev_day_hl"})


def strategy_session_opening_range(df: pd.DataFrame, params: dict) -> dict:
    """Session opening-range breakout."""
    range_bars = int(params.get("range_bars", 6))
    atr_min = float(params.get("atr_expand_min", 0.0))
    close = df["close"]

    # Rolling high/low of last range_bars bars (proxy for opening range)
    range_high = df["high"].rolling(range_bars).max()
    range_low = df["low"].rolling(range_bars).min()

    long_entries = _crossed_above(close, range_high.shift(1))
    long_exits = close < range_low.shift(1)
    short_entries = _crossed_below(close, range_low.shift(1))
    short_exits = close > range_high.shift(1)

    if atr_min > 0:
        atr = _atr(df["high"], df["low"], close)
        bar_range = df["high"] - df["low"]
        expand = bar_range >= atr * atr_min
        long_entries &= expand
        short_entries &= expand

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "breakout", "sub": "session_opening_range"})


def strategy_london_breakout(df: pd.DataFrame, params: dict) -> dict:
    """London breakout (forex-focused): price breaks Asian range during London window."""
    utc_start = int(params.get("utc_start", 7))
    utc_end = int(params.get("utc_end", 11))
    atr_min = float(params.get("atr_expand_min", 0.0))
    close = df["close"]

    # Guard: session strategies require sub-daily data
    if not hasattr(df.index, "hour"):
        return _signals(idx=df.index, meta={"skip": "session_strategy_requires_intraday_data"})
    if pd.Series(df.index.hour).nunique() <= 1:
        return _signals(idx=df.index, meta={"skip": "session_strategy_requires_intraday_data"})

    # Asian range: bars before utc_start in same session day (proxy: rolling 8 bars)
    asian_high = df["high"].rolling(8).max().shift(1)
    asian_low = df["low"].rolling(8).min().shift(1)

    in_window = pd.Series(
        (df.index.hour >= utc_start) & (df.index.hour < utc_end),
        index=df.index
    )

    long_entries = in_window & _crossed_above(close, asian_high)
    long_exits = close < asian_low
    short_entries = in_window & _crossed_below(close, asian_low)
    short_exits = close > asian_high

    if atr_min > 0:
        atr = _atr(df["high"], df["low"], close)
        bar_range = df["high"] - df["low"]
        expand = bar_range >= atr * atr_min
        long_entries &= expand
        short_entries &= expand

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "breakout", "sub": "london_breakout"})


def strategy_ny_breakout(df: pd.DataFrame, params: dict) -> dict:
    """NY session breakout: indices and crypto."""
    utc_start = int(params.get("utc_start", 13))
    utc_end = int(params.get("utc_end", 16))
    atr_min = float(params.get("atr_expand_min", 0.0))
    close = df["close"]

    # Guard: session strategies require sub-daily data
    if not hasattr(df.index, "hour"):
        return _signals(idx=df.index, meta={"skip": "session_strategy_requires_intraday_data"})
    if pd.Series(df.index.hour).nunique() <= 1:
        return _signals(idx=df.index, meta={"skip": "session_strategy_requires_intraday_data"})

    pre_session_high = df["high"].rolling(8).max().shift(1)
    pre_session_low = df["low"].rolling(8).min().shift(1)

    in_window = pd.Series(
        (df.index.hour >= utc_start) & (df.index.hour < utc_end),
        index=df.index
    )

    long_entries = in_window & _crossed_above(close, pre_session_high)
    long_exits = close < pre_session_low
    short_entries = in_window & _crossed_below(close, pre_session_low)
    short_exits = close > pre_session_high

    if atr_min > 0:
        atr = _atr(df["high"], df["low"], close)
        bar_range = df["high"] - df["low"]
        expand = bar_range >= atr * atr_min
        long_entries &= expand
        short_entries &= expand

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "breakout", "sub": "ny_breakout"})


# ── D. Mean Reversion ─────────────────────────────────────────────────────────

def strategy_rsi_extreme(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    period = int(params.get("period", 14))
    oversold = float(params.get("oversold", 30))
    overbought = float(params.get("overbought", 70))

    rsi = _rsi(close, period)

    long_entries = _crossed_above(rsi, pd.Series(oversold, index=df.index))
    long_exits = rsi > 50
    short_entries = _crossed_below(rsi, pd.Series(overbought, index=df.index))
    short_exits = rsi < 50

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "mean_reversion", "sub": "rsi_extreme"})


def strategy_bollinger_touch(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    period = int(params.get("period", 20))
    num_std = float(params.get("num_std", 2.0))

    upper, mid, lower = _bb(close, period, num_std)

    long_entries = (close <= lower) & (close.shift(1) > lower.shift(1))
    long_exits = close >= mid
    short_entries = (close >= upper) & (close.shift(1) < upper.shift(1))
    short_exits = close <= mid

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "mean_reversion", "sub": "bollinger_touch"})


def strategy_vwap_deviation(df: pd.DataFrame, params: dict) -> dict:
    """VWAP deviation reversal — only valid when volume data is present."""
    close = df["close"]
    std_thresh = float(params.get("std_threshold", 2.0))

    if df["volume"].sum() == 0:
        return _signals(idx=df.index, meta={"skip": "no_volume"})

    vwap = _vwap(df)
    price_std = close.rolling(20).std()
    upper = vwap + std_thresh * price_std
    lower = vwap - std_thresh * price_std

    long_entries = (close <= lower) & (close.shift(1) > lower.shift(1))
    long_exits = close >= vwap
    short_entries = (close >= upper) & (close.shift(1) < upper.shift(1))
    short_exits = close <= vwap

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "mean_reversion", "sub": "vwap_deviation"})


# ── E. Volatility Compression / Expansion ────────────────────────────────────

def strategy_bb_squeeze_breakout(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    period = int(params.get("period", 20))
    squeeze_pct = float(params.get("squeeze_pct", 20))
    confirm_ema = bool(params.get("confirm_ema", True))

    upper, mid, lower = _bb(close, period)
    width = (upper - lower) / mid.replace(0, np.nan)
    rank = _pct_rank(width, 100)

    compressed = rank <= squeeze_pct
    expanding = rank > squeeze_pct

    if confirm_ema:
        ema50 = _ema(close, 50)
        long_entries = compressed.shift(1).fillna(False) & expanding & (close > ema50)
        short_entries = compressed.shift(1).fillna(False) & expanding & (close < ema50)
    else:
        rising = close > close.shift(period)
        long_entries = compressed.shift(1).fillna(False) & expanding & rising
        short_entries = compressed.shift(1).fillna(False) & expanding & ~rising

    long_exits = (close < mid) | (rank > 80)
    short_exits = (close > mid) | (rank > 80)

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "volatility", "sub": "bb_squeeze_breakout"})


def strategy_atr_compression(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    period = int(params.get("period", 14))
    squeeze_pct = float(params.get("squeeze_pct", 20))
    confirm_ema = bool(params.get("confirm_ema", True))

    atr = _atr(df["high"], df["low"], close, period)
    rank = _pct_rank(atr, 100)

    compressed = rank <= squeeze_pct
    expanding = rank > squeeze_pct

    if confirm_ema:
        ema50 = _ema(close, 50)
        long_entries = compressed.shift(1).fillna(False) & expanding & (close > ema50)
        short_entries = compressed.shift(1).fillna(False) & expanding & (close < ema50)
    else:
        rising = close > close.shift(period)
        long_entries = compressed.shift(1).fillna(False) & expanding & rising
        short_entries = compressed.shift(1).fillna(False) & expanding & ~rising

    long_exits = close < _ema(close, 50)
    short_exits = close > _ema(close, 50)

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "volatility", "sub": "atr_compression"})


# ── F. Engine B Structure Proxy ───────────────────────────────────────────────

def strategy_ob_bos(df: pd.DataFrame, params: dict) -> dict:
    """
    Proxy for Order Block + Break-of-Structure using pure price action.
    OB proxy: large engulfing candle followed by a pullback.
    BOS proxy: structural high/low break confirmed by close.
    """
    close, high, low, opn = df["close"], df["high"], df["low"], df["open"]
    swing_period = int(params.get("swing_period", 5))
    require_engulf = bool(params.get("require_engulf", False))
    require_vol = bool(params.get("require_vol_expand", False))

    # Swing high/low detection
    swing_high = high.rolling(swing_period * 2 + 1, center=True).max() == high
    swing_low = low.rolling(swing_period * 2 + 1, center=True).min() == low

    # BOS proxy: break of recent swing high (bullish) / swing low (bearish)
    recent_swing_high = high.where(swing_high).ffill().shift(1)
    recent_swing_low = low.where(swing_low).ffill().shift(1)

    long_bos = close > recent_swing_high
    short_bos = close < recent_swing_low

    # OB proxy: large bullish/bearish candle
    body = (close - opn).abs()
    full_range = high - low
    body_pct = body / full_range.replace(0, np.nan)
    strong_candle = body_pct >= 0.60

    if require_engulf:
        prev_body = (close.shift(1) - opn.shift(1)).abs()
        engulf = body > prev_body * 1.5
        long_bos &= engulf & strong_candle
        short_bos &= engulf & strong_candle
    else:
        long_bos &= strong_candle
        short_bos &= strong_candle

    if require_vol and df["volume"].sum() > 0:
        avg_vol = df["volume"].rolling(20).mean()
        vol_expand = df["volume"] > avg_vol * 1.2
        long_bos &= vol_expand
        short_bos &= vol_expand

    long_exits = close < recent_swing_low
    short_exits = close > recent_swing_high

    return _signals(long_bos, long_exits, short_bos, short_exits,
                    meta={"family": "engine_b_proxy", "sub": "ob_bos"})


def strategy_structure_filters(df: pd.DataFrame, params: dict) -> dict:
    """Strong-close + FVG proxy filters."""
    close, high, low, opn = df["close"], df["high"], df["low"], df["open"]
    strong_pct = float(params.get("strong_close_pct", 0.70))
    use_fvg = bool(params.get("fvg_detection", False))

    body_top = close.where(close > opn, opn)
    body_bot = close.where(close < opn, opn)
    candle_range = high - low
    close_pct = (close - low) / candle_range.replace(0, np.nan)

    bullish_strong = (close > opn) & (close_pct >= strong_pct)
    bearish_strong = (close < opn) & (close_pct <= (1 - strong_pct))

    if use_fvg:
        # FVG proxy: gap between current low and prior high (bullish), or vice-versa
        bull_fvg = low > high.shift(2)
        bear_fvg = high < low.shift(2)
        long_entries = bullish_strong & bull_fvg.shift(1).fillna(False)
        short_entries = bearish_strong & bear_fvg.shift(1).fillna(False)
    else:
        long_entries = bullish_strong
        short_entries = bearish_strong

    long_exits = _crossed_below(close, _ema(close, 20))
    short_exits = _crossed_above(close, _ema(close, 20))

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "engine_b_proxy", "sub": "structure_filters"})


# ── G. Scalp Momentum Proxies (NOT real Engine D) ─────────────────────────────
# WARNING: These are simplified pandas momentum/breakout strategies for Research Lab
# screening ONLY. They implement NONE of the real Engine D Fabio Valentini VP+
# order-flow methodology (POC/VAH/VAL, absorption, CVD, AAA, session grading).
# Do NOT use these results to guide live Engine D tuning.

def strategy_vwap_reclaim(df: pd.DataFrame, params: dict) -> dict:
    """VWAP reclaim/reject scalp — crypto/index focused."""
    close = df["close"]
    band_std = float(params.get("band_std", 1.0))
    atr_sl = float(params.get("atr_sl_mult", 1.0))

    if df["volume"].sum() == 0:
        return _signals(idx=df.index, meta={"skip": "no_volume"})

    vwap = _vwap(df)
    atr = _atr(df["high"], df["low"], close)
    band = band_std * atr

    long_entries = _crossed_above(close, vwap + band)
    long_exits = close < vwap - band * atr_sl
    short_entries = _crossed_below(close, vwap - band)
    short_exits = close > vwap + band * atr_sl

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "engine_d_proxy", "sub": "vwap_reclaim"})


def strategy_micro_breakout(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    range_bars = int(params.get("range_bars", 6))
    atr_sl = float(params.get("atr_sl_mult", 1.0))

    recent_high = df["high"].rolling(range_bars).max().shift(1)
    recent_low = df["low"].rolling(range_bars).min().shift(1)
    atr = _atr(df["high"], df["low"], close)

    long_entries = _crossed_above(close, recent_high)
    long_exits = close < recent_low - atr * atr_sl
    short_entries = _crossed_below(close, recent_low)
    short_exits = close > recent_high + atr * atr_sl

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "engine_d_proxy", "sub": "micro_breakout"})


def strategy_ema_scalp_pullback(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    ema_p = int(params.get("ema_period", 21))
    atr_sl = float(params.get("atr_sl_mult", 0.5))

    ema = _ema(close, ema_p)
    atr = _atr(df["high"], df["low"], close)

    # Pullback to EMA in trend direction
    bullish_trend = close > ema
    at_ema = df["low"] <= ema
    long_entries = bullish_trend & at_ema & (close > ema)

    bearish_trend = close < ema
    at_ema_bear = df["high"] >= ema
    short_entries = bearish_trend & at_ema_bear & (close < ema)

    long_exits = close < ema - atr * atr_sl
    short_exits = close > ema + atr * atr_sl

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "engine_d_proxy", "sub": "ema_scalp_pullback"})


# ── G2. CVD Momentum Proxy (NOT real Engine D) ──────────────────────────────

def strategy_cvd_momentum(df: pd.DataFrame, params: dict) -> dict:
    """Cumulative Volume Delta momentum — approximation via candle body ratio."""
    close, opn = df["close"], df["open"]
    smooth = int(params.get("smooth_period", 5))
    atr_sl = float(params.get("atr_sl_mult", 1.0))

    if df["volume"].sum() == 0:
        return _signals(idx=df.index, meta={"skip": "no_volume"})

    # CVD approximation: buy vol when close > open, sell vol otherwise
    bar_range = df["high"] - df["low"]
    buy_ratio = ((close - df["low"]) / bar_range.replace(0, np.nan)).fillna(0.5)
    buy_vol = df["volume"] * buy_ratio
    sell_vol = df["volume"] * (1 - buy_ratio)
    delta = buy_vol - sell_vol
    cvd = delta.cumsum()
    cvd_smooth = _ema(cvd, smooth)

    vwap = _vwap(df)
    long_entries = _crossed_above(cvd_smooth, cvd_smooth.shift(1)) & (close > vwap)
    short_entries = _crossed_below(cvd_smooth, cvd_smooth.shift(1)) & (close < vwap)

    atr = _atr(df["high"], df["low"], close)
    long_exits = close < vwap - atr * atr_sl
    short_exits = close > vwap + atr * atr_sl

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "engine_d_proxy", "sub": "cvd_momentum"})


# ── H. Stochastic Strategies ────────────────────────────────────────────────

def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                k_period: int = 14, k_smooth: int = 3, d_smooth: int = 3):
    """Stochastic %K and %D oscillator."""
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    raw_k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    k = raw_k.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


def strategy_stochastic_cross(df: pd.DataFrame, params: dict) -> dict:
    """Stochastic K/D crossover with oversold/overbought zones."""
    close = df["close"]
    kp = int(params.get("k_period", 14))
    ks = int(params.get("k_smooth", 3))
    ds = int(params.get("d_smooth", 3))
    oversold = float(params.get("oversold", 20))
    overbought = float(params.get("overbought", 80))

    k, d = _stochastic(df["high"], df["low"], close, kp, ks, ds)

    long_entries = _crossed_above(k, d) & (k < oversold + 10)
    long_exits = _crossed_below(k, d) & (k > 50)
    short_entries = _crossed_below(k, d) & (k > overbought - 10)
    short_exits = _crossed_above(k, d) & (k < 50)

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "stochastic", "sub": "stochastic_cross"})


def strategy_stochastic_divergence(df: pd.DataFrame, params: dict) -> dict:
    """Stochastic divergence — price makes new low/high but stochastic doesn't."""
    close = df["close"]
    kp = int(params.get("k_period", 14))
    ks = int(params.get("k_smooth", 3))
    ds = int(params.get("d_smooth", 3))
    lookback = int(params.get("lookback", 20))

    k, d = _stochastic(df["high"], df["low"], close, kp, ks, ds)

    # Bullish divergence: price lower low, stochastic higher low
    price_ll = close < close.rolling(lookback).min().shift(1)
    stoch_hl = k > k.rolling(lookback).min().shift(1)
    long_entries = price_ll & stoch_hl & (k < 30)

    # Bearish divergence: price higher high, stochastic lower high
    price_hh = close > close.rolling(lookback).max().shift(1)
    stoch_lh = k < k.rolling(lookback).max().shift(1)
    short_entries = price_hh & stoch_lh & (k > 70)

    long_exits = k > 70
    short_exits = k < 30

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "stochastic", "sub": "stochastic_divergence"})


# ── I. Fibonacci Retracement ─────────────────────────────────────────────────

def strategy_fib_retracement(df: pd.DataFrame, params: dict) -> dict:
    """Fibonacci retracement bounce — enter at fib level in trending market."""
    close, high, low = df["close"], df["high"], df["low"]
    level = float(params.get("level", 0.618))
    prox_pct = float(params.get("proximity_pct", 1.5)) / 100
    trend_p = int(params.get("trend_period", 50))

    # Trend direction via simple slope
    trend_ema = _ema(close, trend_p)
    uptrend = close > trend_ema
    downtrend = close < trend_ema

    # Rolling swing high/low for fib calc
    swing_high = high.rolling(trend_p).max()
    swing_low = low.rolling(trend_p).min()
    fib_range = swing_high - swing_low

    # Fib levels (bullish: retracement from high)
    fib_bull = swing_high - fib_range * level
    fib_bear = swing_low + fib_range * level

    # Proximity check
    near_bull = ((close - fib_bull).abs() / close) < prox_pct
    near_bear = ((close - fib_bear).abs() / close) < prox_pct

    long_entries = uptrend & near_bull & (close > fib_bull)
    short_entries = downtrend & near_bear & (close < fib_bear)

    long_exits = close < swing_low
    short_exits = close > swing_high

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "fibonacci", "sub": "fib_retracement"})


# ── J. Aroon Trend ──────────────────────────────────────────────────────────

def strategy_aroon_trend(df: pd.DataFrame, params: dict) -> dict:
    """Aroon oscillator crossover — trend direction and strength."""
    high, low = df["high"], df["low"]
    period = int(params.get("period", 14))
    threshold = float(params.get("threshold", 70))

    # Aroon Up/Down
    aroon_up = high.rolling(period + 1).apply(lambda x: x.argmax() / period * 100, raw=True)
    aroon_down = low.rolling(period + 1).apply(lambda x: x.argmin() / period * 100, raw=True)

    long_entries = _crossed_above(aroon_up, aroon_down) & (aroon_up > threshold)
    short_entries = _crossed_below(aroon_up, aroon_down) & (aroon_down > threshold)

    long_exits = aroon_down > threshold
    short_exits = aroon_up > threshold

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "aroon_family", "sub": "aroon_trend"})


# ── K. Divergence Strategies ────────────────────────────────────────────────

def strategy_obv_divergence(df: pd.DataFrame, params: dict) -> dict:
    """OBV divergence — price and volume trend disagreement."""
    close = df["close"]
    lookback = int(params.get("lookback", 20))

    if df["volume"].sum() == 0:
        return _signals(idx=df.index, meta={"skip": "no_volume"})

    # OBV
    vol_signed = df["volume"].where(close > close.shift(1), -df["volume"])
    vol_signed = vol_signed.where(close != close.shift(1), 0)
    obv = vol_signed.cumsum()

    obv_slope = obv - obv.shift(lookback)
    price_slope = close - close.shift(lookback)

    # Bullish divergence: price falling, OBV rising
    long_entries = (price_slope < 0) & (obv_slope > 0)
    # Bearish divergence: price rising, OBV falling
    short_entries = (price_slope > 0) & (obv_slope < 0)

    long_exits = _crossed_below(close, _ema(close, 20))
    short_exits = _crossed_above(close, _ema(close, 20))

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "divergence", "sub": "obv_divergence"})


def strategy_rsi_divergence(df: pd.DataFrame, params: dict) -> dict:
    """RSI divergence — hidden and regular divergence detection."""
    close = df["close"]
    lookback = int(params.get("lookback", 20))

    rsi = _rsi(close, 14)
    rsi_slope = rsi - rsi.shift(lookback)
    price_slope = close - close.shift(lookback)

    # Bullish: price new low, RSI higher low
    long_entries = (price_slope < 0) & (rsi_slope > 0) & (rsi < 40)
    # Bearish: price new high, RSI lower high
    short_entries = (price_slope > 0) & (rsi_slope < 0) & (rsi > 60)

    long_exits = rsi > 70
    short_exits = rsi < 30

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "divergence", "sub": "rsi_divergence"})


# ── L. Trend Following (Chandelier / Supertrend) ────────────────────────────

def strategy_chandelier_trend(df: pd.DataFrame, params: dict) -> dict:
    """Chandelier Exit — ATR trailing stop direction flip."""
    close, high, low = df["close"], df["high"], df["low"]
    atr_period = int(params.get("atr_period", 22))
    atr_mult = float(params.get("atr_mult", 3.0))
    lookback = int(params.get("lookback", 22))

    atr = _atr(high, low, close, atr_period)
    highest = high.rolling(lookback).max()
    lowest = low.rolling(lookback).min()

    long_stop = highest - atr_mult * atr
    short_stop = lowest + atr_mult * atr

    # Direction: long when close > short_stop, short when close < long_stop
    long_signal = close > short_stop
    short_signal = close < long_stop

    long_entries = long_signal & ~long_signal.shift(1).fillna(False)
    short_entries = short_signal & ~short_signal.shift(1).fillna(False)
    long_exits = short_signal
    short_exits = long_signal

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "trend_follow", "sub": "chandelier_trend"})


def strategy_supertrend_follow(df: pd.DataFrame, params: dict) -> dict:
    """Supertrend — ATR-based dynamic support/resistance bands."""
    close, high, low = df["close"], df["high"], df["low"]
    atr_period = int(params.get("atr_period", 10))
    atr_mult = float(params.get("atr_mult", 3.0))

    atr = _atr(high, low, close, atr_period)
    hl2 = (high + low) / 2
    upper = hl2 + atr_mult * atr
    lower = hl2 - atr_mult * atr

    # Use arrays for iterative band state to avoid chained Series writes.
    direction_arr = np.ones(len(df), dtype=int)
    final_upper_arr = upper.to_numpy(dtype=float, copy=True)
    final_lower_arr = lower.to_numpy(dtype=float, copy=True)
    upper_arr = upper.to_numpy(dtype=float, copy=False)
    lower_arr = lower.to_numpy(dtype=float, copy=False)
    close_arr = close.to_numpy(dtype=float, copy=False)

    for i in range(1, len(df)):
        if lower_arr[i] > final_lower_arr[i - 1]:
            final_lower_arr[i] = lower_arr[i]
        else:
            final_lower_arr[i] = final_lower_arr[i - 1]
        if upper_arr[i] < final_upper_arr[i - 1]:
            final_upper_arr[i] = upper_arr[i]
        else:
            final_upper_arr[i] = final_upper_arr[i - 1]

        if close_arr[i] > final_upper_arr[i - 1]:
            direction_arr[i] = 1
        elif close_arr[i] < final_lower_arr[i - 1]:
            direction_arr[i] = -1
        else:
            direction_arr[i] = direction_arr[i - 1]

    direction = pd.Series(direction_arr, index=df.index, dtype=int)

    long_entries = (direction == 1) & (direction.shift(1) == -1)
    short_entries = (direction == -1) & (direction.shift(1) == 1)
    long_exits = direction == -1
    short_exits = direction == 1

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "trend_follow", "sub": "supertrend_follow"})


# ── M. Realized Volatility Regime Breakout ──────────────────────────────────

def strategy_realized_vol_breakout(df: pd.DataFrame, params: dict) -> dict:
    """Low volatility compression → breakout expansion trade."""
    close = df["close"]
    lookback = int(params.get("lookback", 30))
    low_vol_pct = float(params.get("low_vol_pct", 25))
    atr_sl = float(params.get("atr_sl_mult", 1.5))

    # Realized vol via log returns
    log_ret = np.log(close / close.shift(1))
    rvol = log_ret.rolling(lookback).std() * np.sqrt(252)
    rvol_rank = _pct_rank(rvol, lookback * 4)

    compressed = rvol_rank <= low_vol_pct
    expanding = rvol_rank > low_vol_pct

    ema50 = _ema(close, 50)
    long_entries = compressed.shift(1).fillna(False) & expanding & (close > ema50)
    short_entries = compressed.shift(1).fillna(False) & expanding & (close < ema50)

    atr = _atr(df["high"], df["low"], close)
    long_exits = close < ema50 - atr * atr_sl
    short_exits = close > ema50 + atr * atr_sl

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "vol_regime", "sub": "realized_vol_breakout"})


# ── Registry ──────────────────────────────────────────────────────────────────

STRATEGY_REGISTRY: dict[str, tuple[str, Callable]] = {
    # family_name: (family_label, function)
    "ema_cross":              ("trend_momentum", strategy_ema_cross),
    "ema_alignment":          ("trend_momentum", strategy_ema_alignment),
    "macd_direction":         ("trend_momentum", strategy_macd_direction),
    "pullback_ema":           ("pullback",        strategy_pullback_ema),
    "prev_day_hl":            ("breakout",        strategy_prev_day_hl),
    "session_opening_range":  ("breakout",        strategy_session_opening_range),
    "london_breakout":        ("breakout",        strategy_london_breakout),
    "ny_breakout":            ("breakout",        strategy_ny_breakout),
    "rsi_extreme":            ("mean_reversion",  strategy_rsi_extreme),
    "bollinger_touch":        ("mean_reversion",  strategy_bollinger_touch),
    "vwap_deviation":         ("mean_reversion",  strategy_vwap_deviation),
    "bb_squeeze_breakout":    ("volatility",      strategy_bb_squeeze_breakout),
    "atr_compression":        ("volatility",      strategy_atr_compression),
    "ob_bos":                 ("engine_b_proxy",  strategy_ob_bos),
    "structure_filters":      ("engine_b_proxy",  strategy_structure_filters),
    "vwap_reclaim":           ("engine_d_proxy",  strategy_vwap_reclaim),
    "micro_breakout":         ("engine_d_proxy",  strategy_micro_breakout),
    "ema_scalp_pullback":     ("engine_d_proxy",  strategy_ema_scalp_pullback),
    "cvd_momentum":           ("engine_d_proxy",  strategy_cvd_momentum),
    "stochastic_cross":       ("stochastic",      strategy_stochastic_cross),
    "stochastic_divergence":  ("stochastic",      strategy_stochastic_divergence),
    "fib_retracement":        ("fibonacci",        strategy_fib_retracement),
    "aroon_trend":            ("aroon_family",     strategy_aroon_trend),
    "obv_divergence":         ("divergence",       strategy_obv_divergence),
    "rsi_divergence":         ("divergence",       strategy_rsi_divergence),
    "chandelier_trend":       ("trend_follow",     strategy_chandelier_trend),
    "supertrend_follow":      ("trend_follow",     strategy_supertrend_follow),
    "realized_vol_breakout":  ("vol_regime",       strategy_realized_vol_breakout),
}

_ENGINE_D_PROXY_STRATEGIES = ["vwap_reclaim", "micro_breakout", "ema_scalp_pullback", "cvd_momentum"]
FAMILY_ALIASES: dict[str, str] = {
    "scalp_momentum_proxy": "engine_d_proxy",
}

FAMILY_STRATEGIES: dict[str, list[str]] = {
    "trend_momentum": ["ema_cross", "ema_alignment", "macd_direction"],
    "pullback":       ["pullback_ema"],
    "breakout":       ["prev_day_hl", "session_opening_range", "london_breakout", "ny_breakout"],
    "mean_reversion": ["rsi_extreme", "bollinger_touch", "vwap_deviation"],
    "volatility":     ["bb_squeeze_breakout", "atr_compression"],
    "engine_b_proxy": ["ob_bos", "structure_filters"],
    "engine_d_proxy": _ENGINE_D_PROXY_STRATEGIES,
    # Backward-compatible alias for older UI/autopilot references.
    "scalp_momentum_proxy": _ENGINE_D_PROXY_STRATEGIES,
    "stochastic":     ["stochastic_cross", "stochastic_divergence"],
    "fibonacci":      ["fib_retracement"],
    "aroon_family":   ["aroon_trend"],
    "divergence":     ["obv_divergence", "rsi_divergence"],
    "trend_follow":   ["chandelier_trend", "supertrend_follow"],
    "vol_regime":     ["realized_vol_breakout"],
}


def generate_param_grid(strategy_name: str, param_config: dict) -> list[dict]:
    """Expand a parameter config dict into all combinations."""
    keys, values = [], []
    for k, v in param_config.items():
        keys.append(k)
        values.append(v if isinstance(v, list) else [v])
    combos = list(itertools.product(*values))
    return [dict(zip(keys, combo)) for combo in combos]


def iter_strategy_specs(
    families: list[str],
    strategy_params: dict,
    direction: str = "both",
) -> Iterator[StrategySpec]:
    """Yield all StrategySpec instances for the requested families."""
    for requested_family in families:
        canonical_family = FAMILY_ALIASES.get(requested_family, requested_family)
        strategy_names = FAMILY_STRATEGIES.get(requested_family, FAMILY_STRATEGIES.get(canonical_family, []))
        family_cfg = strategy_params.get(requested_family)
        if family_cfg is None and canonical_family != requested_family:
            family_cfg = strategy_params.get(canonical_family, {})
        family_cfg = family_cfg or {}
        for sname in strategy_names:
            param_cfg = family_cfg.get(sname, {})
            grids = generate_param_grid(sname, param_cfg) if param_cfg else [{}]
            for params in grids:
                yield StrategySpec(
                    family=canonical_family,
                    name=sname,
                    params=params,
                    direction=direction,
                )


def run_strategy(df: pd.DataFrame, spec: StrategySpec) -> dict:
    """Run *spec* against *df*.  Returns signal dict or None on error."""
    family_label, fn = STRATEGY_REGISTRY.get(spec.name, (None, None))
    if fn is None:
        log.warning("[strategies] Unknown strategy: %s", spec.name)
        return {}
    try:
        result = fn(df, spec.params)
        result["spec"] = spec
        return result
    except Exception as e:
        log.debug("[strategies] %s(%s) error: %s", spec.name, spec.params, e)
        return {}
