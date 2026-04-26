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
    di_plus = 100 * _ema(dm_plus, n) / tr.replace(0, np.nan)
    di_minus = 100 * _ema(dm_minus, n) / tr.replace(0, np.nan)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def _bb(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    return mid + k * sd, mid, mid - k * sd


def _vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].replace(0, np.nan)
    cum_tp_v = (typical * vol).cumsum()
    cum_v = vol.cumsum()
    return cum_tp_v / cum_v


def _pct_rank(s: pd.Series, n: int = 100) -> pd.Series:
    """Rolling percentile rank of *s* over last *n* bars (0–100)."""
    def _rank(x):
        return (x < x[-1]).sum() / len(x) * 100
    return s.rolling(n, min_periods=n // 2).apply(_rank, raw=True)


def _crossed_above(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def _crossed_below(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


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
    """EMA stack alignment continuation (short/mid/long all in order)."""
    close = df["close"]
    s = int(params.get("ema_short", 20))
    m = int(params.get("ema_mid", 50))
    l = int(params.get("ema_long", 200))
    adx_min = float(params.get("adx_min", 0))

    es, em, el = _ema(close, s), _ema(close, m), _ema(close, l)

    bullish = (es > em) & (em > el) & (~((es > em) & (em > el)).shift(1).fillna(False))
    bearish = (es < em) & (em < el) & (~((es < em) & (em < el)).shift(1).fillna(False))

    long_exits = _crossed_below(es, em)
    short_exits = _crossed_above(es, em)

    if adx_min > 0:
        adx = _adx(df["high"], df["low"], close)
        adx_ok = adx >= adx_min
        bullish &= adx_ok
        bearish &= adx_ok

    return _signals(bullish, long_exits, bearish, short_exits,
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

    # Resample to daily to get prior-day high/low, then forward-fill
    try:
        daily = df["close"].resample("1D").ohlc()
        prev_high = daily["high"].shift(1).reindex(df.index).ffill()
        prev_low = daily["low"].shift(1).reindex(df.index).ffill()
    except Exception:
        # If resampling fails (e.g. no datetime index), use rolling 24-bar proxy
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

    # Asian range: bars before utc_start in same session day (proxy: rolling 8 bars)
    asian_high = df["high"].rolling(8).max().shift(1)
    asian_low = df["low"].rolling(8).min().shift(1)

    in_window = pd.Series(False, index=df.index)
    if hasattr(df.index, "hour"):
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

    pre_session_high = df["high"].rolling(8).max().shift(1)
    pre_session_low = df["low"].rolling(8).min().shift(1)

    in_window = pd.Series(False, index=df.index)
    if hasattr(df.index, "hour"):
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


# ── G. Engine D Scalp Proxy ───────────────────────────────────────────────────

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
    long_exits = close < vwap - band
    short_entries = _crossed_below(close, vwap - band)
    short_exits = close > vwap + band

    return _signals(long_entries, long_exits, short_entries, short_exits,
                    meta={"family": "engine_d_proxy", "sub": "vwap_reclaim"})


def strategy_micro_breakout(df: pd.DataFrame, params: dict) -> dict:
    close = df["close"]
    range_bars = int(params.get("range_bars", 6))
    atr_sl = float(params.get("atr_sl_mult", 1.0))

    recent_high = df["high"].rolling(range_bars).max().shift(1)
    recent_low = df["low"].rolling(range_bars).min().shift(1)

    long_entries = _crossed_above(close, recent_high)
    long_exits = close < recent_low
    short_entries = _crossed_below(close, recent_low)
    short_exits = close > recent_high

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
}

FAMILY_STRATEGIES: dict[str, list[str]] = {
    "trend_momentum": ["ema_cross", "ema_alignment", "macd_direction"],
    "pullback":       ["pullback_ema"],
    "breakout":       ["prev_day_hl", "session_opening_range", "london_breakout", "ny_breakout"],
    "mean_reversion": ["rsi_extreme", "bollinger_touch", "vwap_deviation"],
    "volatility":     ["bb_squeeze_breakout", "atr_compression"],
    "engine_b_proxy": ["ob_bos", "structure_filters"],
    "engine_d_proxy": ["vwap_reclaim", "micro_breakout", "ema_scalp_pullback"],
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
    for family in families:
        strategy_names = FAMILY_STRATEGIES.get(family, [])
        for sname in strategy_names:
            family_cfg = strategy_params.get(family, {})
            param_cfg = family_cfg.get(sname, {})
            grids = generate_param_grid(sname, param_cfg) if param_cfg else [{}]
            for params in grids:
                yield StrategySpec(
                    family=family,
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
