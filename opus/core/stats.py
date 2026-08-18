"""Numerical primitives for OPUS.

Everything here is a pure function over numpy arrays. No indicator logic, no
config, no I/O - so these can be unit-tested against closed-form expectations.
"""

from __future__ import annotations

import numpy as np

EPS = 1e-12


def safe_div(num, den, default: float = 0.0):
    """Elementwise divide that yields `default` wherever the denominator is
    zero or non-finite, instead of inf/nan leaking downstream."""
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)
    out = np.full(np.broadcast(num, den).shape, float(default), dtype=float)
    ok = np.isfinite(den) & (np.abs(den) > EPS) & np.isfinite(num)
    np.divide(num, den, out=out, where=ok)
    out[~np.isfinite(out)] = float(default)
    return out


def clamp(value: float, lo: float, hi: float) -> float:
    if not np.isfinite(value):
        return lo
    return float(min(max(value, lo), hi))


def squash(x: float, scale: float = 1.0) -> float:
    """Map an unbounded score into (-1, 1) with tanh.

    `scale` is the value of x that maps to ~0.76, i.e. the "this is a strong
    reading" reference point. Using tanh rather than a hard clip keeps the
    gradient alive for extreme readings instead of saturating a whole class of
    observations onto the same number.
    """
    if not np.isfinite(x) or scale <= 0:
        return 0.0
    return float(np.tanh(x / scale))


def ema(values: np.ndarray, span: int) -> np.ndarray:
    """Exponential moving average, span-parameterised (alpha = 2/(span+1)).

    Implemented as an explicit recursion because numpy has no cumulative EWMA
    and the pandas round-trip costs more than the loop at scan sizes.
    """
    values = np.asarray(values, dtype=float)
    n = values.shape[0]
    out = np.full(n, np.nan)
    if n == 0:
        return out
    alpha = 2.0 / (float(span) + 1.0)
    acc = values[0]
    out[0] = acc
    for i in range(1, n):
        v = values[i]
        if not np.isfinite(v):
            out[i] = acc
            continue
        acc = alpha * v + (1.0 - alpha) * acc
        out[i] = acc
    return out


def rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    n = values.shape[0]
    out = np.full(n, np.nan)
    if n == 0 or window <= 0:
        return out
    finite = np.isfinite(values)
    filled = np.where(finite, values, 0.0)
    csum = np.concatenate(([0.0], np.cumsum(filled)))
    ccnt = np.concatenate(([0.0], np.cumsum(finite.astype(float))))
    for i in range(n):
        lo = max(0, i - window + 1)
        cnt = ccnt[i + 1] - ccnt[lo]
        if cnt > 0:
            out[i] = (csum[i + 1] - csum[lo]) / cnt
    return out


def rolling_std(values: np.ndarray, window: int, ddof: int = 1) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    n = values.shape[0]
    out = np.full(n, np.nan)
    if n == 0 or window <= 1:
        return out
    finite = np.isfinite(values)
    filled = np.where(finite, values, 0.0)
    csum = np.concatenate(([0.0], np.cumsum(filled)))
    csq = np.concatenate(([0.0], np.cumsum(filled * filled)))
    ccnt = np.concatenate(([0.0], np.cumsum(finite.astype(float))))
    for i in range(n):
        lo = max(0, i - window + 1)
        cnt = ccnt[i + 1] - ccnt[lo]
        if cnt <= ddof:
            continue
        s = csum[i + 1] - csum[lo]
        q = csq[i + 1] - csq[lo]
        var = (q - s * s / cnt) / (cnt - ddof)
        out[i] = np.sqrt(var) if var > 0 else 0.0
    return out


def zscore_last(values: np.ndarray, window: int) -> float:
    """Z-score of the final observation against the preceding `window` bars.

    The final observation is excluded from the baseline: scoring a point
    against a mean it helped set shrinks the very outliers we care about.
    """
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.shape[0] < 3:
        return 0.0
    last = values[-1]
    base = values[-(window + 1):-1] if values.shape[0] > window else values[:-1]
    if base.shape[0] < 2:
        return 0.0
    mu = float(np.mean(base))
    sd = float(np.std(base, ddof=1))
    if sd <= EPS:
        return 0.0
    return float((last - mu) / sd)


def robust_z(values: np.ndarray, window: int) -> float:
    """Median/MAD z-score of the last observation.

    Volume and range distributions are heavy-tailed, so a mean/stdev z-score
    is dragged around by the same spikes it is meant to detect. MAD-based
    scaling (1.4826 makes it consistent with sigma under normality) keeps the
    baseline stable when a single bar is 20x normal.
    """
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.shape[0] < 3:
        return 0.0
    last = values[-1]
    base = values[-(window + 1):-1] if values.shape[0] > window else values[:-1]
    if base.shape[0] < 3:
        return 0.0
    med = float(np.median(base))
    mad = float(np.median(np.abs(base - med)))
    scale = 1.4826 * mad
    if scale <= EPS:
        sd = float(np.std(base, ddof=1))
        if sd <= EPS:
            return 0.0
        scale = sd
    return float((last - med) / scale)


def decay_weights(n: int, half_life: float) -> np.ndarray:
    """Exponential recency weights, newest last, normalised to sum 1."""
    if n <= 0:
        return np.zeros(0)
    if half_life <= 0:
        w = np.zeros(n)
        w[-1] = 1.0
        return w
    age = np.arange(n - 1, -1, -1, dtype=float)
    w = np.power(0.5, age / float(half_life))
    total = w.sum()
    return w / total if total > EPS else np.full(n, 1.0 / n)


def yang_zhang_sigma(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    window: int,
) -> float:
    """Yang-Zhang volatility over the last `window` bars, in price units.

    Yang-Zhang is drift-independent and handles the overnight/session gap by
    decomposing variance into overnight, open-to-close and Rogers-Satchell
    components. It is materially more efficient than close-to-close or simple
    range averages, which matters because every OPUS threshold is expressed in
    sigma - a noisy denominator would make every gate noisy.

    Returns an absolute price-unit sigma (fraction * last close).
    """
    o = np.asarray(open_, dtype=float)
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = c.shape[0]
    if n < 3:
        return 0.0
    w = int(min(window, n - 1))
    if w < 2:
        return 0.0

    o_w = o[-w:]
    h_w = h[-w:]
    l_w = lo[-w:]
    c_w = c[-w:]
    c_prev = c[-(w + 1):-1]

    valid = (
        np.isfinite(o_w) & np.isfinite(h_w) & np.isfinite(l_w)
        & np.isfinite(c_w) & np.isfinite(c_prev)
        & (o_w > 0) & (h_w > 0) & (l_w > 0) & (c_w > 0) & (c_prev > 0)
    )
    if int(valid.sum()) < 2:
        return 0.0
    o_w, h_w, l_w, c_w, c_prev = (
        o_w[valid], h_w[valid], l_w[valid], c_w[valid], c_prev[valid]
    )
    m = o_w.shape[0]

    log_oc = np.log(o_w / c_prev)      # overnight jump
    log_co = np.log(c_w / o_w)         # open to close
    log_ho = np.log(h_w / o_w)
    log_lo = np.log(l_w / o_w)
    log_hc = np.log(h_w / c_w)
    log_lc = np.log(l_w / c_w)

    denom = max(m - 1, 1)
    var_o = float(np.sum((log_oc - log_oc.mean()) ** 2) / denom)
    var_c = float(np.sum((log_co - log_co.mean()) ** 2) / denom)
    var_rs = float(np.mean(log_ho * log_hc + log_lo * log_lc))
    var_rs = max(var_rs, 0.0)

    k = 0.34 / (1.34 + (m + 1.0) / max(m - 1.0, 1.0))
    var = var_o + k * var_c + (1.0 - k) * var_rs
    if not np.isfinite(var) or var <= 0:
        return 0.0

    last_close = float(c[-1])
    return float(np.sqrt(var) * last_close)


def jump_ratio(close: np.ndarray, window: int) -> float:
    """Fraction of realised variance attributable to jumps, in [0, 1].

    Realised variance RV = sum r_i^2 captures all quadratic variation.
    Bipower variation BV = (pi/2) * sum |r_i||r_{i-1}| is robust to jumps
    because a jump contaminates only one factor of each product. The residual
    RV - BV therefore isolates the discontinuous part.

    High ratio = the move happened in discrete displacements (institutional
    repricing). Low ratio = a continuous grind. OPUS uses this to tell a real
    displacement leg apart from a drift of equal magnitude.
    """
    c = np.asarray(close, dtype=float)
    c = c[np.isfinite(c) & (c > 0)]
    if c.shape[0] < 5:
        return 0.0
    r = np.diff(np.log(c))
    if r.shape[0] > window:
        r = r[-window:]
    if r.shape[0] < 4:
        return 0.0
    rv = float(np.sum(r * r))
    if rv <= EPS:
        return 0.0
    abs_r = np.abs(r)
    bv = float((np.pi / 2.0) * np.sum(abs_r[1:] * abs_r[:-1]))
    # Bipower sums one fewer term than RV; rescale so the two are comparable.
    bv *= r.shape[0] / max(r.shape[0] - 1.0, 1.0)
    ratio = 1.0 - (bv / rv)
    return clamp(ratio, 0.0, 1.0)


def fractal_efficiency(close: np.ndarray, window: int) -> float:
    """Net displacement divided by total path length, in [0, 1].

    1.0 = a straight line (pure trend). 0.0 = the path returned to where it
    started after travelling a long way (pure chop). This separates a 50-pip
    move that went straight from a 50-pip move that took 400 pips of wandering
    to get there - two situations any pure momentum measure scores identically.
    """
    c = np.asarray(close, dtype=float)
    c = c[np.isfinite(c)]
    if c.shape[0] < 3:
        return 0.0
    seg = c[-(window + 1):] if c.shape[0] > window else c
    if seg.shape[0] < 3:
        return 0.0
    net = abs(float(seg[-1] - seg[0]))
    path = float(np.sum(np.abs(np.diff(seg))))
    if path <= EPS:
        return 0.0
    return clamp(net / path, 0.0, 1.0)


def swing_pivots(
    high: np.ndarray, low: np.ndarray, k: int = 2
) -> tuple[np.ndarray, np.ndarray]:
    """Indices of fractal swing highs and swing lows.

    A swing high at i requires high[i] to dominate the +/-k neighbourhood and
    to be strictly above both window EDGES. Ties inside the window are allowed
    on purpose: equal highs are not a degenerate case to be filtered out, they
    are the single richest liquidity structure on the chart - a cluster of
    stops resting at one price. A strict-uniqueness test would silently drop
    exactly the levels LDI most wants to find, and on tick-rounded instruments
    (coarse crypto ticks, 5-decimal FX) it drops a large share of all pivots.

    Adjacent plateau bars are de-duplicated to the LAST index of the run, which
    is both the most recent touch and the correct anchor for recency decay.

    `k` bars of right-hand context mean the most recent k bars can never host a
    confirmed pivot. That lag is deliberate: an unconfirmed pivot is exactly
    how a level-based system front-runs itself.
    """
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    n = h.shape[0]
    highs: list[int] = []
    lows: list[int] = []
    if n < 2 * k + 1 or k < 1:
        return np.array(highs, dtype=int), np.array(lows, dtype=int)
    for i in range(k, n - k):
        win_h = h[i - k: i + k + 1]
        win_l = lo[i - k: i + k + 1]
        if not (np.isfinite(win_h).all() and np.isfinite(win_l).all()):
            continue
        # Dominates the neighbourhood, and the neighbourhood falls away at
        # both edges (so a monotone ramp does not register a pivot).
        if h[i] >= win_h.max() and h[i] > h[i - k] and h[i] > h[i + k]:
            if highs and highs[-1] == i - 1 and h[i] >= h[i - 1]:
                highs[-1] = i          # extend plateau, keep latest touch
            else:
                highs.append(i)
        if lo[i] <= win_l.min() and lo[i] < lo[i - k] and lo[i] < lo[i + k]:
            if lows and lows[-1] == i - 1 and lo[i] <= lo[i - 1]:
                lows[-1] = i
            else:
                lows.append(i)
    return np.array(highs, dtype=int), np.array(lows, dtype=int)


def logistic(x: float) -> float:
    """Numerically stable logistic."""
    if not np.isfinite(x):
        return 0.5
    if x >= 0:
        z = np.exp(-x)
        return float(1.0 / (1.0 + z))
    z = np.exp(x)
    return float(z / (1.0 + z))


def weighted_dispersion(values: np.ndarray, weights: np.ndarray) -> float:
    """Weighted standard deviation, used as the disagreement measure."""
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
    if int(ok.sum()) < 2:
        return 0.0
    v, w = v[ok], w[ok]
    w = w / w.sum()
    mu = float(np.sum(w * v))
    var = float(np.sum(w * (v - mu) ** 2))
    return float(np.sqrt(max(var, 0.0)))


__all__ = [
    "EPS", "safe_div", "clamp", "squash", "ema", "rolling_mean", "rolling_std",
    "zscore_last", "robust_z", "decay_weights", "yang_zhang_sigma",
    "jump_ratio", "fractal_efficiency", "swing_pivots", "logistic",
    "weighted_dispersion",
]
