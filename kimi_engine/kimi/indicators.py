"""KIMI proprietary indicator suite.

All indicators are original implementations — no TA-Lib, no borrowed formulas
beyond primitive building blocks (EMA/RSI/ATR are industry primitives; the
composite constructs below are KIMI's own):

  tide        — multi-ribbon trend alignment, slope- and position-aware (-1..1)
  pulse       — momentum composite 0..100 (50 = neutral)
  pressure    — signed relative volume x close-location (-1..1)
  flow        — taker-buy imbalance drift (-1..1)
  vol_regime  — ATR percentile vs its own history (0..1)
  vwap        — session-anchored (UTC day) VWAP with 1/2-sigma bands
"""
from __future__ import annotations

import numpy as np


# ----------------------------------------------------------------------
# primitives
# ----------------------------------------------------------------------
def ema(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) == 0:
        return x
    alpha = 2.0 / (n + 1.0)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    au = ema(up, n)
    ad = ema(dn, n)
    rs = np.divide(au, ad, out=np.full_like(au, 1e9), where=ad > 0)
    return 100.0 - 100.0 / (1.0 + rs)


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return ema(tr, n)


def rolling_percentile(x: np.ndarray, window: int) -> np.ndarray:
    """Percentile rank of each point within the trailing window (0..1)."""
    out = np.full(len(x), 0.5)
    for i in range(len(x)):
        lo = max(0, i - window + 1)
        w = x[lo:i + 1]
        out[i] = float((w <= x[i]).mean())
    return out


# ----------------------------------------------------------------------
# KIMI constructs
# ----------------------------------------------------------------------
def tide(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    """KIMI Tide — trend direction & strength in [-1, +1].

    Blend of:
      * EMA ribbon (9/21/50) ordering  — structure of the ribbon itself
      * ribbon slope over 5 bars       — is it bending?
      * close position inside 20-bar Donchian channel — where price sits
    """
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    a = atr(h, l, c, 14)
    a_safe = np.where(a > 0, a, 1e-12)

    order = np.clip((e9 - e21) / a_safe, -1, 1) * 0.5 + \
        np.clip((e21 - e50) / a_safe, -1, 1) * 0.5

    slope = np.zeros_like(c)
    if len(c) > 5:
        slope[5:] = (e21[5:] - e21[:-5]) / a_safe[5:]
    slope = np.clip(slope, -1, 1)

    hh = np.maximum.accumulate(np.maximum(h, 0))  # placeholder replaced below
    n = 20
    don = np.zeros_like(c)
    for i in range(len(c)):
        lo_i = max(0, i - n + 1)
        hh_i = h[lo_i:i + 1].max(); ll_i = l[lo_i:i + 1].min()
        rng = hh_i - ll_i
        don[i] = ((c[i] - ll_i) / rng * 2 - 1) if rng > 0 else 0.0

    t = 0.45 * order + 0.25 * slope + 0.30 * don
    return np.clip(t, -1, 1)


def pulse(c: np.ndarray, h: np.ndarray, l: np.ndarray) -> np.ndarray:
    """KIMI Pulse — momentum composite, 0..100, 50 neutral.

    RSI deviation + MACD-histogram slope + short ROC, volatility-normalized.
    """
    r = rsi(c, 14)
    macd_line = ema(c, 12) - ema(c, 26)
    sig = ema(macd_line, 9)
    hist = macd_line - sig
    a = atr(h, l, c, 14)
    a_safe = np.where(a > 0, a, 1e-12)
    hs = np.zeros_like(c)
    if len(c) > 3:
        hs[3:] = (hist[3:] - hist[:-3]) / a_safe[3:]
    roc = np.zeros_like(c)
    if len(c) > 8:
        roc[8:] = (c[8:] - c[:-8]) / a_safe[8:]
    comp = (r - 50.0) / 50.0 * 0.5 + np.clip(hs, -1, 1) * 0.25 + np.clip(roc, -2, 2) / 2 * 0.25
    return np.clip(50.0 + comp * 50.0, 0, 100)


def pressure(v: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    """KIMI Pressure — signed relative volume weighted by close location.

    relvol = volume / rolling median(20); clv in [-1,1]; product clipped.
    """
    n = 20
    med = np.zeros_like(v)
    for i in range(len(v)):
        lo_i = max(0, i - n + 1)
        med[i] = np.median(v[lo_i:i + 1])
    relvol = np.divide(v, med, out=np.ones_like(v), where=med > 0)
    rng = h - l
    clv = np.divide((c - l) - (h - c), rng, out=np.zeros_like(c), where=rng > 0)
    return np.clip((relvol - 1.0) * clv, -1, 1)


def flow(taker: np.ndarray, v: np.ndarray) -> np.ndarray:
    """KIMI Flow — taker-buy imbalance, EMA-smoothed, centered at 0.

    ratio = takerBuy / volume in [0,1]; drift vs its own 34-bar EMA.
    """
    ratio = np.divide(taker, v, out=np.full_like(v, 0.5), where=v > 0)
    base = ema(ratio, 34)
    return np.clip((ratio - base) * 10.0, -1, 1)


def vol_regime(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    """KIMI Vol — ATR(14) percentile vs trailing 200 bars (0..1)."""
    return rolling_percentile(atr(h, l, c, 14), 200)


def vwap_session(t_ms: np.ndarray, h: np.ndarray, l: np.ndarray,
                 c: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Session-anchored (UTC day) VWAP with 1σ / 2σ bands."""
    tp = (h + l + c) / 3.0
    day = (t_ms // 86_400_000)
    out = np.zeros_like(c)
    s1 = np.zeros_like(c)
    s2 = np.zeros_like(c)
    i = 0
    while i < len(c):
        j = i
        pv = vv = pv2 = 0.0
        while j < len(c) and day[j] == day[i]:
            pv += tp[j] * v[j]
            pv2 += tp[j] * tp[j] * v[j]
            vv += v[j]
            if vv > 0:
                m = pv / vv
                var = max(pv2 / vv - m * m, 0.0)
                out[j] = m
                s1[j] = var ** 0.5
                s2[j] = 2 * var ** 0.5
            else:
                out[j] = tp[j]
            j += 1
        i = j
    return out, s1, s2
