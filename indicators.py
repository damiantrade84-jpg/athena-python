"""indicators.py — Technical indicator calculations for Athena Pro.



Pure math functions (no I/O) plus calc_levels which reads CONFIG for

per-asset-class ATR multipliers. Safe to import and unit-test in isolation.

"""

import math

import logging
import numpy as np

from config import CONFIG

from feature_normalizer import (
    zscore_normalize,
    percentile_rank,
    get_normalization_lookback,
)


log = logging.getLogger("athena")


def calc_ema(c: list, p: int) -> list:
    """Exponential Moving Average. Returns list aligned with input, None-padded."""

    n = len(c)
    e = [None] * n
    if n < p:
        return e

    arr = np.asarray(c, dtype=np.float64)
    k = 2.0 / (p + 1)
    ema_val = float(np.mean(arr[:p]))
    e[p - 1] = ema_val
    for i in range(p, n):
        ema_val = arr[i] * k + ema_val * (1.0 - k)
        e[i] = float(ema_val)

    return e


def calc_sma(a: list, p: int) -> list:
    """Simple Moving Average. Returns list aligned with input, None-padded.
    Skips None values in the window (computes mean of available values).
    """
    r = [None] * len(a)

    for i in range(p - 1, len(a)):
        window = a[i - p + 1 : i + 1]
        valid = [v for v in window if v is not None]
        if valid:
            r[i] = sum(valid) / len(valid)

    return r


def calc_rsi(c: list, p: int) -> list:
    """Wilder RSI (smoothed). Returns list aligned with input, None-padded."""

    n = len(c)
    r = [None] * n
    if n < p + 1:
        return r

    arr = np.asarray(c, dtype=np.float64)
    delta = np.diff(arr)
    ag = float(np.mean(np.maximum(delta[:p], 0.0)))
    al = float(np.mean(np.maximum(-delta[:p], 0.0)))
    r[p] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)

    for i in range(p + 1, n):
        d = float(delta[i - 1])
        ag = (ag * (p - 1) + max(d, 0.0)) / p
        al = (al * (p - 1) + max(-d, 0.0)) / p
        r[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)

    return r


def calc_macd(c: list, f: int = 12, s: int = 26, sig: int = 9) -> dict:

    ef, es = calc_ema(c, f), calc_ema(c, s)

    ml = [
        ef[i] - es[i] if ef[i] is not None and es[i] is not None else None
        for i in range(len(c))
    ]

    valid = [v for v in ml if v is not None]

    se = calc_ema(valid, sig)

    sl2 = [None] * len(c)

    vf = next((i for i, v in enumerate(ml) if v is not None), len(c))

    si = 0

    for i in range(vf, len(c)):
        sl2[i] = se[si] if si < len(se) else None

        si += 1

    hist = [
        ml[i] - sl2[i] if ml[i] is not None and sl2[i] is not None else None
        for i in range(len(c))
    ]

    return {"macd": ml, "signal": sl2, "hist": hist}


def calc_atr(h: list, lo: list, c: list, p: int) -> list:

    tr = [0] + [
        max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
        for i in range(1, len(c))
    ]

    a = [None] * len(c)

    if len(tr) <= p:
        return a

    a[p] = sum(tr[1 : p + 1]) / p

    for i in range(p + 1, len(tr)):
        a[i] = (a[i - 1] * (p - 1) + tr[i]) / p

    return a


def calc_adx(hi: list, lo: list, c: list, p: int) -> dict:
    """Wilder ADX with +DI/-DI. Returns dict with aligned arrays, None-padded.

    Alignment: all arrays are indexed to the *close* bar they correspond to.
    - plusDI[i] / minusDI[i] valid from i = p+1 onward (after p-bar smoothing)
    - adx[i] valid from i = 2*p+1 onward (after p-bar DX smoothing)
    """

    n = len(c)

    adx = [None] * n
    plus_di = [None] * n
    minus_di = [None] * n

    if n < p * 2 + 1:
        return {"adx": adx, "plusDI": plus_di, "minusDI": minus_di}

    true_range, dm_plus, dm_minus = [], [], []

    for i in range(1, n):
        up_move = hi[i] - hi[i - 1]
        down_move = lo[i - 1] - lo[i]

        dm_plus.append(up_move if up_move > down_move and up_move > 0 else 0)
        dm_minus.append(down_move if down_move > up_move and down_move > 0 else 0)

        true_range.append(
            max(hi[i] - lo[i], abs(hi[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
        )

    # Wilder smoothing init: first p bars (indices 0..p-1 of true_range)
    smooth_tr = sum(true_range[:p])
    smooth_dp = sum(dm_plus[:p])
    smooth_dm = sum(dm_minus[:p])

    dx_values = []

    # i indexes into true_range (which starts at price bar 1).
    # After smoothing, DI values correspond to price bar i+1.
    for i in range(p, len(true_range)):
        smooth_tr = smooth_tr - smooth_tr / p + true_range[i]
        smooth_dp = smooth_dp - smooth_dp / p + dm_plus[i]
        smooth_dm = smooth_dm - smooth_dm / p + dm_minus[i]

        pdi_val = (smooth_dp / smooth_tr) * 100 if smooth_tr else 0
        mdi_val = (smooth_dm / smooth_tr) * 100 if smooth_tr else 0

        di_sum = pdi_val + mdi_val

        # Align DI to the current price bar (i+1 because true_range[0] = bar 1)
        plus_di[i + 1] = pdi_val
        minus_di[i + 1] = mdi_val

        dx_values.append(abs(pdi_val - mdi_val) / di_sum * 100 if di_sum else 0)

    # ADX: Wilder-smoothed DX. First ADX = mean of first p DX values.
    # DX value j corresponds to price bar (p + 1 + j) because:
    #   - first DX computed at i=p → price bar p+1
    #   - so dx_values[0] → bar p+1, dx_values[p-1] → bar 2p
    # First smoothed ADX should be at bar 2p+1 (after p DX values smoothed).
    if len(dx_values) >= p:
        adx_avg = sum(dx_values[:p]) / p

        # Place first smoothed ADX at bar 2p+1 (index 2*p + 1)
        first_adx_idx = 2 * p + 1
        if first_adx_idx < n:
            adx[first_adx_idx] = adx_avg

        # Continue smoothing: each subsequent DX advances by one bar
        for i in range(p, len(dx_values)):
            adx_avg = (adx_avg * (p - 1) + dx_values[i]) / p
            idx = first_adx_idx + (i - p + 1)
            if idx < n:
                adx[idx] = adx_avg

    return {"adx": adx, "plusDI": plus_di, "minusDI": minus_di}


def calc_bb(c: list, p: int, m: float) -> dict:

    u, mid, lo = [], [], []

    for i in range(len(c)):
        if i < p - 1:
            u.append(None)
            mid.append(None)
            lo.append(None)

            continue

        sl = c[i - p + 1 : i + 1]

        mn = sum(sl) / p

        sd = math.sqrt(sum((x - mn) ** 2 for x in sl) / p) if p > 0 else 0

        mid.append(mn)
        u.append(mn + m * sd)
        lo.append(mn - m * sd)

    return {"upper": u, "mid": mid, "lower": lo}


def calc_squeeze(
    candles: list,
    bb_period: int = 20,
    bb_mult: float = 2.0,
    kc_period: int = 20,
    kc_mult: float = 1.5,
) -> dict | None:
    """Detect Bollinger Band squeeze (BB inside Keltner Channels).

    Returns dict with 'active' (bool), 'bars' (consecutive squeeze bars), 'momentum' direction."""

    if len(candles) < max(bb_period, kc_period) + 5:
        return None

    cl = [c["close"] for c in candles]

    hi = [c["high"] for c in candles]

    lo = [c["low"] for c in candles]

    n = len(cl)

    # Keltner Channel: EMA(kc_period) ± kc_mult * ATR(kc_period)

    ema = calc_ema(cl, kc_period)

    atr = calc_atr(hi, lo, cl, kc_period)

    # Check last bar

    i = n - 1

    if ema[i] is None or atr[i] is None:
        return None

    kc_upper = ema[i] + kc_mult * atr[i]

    kc_lower = ema[i] - kc_mult * atr[i]

    # BB at last bar

    sl = cl[i - bb_period + 1 : i + 1]

    mn = sum(sl) / bb_period

    sd = (
        math.sqrt(sum((x - mn) ** 2 for x in sl) / bb_period)
        if bb_period > 0
        else 0
    )

    bb_upper = mn + bb_mult * sd

    bb_lower = mn - bb_mult * sd

    squeeze_now = bb_upper < kc_upper and bb_lower > kc_lower

    # Count consecutive squeeze bars (including current bar)
    bars = 1 if squeeze_now else 0
    for j in range(i - 1, max(bb_period, kc_period) - 1, -1):
        if ema[j] is None or atr[j] is None:
            break

        _kcu = ema[j] + kc_mult * atr[j]
        _kcl = ema[j] - kc_mult * atr[j]

        _sl = cl[j - bb_period + 1 : j + 1]
        _mn = sum(_sl) / bb_period
        _sd = (
            math.sqrt(sum((x - _mn) ** 2 for x in _sl) / bb_period)
            if bb_period > 0
            else 0
        )

        _bbu = _mn + bb_mult * _sd
        _bbl = _mn - bb_mult * _sd

        if _bbu < _kcu and _bbl > _kcl:
            bars += 1
        else:
            break

    # Momentum direction: close relative to midline
    mom_dir = "bullish" if cl[-1] > mn else "bearish"

    return {"active": squeeze_now, "bars": bars, "momentum": mom_dir}


def calc_obv_trend(candles: list, lookback: int = 20) -> str | None:
    """On-Balance Volume trend vs price trend.

    Returns: 'confirming', 'diverging_bearish', 'diverging_bullish', or None."""

    if len(candles) < lookback + 5:
        return None

    window = candles[-lookback:]

    cl = [c["close"] for c in window]

    vols = [c.get("vol", 0) for c in window]
    nonzero_vol_bars = sum(1 for v in vols if float(v or 0) > 0)
    if sum(float(v or 0) for v in vols) <= 0 or nonzero_vol_bars < 2:
        return None

    # Build OBV

    obv = [0.0]

    for i in range(1, len(cl)):
        if cl[i] > cl[i - 1]:
            obv.append(obv[-1] + vols[i])

        elif cl[i] < cl[i - 1]:
            obv.append(obv[-1] - vols[i])

        else:
            obv.append(obv[-1])

    # Compare slopes: price vs OBV over last N bars

    half = lookback // 2

    price_slope = cl[-1] - cl[half] if len(cl) > half else 0

    obv_slope = obv[-1] - obv[half] if len(obv) > half else 0

    if price_slope > 0 and obv_slope > 0:
        return "confirming"

    if price_slope < 0 and obv_slope < 0:
        return "confirming"

    if price_slope > 0 and obv_slope < 0:
        return "diverging_bearish"

    if price_slope < 0 and obv_slope > 0:
        return "diverging_bullish"

    return None


def calc_rsi_divergence(candles: list, lookback: int = 30) -> str | None:
    """Wilder: RSI divergence using proper 3-bar pivot detection.

    Returns: 'bullish', 'bearish', or None"""

    try:
        c = candles[-lookback:]

        cl = [x["close"] for x in c]

        hi = [x["high"] for x in c]

        lo = [x["low"] for x in c]

        rsi = calc_rsi(cl, 14)

        n = len(cl)

        if n < 15 or rsi[-1] is None:
            return None

        phigh_idx = [
            i
            for i in range(1, n - 1)
            if hi[i] > hi[i - 1] and hi[i] > hi[i + 1] and rsi[i] is not None
        ]

        plow_idx = [
            i
            for i in range(1, n - 1)
            if lo[i] < lo[i - 1] and lo[i] < lo[i + 1] and rsi[i] is not None
        ]

        if len(phigh_idx) >= 2:
            i1, i2 = phigh_idx[-2], phigh_idx[-1]

            if hi[i2] > hi[i1] * 1.001 and rsi[i2] < rsi[i1] * 0.99:
                return "bearish"

        if len(plow_idx) >= 2:
            i1, i2 = plow_idx[-2], plow_idx[-1]

            if lo[i2] < lo[i1] * 0.999 and rsi[i2] > rsi[i1] * 1.01:
                return "bullish"

    except Exception as e:
        log.warning(f"calc_rsi_divergence: {e}")

    return None


def calc_weinstein_stage(candles: list, lookback: int = 150) -> tuple:
    """Stan Weinstein stage analysis using configurable MA lookback.

    Stage 1=Basing, Stage 2=Advancing, Stage 3=Topping, Stage 4=Declining"""

    try:
        cl = [c["close"] for c in candles]

        if len(cl) < lookback:
            return None, None

        ma30w = calc_sma(cl, lookback)

        L = len(cl) - 1

        if ma30w[L] is None or ma30w[L - 5] is None:
            return None, None

        price = cl[L]
        ma = ma30w[L]
        ma_prev = ma30w[L - 5]

        ma_rising = ma > ma_prev

        if price > ma and ma_rising:
            return 2, "Stage 2 — Advancing"

        if price > ma and not ma_rising:
            return 3, "Stage 3 — Topping"

        if price < ma and not ma_rising:
            return 4, "Stage 4 — Declining"

        if price < ma and ma_rising:
            return 1, "Stage 1 — Basing"

    except Exception as e:
        log.warning(f"calc_weinstein_stage: {e}")

    return None, None


def calc_fib_proximity(price: float, fib: dict) -> int:
    """Check if price is near a key Fibonacci level (within 1.5% range band).

    Returns +1 (support/bullish), -1 (resistance/bearish), or 0 (not near)."""

    try:
        levels = [fib["fib236"], fib["fib382"], fib["fib500"], fib["fib618"], fib["fib786"]]

        rng = fib["highest"] - fib["lowest"]

        if rng <= 0:
            return 0

        band = rng * 0.015

        for lvl in levels:
            if abs(price - lvl) <= band:
                return 1 if price <= lvl else -1

    except Exception as e:
        log.warning(f"calc_fib_proximity: {e}")

    return 0


def calc_stochastic(candles: list, kp: int, ks: int, ds: int) -> dict:

    if not candles or len(candles) < kp:
        return {"k": [None] * len(candles), "d": [None] * len(candles)}

    hi = [c["high"] for c in candles]
    lo = [c["low"] for c in candles]
    cl = [c["close"] for c in candles]

    n = len(cl)
    rawK = [None] * n

    for i in range(kp - 1, n):
        window_hi = hi[i - kp + 1 : i + 1]
        window_lo = lo[i - kp + 1 : i + 1]
        
        if not window_hi or not window_lo:
            rawK[i] = 50
            continue

        _valid_hi = [v for v in window_hi if v is not None]
        _valid_lo = [v for v in window_lo if v is not None]
        if not _valid_hi or not _valid_lo:
            rawK[i] = 50
            continue

        hh = max(_valid_hi)
        ll = min(_valid_lo)

        rawK[i] = ((cl[i] - ll) / (hh - ll)) * 100 if hh != ll else 50

    # SMA of %K to produce %K-smoothed (often called %K slow).
    # Preserve None padding: do NOT replace None with 0 before SMA.
    # Instead, slice from first valid %K and re-align after SMA.
    valid_start = kp - 1
    rawK_valid = rawK[valid_start:]
    kL_sliced = calc_sma(rawK_valid, ks)
    kL = [None] * valid_start + kL_sliced

    # SMA of smoothed %K to produce %D.
    # First valid %D is after ks-1 more bars.
    d_valid_start = valid_start + ks - 1
    kL_valid = kL[d_valid_start:]
    dL_sliced = calc_sma(kL_valid, ds)
    dL = [None] * d_valid_start + dL_sliced

    return {"k": kL, "d": dL}


def calc_stochastic_rsi(candles: list, rsi_period: int = 14, stoch_period: int = 14,
                       k_smooth: int = 3, d_smooth: int = 3) -> dict:
    """Stochastic RSI: applies Stochastic formula to RSI values.

    Returns dict with "k" (fast %K) and "d" (fast %D) arrays.
    Both are bounded 0-100. Values >80 indicate overbought, <20 oversold.

    Args:
        candles: list of dicts with "close" key
        rsi_period: period for RSI calculation (default 14)
        stoch_period: period for Stochastic of RSI (default 14)
        k_smooth: smoothing period for %K (default 3)
        d_smooth: smoothing period for %D (default 3)
    """
    if not candles or len(candles) < rsi_period + stoch_period:
        return {"k": [None] * len(candles), "d": [None] * len(candles)}

    closes = [float(c["close"]) for c in candles]
    rsi_values = calc_rsi(closes, rsi_period)

    # Now apply Stochastic formula to RSI values
    n = len(rsi_values)
    rawK = [None] * n

    for i in range(stoch_period - 1, n):
        rsi_window = rsi_values[i - stoch_period + 1:i + 1]
        valid_rsi = [v for v in rsi_window if v is not None]

        if len(valid_rsi) < stoch_period:
            continue

        hh = max(valid_rsi)
        ll = min(valid_rsi)

        if hh == ll:
            rawK[i] = 50
        else:
            rawK[i] = ((rsi_values[i] - ll) / (hh - ll)) * 100

    kL = [None] * n
    for i in range(n):
        window = rawK[i - k_smooth + 1 : i + 1]
        if i >= k_smooth - 1 and len(window) == k_smooth and all(v is not None for v in window):
            kL[i] = sum(window) / k_smooth

    dL = [None] * n
    for i in range(n):
        window = kL[i - d_smooth + 1 : i + 1]
        if i >= d_smooth - 1 and len(window) == d_smooth and all(v is not None for v in window):
            dL[i] = sum(window) / d_smooth

    return {"k": kL, "d": dL}


def calc_aroon(candles: list, period: int = 14) -> dict:
    """Aroon Up/Down and Oscillator. TA-Lib standard, period=14."""

    if not candles or len(candles) < period + 1:
        return {"aroonUp": None, "aroonDown": None, "aroonOsc": None}

    hi = [c["high"] for c in candles[-(period + 1) :] if c.get("high") is not None]
    lo = [c["low"] for c in candles[-(period + 1) :] if c.get("low") is not None]

    if len(hi) <= 0 or len(lo) <= 0:
        return {"aroonUp": None, "aroonDown": None, "aroonOsc": None}

    # Most recent occurrence of highest high / lowest low
    max_val = max(hi)
    max_idx = len(hi) - 1 - hi[::-1].index(max_val)
    min_val = min(lo)
    min_idx = len(lo) - 1 - lo[::-1].index(min_val)

    bars_since_high = (len(hi) - 1) - max_idx
    bars_since_low = (len(lo) - 1) - min_idx

    aroon_up = ((period - bars_since_high) / period) * 100
    aroon_down = ((period - bars_since_low) / period) * 100
    aroon_osc = aroon_up - aroon_down

    return {
        "aroonUp": round(aroon_up, 1),
        "aroonDown": round(aroon_down, 1),
        "aroonOsc": round(aroon_osc, 1),
    }


def calc_realized_volatility(
    candles: list, lookback: int = 30, asset_type: str = "crypto"
) -> dict:
    """Realized volatility via log returns, annualized per asset class."""

    if not candles or len(candles) < lookback + 1:
        return {"realized_vol": None, "realized_vol_z": None, "realized_vol_pct": None}

    closes = [float(c["close"]) for c in candles if c.get("close") is not None]
    if len(closes) < lookback + 1:
        return {"realized_vol": None, "realized_vol_z": None, "realized_vol_pct": None}

    annual_factor = CONFIG.get("REALIZED_VOL_ANNUALIZATION", {}).get(asset_type, 252)

    # Build rolling realized vol series (one value per bar from lookback onward)

    vol_series = []

    for i in range(lookback, len(closes)):
        window_closes = closes[i - lookback : i + 1]

        log_rets = [
            math.log(window_closes[j] / window_closes[j - 1])
            for j in range(1, len(window_closes))
        ]

        rv = math.sqrt(sum(r * r for r in log_rets) * annual_factor)

        vol_series.append(rv)

    raw = vol_series[-1] if vol_series else None

    if raw is None:
        return {"realized_vol": None, "realized_vol_z": None, "realized_vol_pct": None}

    # Normalize the rolling series

    norm_window = get_normalization_lookback(asset_type)

    zs = zscore_normalize(vol_series, min(norm_window, len(vol_series)))

    pct = percentile_rank(vol_series, min(norm_window, len(vol_series)))

    return {"realized_vol": raw, "realized_vol_z": zs[-1], "realized_vol_pct": pct[-1]}


def calc_adx_momentum(adx_series: list, window: int = 5) -> tuple:
    """Detect ADX momentum — is the trend strengthening or exhausting?"""

    if not adx_series:
        return 0.0, "stable"

    valid = [v for v in adx_series if v is not None]

    if len(valid) < window + 2:
        return 0.0, "stable"

    recent = valid[-(window + 1) :]

    slope = (recent[-1] - recent[0]) / window

    peak = max(valid[-20:]) if len(valid) >= 20 else max(valid)

    cur = valid[-1]

    if slope > 0.5:
        return round(slope, 2), "strengthening"

    elif peak > 30 and cur < peak * 0.8 and slope < -0.3:
        return round(slope, 2), "collapsing"

    elif peak > 25 and slope < -0.3:
        return round(slope, 2), "exhausting"

    return round(slope, 2), "stable"


def calc_adx_percentile(adx_series: list, lookback: int = 252) -> tuple:
    """Rank current ADX vs its own history. Returns (percentile 0-100, label)."""

    valid = [v for v in adx_series if v is not None]

    if len(valid) < 20:
        return None, "insufficient data"

    cur = valid[-1]

    window = valid[-lookback:]

    pct = round(sum(1 for v in window if v <= cur) / len(window) * 100, 1)

    label = (
        "expanding" if (len(valid) >= 3 and valid[-1] > valid[-2]) else "contracting"
    )

    return pct, label


def calc_atr_percentile(atr_series: list, lookback: int = 100) -> tuple:
    """Rank current ATR vs its own history. Returns (percentile 0-100, label)."""

    valid = [v for v in atr_series if v is not None]

    if len(valid) < 20:
        return None, "insufficient data"

    cur = valid[-1]

    window = valid[-lookback:]

    pct = round(sum(1 for v in window if v <= cur) / len(window) * 100, 1)

    label = (
        "expanding" if (len(valid) >= 3 and valid[-1] > valid[-2]) else "contracting"
    )

    return pct, label


def calc_bb_width_percentile(
    candles: list, bb_period: int = 20, bb_mult: float = 2.0, lookback: int = 100
) -> tuple:
    """Rank current Bollinger Band width vs its own history.

    Returns (percentile 0-100, label: 'compressed'|'expanded'|'normal')."""

    cl = [c["close"] for c in candles]

    if len(cl) < bb_period + lookback:
        return None, "insufficient data"

    widths = []

    for i in range(bb_period - 1, len(cl)):
        sl = cl[i - bb_period + 1 : i + 1]

        mn = sum(sl) / bb_period

        sd = (
            math.sqrt(sum((x - mn) ** 2 for x in sl) / bb_period)
            if bb_period > 0
            else 0
        )

        w = (2 * bb_mult * sd) / mn if mn > 0 else 0  # normalised width

        widths.append(w)

    if len(widths) < 20:
        return None, "insufficient data"

    cur = widths[-1]

    window = widths[-lookback:]

    pct = round(sum(1 for v in window if v <= cur) / len(window) * 100, 1)

    label = "compressed" if pct <= 25 else ("expanded" if pct >= 75 else "normal")

    return pct, label


def chandelier_exit(
    highs: list,
    lows: list,
    closes: list,
    atr_period: int = 14,
    lookback: int = 22,
    mult: float = 3.0,
) -> dict:
    """Chandelier Exit — ATR-based trailing stop for trend following.

    Returns dict with:
      long_stop:  list aligned with input — trailing stop for long positions
                  (highest_high(lookback) - mult * ATR)
      short_stop: list aligned with input — trailing stop for short positions
                  (lowest_low(lookback) + mult * ATR)
      direction:  list of 1 (bullish) / -1 (bearish) based on which stop is active

    The stops ratchet: long_stop only rises, short_stop only falls.
    Direction flips when price crosses the opposite stop.
    """
    n = len(closes)
    atr_series = calc_atr(highs, lows, closes, atr_period)

    long_stop = [None] * n
    short_stop = [None] * n
    direction_out = [None] * n

    start = max(lookback, atr_period + 1)
    if n <= start:
        return {"long_stop": long_stop, "short_stop": short_stop, "direction": direction_out}

    for i in range(start, n):
        atr_val = atr_series[i]
        if atr_val is None:
            long_stop[i] = long_stop[i - 1]
            short_stop[i] = short_stop[i - 1]
            direction_out[i] = direction_out[i - 1]
            continue

        hh = max(highs[i - lookback + 1 : i + 1])
        ll = min(lows[i - lookback + 1 : i + 1])

        raw_long = hh - mult * atr_val
        raw_short = ll + mult * atr_val

        prev_long = long_stop[i - 1]
        prev_short = short_stop[i - 1]

        if prev_long is not None and closes[i - 1] > prev_long:
            raw_long = max(raw_long, prev_long)
        if prev_short is not None and closes[i - 1] < prev_short:
            raw_short = min(raw_short, prev_short)

        long_stop[i] = raw_long
        short_stop[i] = raw_short

        prev_dir = direction_out[i - 1]
        if prev_dir is None:
            direction_out[i] = 1 if closes[i] > raw_long else -1
        elif closes[i] > prev_short if prev_dir == -1 else False:
            direction_out[i] = 1
        elif closes[i] < prev_long if prev_dir == 1 else False:
            direction_out[i] = -1
        else:
            direction_out[i] = prev_dir

    return {"long_stop": long_stop, "short_stop": short_stop, "direction": direction_out}


def calc_levels(
    price: float,
    atr: float,
    direction: str,
    pair_type: str,
    regime_state: int | None = None,
    style: str = "swing",
) -> dict:
    """Shared SL/TP calculation — used by both analyze_pair and backtest_pair.



    regime_state: 0=TRENDING (wider stops), 1=RANGING (default), 2=HIGH_VOLATILITY (wider stops), 3=LOW_VOLATILITY (tighter stops).

    """

    # Style-aware ATR multiplier lookup
    _style = (style or "swing").lower()
    _style_mults = CONFIG.get("STYLE_ATR_MULTS", {}).get(_style, {})
    _style_class = _style_mults.get(pair_type)

    if _style_class:
        m = _style_class
    else:
        m = CONFIG["ATR_CLASS"].get(
            pair_type,
            {
                "sl": CONFIG["SL_ATR_MULT"],
                "tp1": CONFIG["TP1_ATR_MULT"],
                "tp2": CONFIG["TP2_ATR_MULT"],
            },
        )

    # Read from config so values can be overridden in config.yaml.
    # Defaults mirror CALC_LEVELS_REGIME_FACTOR in config.py.
    _REGIME_FACTOR = CONFIG.get(
        "CALC_LEVELS_REGIME_FACTOR",
        {0: 1.10, 1: 1.0, 2: 1.15, 3: 0.90},
    )
    # 0=TRENDING: slight breathing room (1.10x)
    # 1=RANGING: base multipliers unchanged (1.0x)
    # 2=HIGH_VOLATILITY: modest widening (1.15x) — was 1.35x, too aggressive for altcoins
    # 3=LOW_VOLATILITY: slightly tighter (0.90x) — compressed environment

    rf = _REGIME_FACTOR.get(regime_state, 1.0)

    mults_base = {"sl": float(m["sl"]), "tp1": float(m["tp1"]), "tp2": float(m["tp2"])}

    sl_mult = mults_base["sl"] * rf

    tp1_mult = mults_base["tp1"] * rf

    tp2_mult = mults_base["tp2"] * rf

    mults_effective = {"sl": sl_mult, "tp1": tp1_mult, "tp2": tp2_mult}

    sl = price - atr * sl_mult if direction == "LONG" else price + atr * sl_mult

    tp1 = price + atr * tp1_mult if direction == "LONG" else price - atr * tp1_mult

    tp2 = price + atr * tp2_mult if direction == "LONG" else price - atr * tp2_mult

    rr1 = abs(tp1 - price) / abs(sl - price) if abs(sl - price) > 0 else 0

    rr2 = abs(tp2 - price) / abs(sl - price) if abs(sl - price) > 0 else 0

    return {
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr1": rr1,
        "rr2": rr2,
        "mults": mults_effective,
        "mults_base": mults_base,
        "mults_effective": mults_effective,
        "regimeFactor": rf,
    }


def select_overlay_sl(
    direction: str, math_sl: float, struct_sl: float, floor_atr: bool
) -> float:
    """Combine the ATR (math) SL with an Engine B structural SL for Engine A.

    direction: "LONG" or "SHORT".
    math_sl:   ATR-based SL from calc_levels (the baseline distance).
    struct_sl: structural SL recommended by Engine B.

    floor_atr=False (legacy/default): pick the tighter stop (closer to price).
        LONG -> max(...), SHORT -> min(...).
    floor_atr=True: floor the stop at the ATR distance — the structural SL may
        only WIDEN the stop, never tighten it inside the ATR baseline. This
        avoids placing the stop on swing/sweep liquidity that gets hunted.
        LONG -> min(...) (lower = wider), SHORT -> max(...) (higher = wider).
    """
    if floor_atr:
        return min(math_sl, struct_sl) if direction == "LONG" else max(math_sl, struct_sl)
    return max(math_sl, struct_sl) if direction == "LONG" else min(math_sl, struct_sl)


def calc_fib(candles: list) -> dict:
    """Calculate Fibonacci retracement levels from last 50 candles' high/low range."""

    if not candles:
        return {
            "highest": 0.0, "lowest": 0.0,
            "fib236": 0.0, "fib382": 0.0, "fib500": 0.0,
            "fib618": 0.0, "fib786": 0.0, "ext1618": 0.0
        }

    r = candles[-50:]
    highs = [c["high"] for c in r if c.get("high") is not None]
    lows = [c["low"] for c in r if c.get("low") is not None]

    if not highs or not lows:
        return {
            "highest": 0.0, "lowest": 0.0,
            "fib236": 0.0, "fib382": 0.0, "fib500": 0.0,
            "fib618": 0.0, "fib786": 0.0, "ext1618": 0.0
        }

    high = max(highs)
    low = min(lows)
    rng = high - low

    return {
        "highest": round(high, 6),
        "lowest": round(low, 6),
        "fib236": round(high - rng * 0.236, 6),
        "fib382": round(high - rng * 0.382, 6),
        "fib500": round(high - rng * 0.5, 6),
        "fib618": round(high - rng * 0.618, 6),
        "fib786": round(high - rng * 0.786, 6),
        "ext1618": round(high + rng * 1.618, 6),
    }


def calc_indicators(candles: list, *, _bundle: dict | None = None) -> dict:
    """Compute all indicators for a candle series. Returns dict with 'snap' of latest values."""
    bundle = _bundle or _calc_indicator_bundle(candles)
    cl = bundle["close"]
    e21 = bundle["ema21"]
    e50 = bundle["ema50"]
    e200 = bundle["ema200"]
    rsi = bundle["rsi"]
    macd = bundle["macd"]
    atr = bundle["atr"]
    adx = bundle["adx"]
    bb = bundle["bb"]

    L = len(cl) - 1

    adx_now = adx["adx"][L]

    adx_prev = next(
        (adx["adx"][j] for j in range(L - 1, -1, -1) if adx["adx"][j] is not None), None
    )

    rsi_prev = next((rsi[j] for j in range(L - 1, -1, -1) if rsi[j] is not None), None)

    e200_slope = (
        round((e200[L] - e200[L - 10]) / e200[L - 10] * 100, 3)
        if e200[L] and L >= 10 and e200[L - 10]
        else 0
    )

    adx_pct, adx_lbl = calc_adx_percentile(adx["adx"])

    atr_pct, atr_lbl = calc_atr_percentile(atr)

    adx_slope, adx_mom = calc_adx_momentum(adx["adx"])

    return {
        "snap": {
            "ema21": e21[L],
            "ema50": e50[L],
            "ema200": e200[L],
            "close": cl[L],
            "rsi": rsi[L],
            "rsiPrev": rsi_prev,
            "macdLine": macd["macd"][L],
            "macdSignal": macd["signal"][L],
            "macdHist": macd["hist"][L],
            "macdHistPrev": macd["hist"][L - 1] if L > 0 else None,
            "atr": atr[L],
            "adx": adx_now,
            "adxPrev": adx_prev,
            "adxPct": adx_pct,
            "adxLabel": adx_lbl,
            "atrPct": atr_pct,
            "atrLabel": atr_lbl,
            "adxSlope": adx_slope,
            "adxMomentum": adx_mom,
            "plusDI": adx["plusDI"][L],
            "minusDI": adx["minusDI"][L],
            "bbUpper": bb["upper"][L],
            "bbMid": bb["mid"][L],
            "bbLower": bb["lower"][L],
            "ema200Slope10": e200_slope,
        }
    }


def _calc_indicator_bundle(candles: list, periods: dict | None = None) -> dict:
    """Shared raw-series calculation used by calc_indicators* helpers.

    periods: optional dict from Engine A group resolvers (e.g. {'ema_trend':21, 'ema_long':200, ...}).
    When omitted or incomplete, falls back to the long-standing universal defaults (zero behavior change).
    """
    cl = [c["close"] for c in candles]
    hi = [c["high"] for c in candles]
    lo = [c["low"] for c in candles]
    p = periods or {}
    # Exact historical defaults preserved when key missing — this is the safe default path.
    return {
        "close": cl,
        "high": hi,
        "low": lo,
        "ema21": calc_ema(cl, int(p.get("ema_trend", 21))),
        "ema50": calc_ema(cl, int(p.get("ema_momentum", p.get("ema_trend", 50)))),
        "ema200": calc_ema(cl, int(p.get("ema_long", 200))),
        "rsi": calc_rsi(cl, int(p.get("rsi", 14))),
        "macd": calc_macd(cl),  # MACD periods are applied at call site via wrapper for now (macd default 12/26/9)
        "atr": calc_atr(hi, lo, cl, int(p.get("atr", 14))),
        "adx": calc_adx(hi, lo, cl, int(p.get("adx", 14))),
        "bb": calc_bb(cl, 20, 2),
    }


def calc_indicators_with_normalized(
    candles: list,
    asset_type: str = "crypto",
    score_group: str | None = None,
) -> dict:
    """Compute indicators and return raw + normalized fields for factor scoring.

    When score_group is provided and ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED is true,
    uses the group-calibrated period path (from the 2026 calibration review fix).
    This broadens adoption for backtests, research, and any Engine A paths calling
    this function without changing behavior for existing callers that omit score_group.
    """

    if score_group:
        # Use the group-aware path when explicitly requested (safe opt-in)
        try:
            from factor_scoring import _engine_a_group_adjustments_enabled
            if _engine_a_group_adjustments_enabled():
                return calc_indicators_for_engine_a(candles, score_group=score_group, asset_type=asset_type)
        except Exception:
            pass  # fall through to universal

    bundle = _calc_indicator_bundle(candles)
    base = calc_indicators(candles, _bundle=bundle)

    # Prepare raw series for normalization

    lookback = get_normalization_lookback(asset_type)

    # Build series aligned with candles (None-padded)

    close_series = bundle["close"]
    rsi_series = bundle["rsi"]
    macd_line = bundle["macd"]["macd"]
    atr_series = bundle["atr"]
    adx_series = bundle["adx"]["adx"]

    # BB width

    bb = bundle["bb"]

    bb_width = [None] * len(close_series)

    for i in range(len(close_series)):
        if bb["upper"][i] is not None and bb["lower"][i] is not None:
            bb_width[i] = bb["upper"][i] - bb["lower"][i]

    # Realized volatility

    vol = calc_realized_volatility(
        candles, lookback=CONFIG.get("REALIZED_VOL_LOOKBACK", 30), asset_type=asset_type
    )

    # OBV trend (+1 rising, -1 falling) — convert string result to numeric for factor scoring
    # None when no trend detected or no real volume data — excluded from factor scoring (not 0)
    _obv_str = calc_obv_trend(candles)
    obv_val = (
        {"confirming": 1, "diverging_bullish": 0.5, "diverging_bearish": -0.5}.get(
            _obv_str
        )
        if _obv_str
        else None
    )

    # Normalized series

    rsi_z = zscore_normalize(rsi_series, lookback)

    macd_z = zscore_normalize(macd_line, lookback)

    atr_z = zscore_normalize(atr_series, lookback)

    adx_z = zscore_normalize(adx_series, lookback)

    bbw_z = zscore_normalize(bb_width, lookback)

    # Percentiles for some indicators

    rsi_pct = percentile_rank(rsi_series, lookback)

    macd_pct = percentile_rank(macd_line, lookback)

    atr_pct = percentile_rank(atr_series, lookback)

    adx_pct = percentile_rank(adx_series, lookback)

    bbw_pct = percentile_rank(bb_width, lookback)

    # Latest values

    L = len(candles) - 1

    result = {"snap": dict(base["snap"])}  # shallow copy

    result["snap"].update(
        {
            "rsi_z": rsi_z[L],
            "rsi_pct": rsi_pct[L],
            "macdLine_z": macd_z[L],
            "macdLine_pct": macd_pct[L],
            "atr_z": atr_z[L],
            "atr_pct": atr_pct[L],
            "adx_z": adx_z[L],
            "adx_pct": adx_pct[L],
            "bbWidth_z": bbw_z[L],
            "bbWidth_pct": bbw_pct[L],
            "realized_vol": vol["realized_vol"],
            "realized_vol_z": vol["realized_vol_z"],
            "realized_vol_pct": vol["realized_vol_pct"],
            "obv_trend": obv_val,
        }
    )

    return result


# ── Engine A group-calibrated entry point (2026 calibration review) ───────────
# Thin wrapper that resolves per-score_group / asset_type periods via the
# existing factor_scoring resolvers and produces a snap using those lengths.
# All existing callers (scanner, backtest, etc.) continue to use the universal
# calc_indicators / calc_indicators_with_normalized paths — zero behavior change.
# When ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED is false, this returns identical
# results to the classic path.
def calc_indicators_for_engine_a(
    candles: list,
    score_group: str | None = None,
    asset_type: str = "other",
) -> dict:
    """Compute indicators using group-resolved periods when the calibration flag is on.

    Returns the same shape as calc_indicators(...).
    Safe to call from any Engine A path; falls back gracefully.
    """
    try:
        from factor_scoring import (
            _resolve_ema_periods,
            _resolve_rsi_period,
            _resolve_macd_params,
            _resolve_atr_adx_periods,
            _engine_a_group_adjustments_enabled,
        )

        if not _engine_a_group_adjustments_enabled():
            # Fast path — identical to historical behavior
            bundle = _calc_indicator_bundle(candles)
            return calc_indicators(candles, _bundle=bundle)

        ema_p = _resolve_ema_periods(score_group, asset_type)
        rsi_p = _resolve_rsi_period(score_group, asset_type)
        macd_p = _resolve_macd_params(score_group, asset_type)
        atr_adx_p = _resolve_atr_adx_periods(score_group, asset_type)

        # Build periods dict that _calc_indicator_bundle understands
        periods = {
            "ema_trend": ema_p.get("trend", 21),
            "ema_momentum": ema_p.get("momentum", 50),
            "ema_long": ema_p.get("long", 200),
            "rsi": rsi_p,
            "macd_fast": macd_p.get("fast", 12),
            "macd_slow": macd_p.get("slow", 26),
            "macd_signal": macd_p.get("signal", 9),
            "atr": atr_adx_p.get("atr", 14),
            "adx": atr_adx_p.get("adx", 14),
        }

        bundle = _calc_indicator_bundle(candles, periods=periods)
        # Note: full MACD period override would require a small enhancement to calc_macd;
        # for v1 we keep the proven 12/26/9 while still exposing the resolver for future use.
        return calc_indicators(candles, _bundle=bundle)

    except Exception as exc:
        # Absolute safety: never break scoring because of the new calibration path
        log.warning("[EA-CAL] group period resolution failed, falling back to universal: %s", exc)
        bundle = _calc_indicator_bundle(candles)
        return calc_indicators(candles, _bundle=bundle)


def _pct_change(series: list, lookback: int) -> float | None:
    """Percent change between the latest value and N bars back."""
    if len(series) <= lookback:
        return None
    base = series[-(lookback + 1)]
    last = series[-1]
    if base in (None, 0) or last is None:
        return None
    return ((last - base) / abs(base)) * 100.0


def _pearson_corr(a: list, b: list) -> float | None:
    """Small pure-Python Pearson correlation for aligned numeric series."""
    if len(a) != len(b) or len(a) < 2:
        return None

    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)

    if var_a <= 0 or var_b <= 0:
        return None

    return cov / math.sqrt(var_a * var_b)


def calc_usd_relative_strength_context(
    asset_candles: list,
    usd_proxy_candles: list,
    asset_label: str = "Asset",
    proxy_label: str = "DXY",
    tf: str = "H4",
) -> dict | None:
    """Build a read-only relative-strength summary for an asset vs a USD proxy."""
    asset_close = []
    for candle in asset_candles or []:
        try:
            asset_close.append(float(candle["close"]))
        except (KeyError, TypeError, ValueError):
            continue

    proxy_close = []
    for candle in usd_proxy_candles or []:
        try:
            proxy_close.append(float(candle["close"]))
        except (KeyError, TypeError, ValueError):
            continue

    if len(asset_close) < 6 or len(proxy_close) < 6:
        return None

    asset_change = _pct_change(asset_close, 5)
    proxy_change = _pct_change(proxy_close, 5)
    if asset_change is None or proxy_change is None:
        return None

    corr_window = min(len(asset_close), len(proxy_close), 30)
    corr = _pearson_corr(asset_close[-corr_window:], proxy_close[-corr_window:])
    spread = asset_change - proxy_change
    composite = (asset_change * 0.65) + ((-proxy_change) * 0.35)

    if spread > 0:
        bias = "asset_stronger"
        bias_label = f"{asset_label} stronger"
    elif spread < 0:
        bias = "usd_stronger"
        bias_label = "USD stronger"
    else:
        bias = "balanced"
        bias_label = "Balanced"

    if corr is None:
        relationship = "unknown"
        relationship_label = "unknown"
    elif corr <= -0.35:
        relationship = "inverse"
        relationship_label = "inverse"
    elif corr >= 0.35:
        relationship = "positive"
        relationship_label = "positive"
    else:
        relationship = "mixed"
        relationship_label = "mixed"

    if corr is None:
        corr_mult = 0.6
    elif corr <= -0.35:
        corr_mult = 1.0
    elif corr < 0.35:
        corr_mult = 0.75
    else:
        corr_mult = 0.4

    score = max(-3.0, min(3.0, (composite / 0.5) * corr_mult))
    proxy_trend = (
        "rising" if proxy_change > 0 else "falling" if proxy_change < 0 else "flat"
    )
    corr_text = f"{corr:+.2f}" if corr is not None else "n/a"

    return {
        "assetLabel": asset_label,
        "proxyLabel": proxy_label,
        "timeframe": tf,
        "lookbackBars": 5,
        "correlationWindow": corr_window,
        "assetChangePct": round(asset_change, 2),
        "usdProxyChangePct": round(proxy_change, 2),
        "spreadPct": round(spread, 2),
        "correlation": round(corr, 3) if corr is not None else None,
        "relationship": relationship,
        "relationshipLabel": relationship_label,
        "usdProxyTrend": proxy_trend,
        "score": round(score, 3),
        "usd_proxy_score": round(score, 3),
        "bias": bias,
        "biasLabel": bias_label,
        "summary": (
            f"{bias_label} vs USD proxy ({proxy_label}) | "
            f"{asset_label} 5x{tf} {asset_change:+.2f}% | "
            f"{proxy_label} 5x{tf} {proxy_change:+.2f}% | "
            f"corr{corr_window}={corr_text}"
        ),
    }


# ── Forex-specific helpers (safe, optional, never called by other engines) ──


def detect_fvg(candles: list) -> list:
    """Fair Value Gap detection — standard 3-candle pattern.
    Candle 1 (i-2) = reference, Candle 2 (i-1) = impulse, Candle 3 (i) = confirmation.
    Bullish FVG: Candle 3 low > Candle 1 high (gap above Candle 1).
    Bearish FVG: Candle 3 high < Candle 1 low (gap below Candle 1)."""
    fvgs = []
    for i in range(2, len(candles)):
        c1_high = candles[i - 2]["high"]  # Candle 1
        c1_low = candles[i - 2]["low"]
        c3_high = candles[i]["high"]  # Candle 3
        c3_low = candles[i]["low"]

        if c3_low > c1_high:  # Bullish FVG — gap zone is [c1_high, c3_low]
            fvgs.append({"type": "bullish", "top": c3_low, "bottom": c1_high})
        if c3_high < c1_low:  # Bearish FVG — gap zone is [c3_high, c1_low]
            fvgs.append({"type": "bearish", "top": c1_low, "bottom": c3_high})
    return fvgs


def detect_liquidity_sweep(candles: list, atr: float) -> bool:
    """Liquidity sweep: equal highs/lows + price wick beyond the equal level."""
    if len(candles) < 10:
        return False
    highs = [c["high"] for c in candles[-8:]]
    lows = [c["low"] for c in candles[-8:]]
    last_high = highs[-1]
    last_low = lows[-1]
    prev_high = max(highs[:-1])
    prev_low = min(lows[:-1])

    if abs(last_high - prev_high) < atr * 0.25 and last_high > prev_high + atr * 0.1:
        return True  # swept equal highs
    if abs(last_low - prev_low) < atr * 0.25 and last_low < prev_low - atr * 0.1:
        return True  # swept equal lows
    return False


def volume_strength_at_level(candles: list, level: float, lookback: int = 20) -> float:
    """Volume strength at a key level (Asian range or Fib). 1.0 = strong."""
    if len(candles) < lookback:
        return 1.0
    recent_vol = sum(c.get("vol", 0) for c in candles[-lookback:])
    avg_vol = recent_vol / lookback
    if avg_vol == 0:
        return 1.0
    level_vol = sum(
        c.get("vol", 0)
        for c in candles[-5:]
        if abs(c["close"] - level) < (c["high"] - c["low"]) * 0.5
    )
    return min(1.0, level_vol / (avg_vol * 0.6))


def calc_vwap(candles: list, *, anchor_index: int = 0, band_mult: float = 0.5) -> dict:
    """Session-anchored VWAP with ATR-based bands.

    Args:
        candles: list of dicts with keys: high, low, close, vol
        anchor_index: bar index to start VWAP calculation from (0 = first bar)
        band_mult: ATR multiplier used for the VWAP bands

    Returns:
        {vwap: list[float], upper_band: list[float], lower_band: list[float],
         atr_band_width: float}
    """
    if not candles or len(candles) < 2:
        return {"vwap": [], "upper_band": [], "lower_band": []}

    vwap_values = []
    upper_band = []
    lower_band = []
    cum_vol = 0.0
    cum_tp_vol = 0.0

    # ATR for band width (14-period)
    highs = [float(c.get("high", 0)) for c in candles]
    lows = [float(c.get("low", 0)) for c in candles]
    closes = [float(c.get("close", 0)) for c in candles]
    atr_list = calc_atr(highs, lows, closes, 14)
    band_mult = float(band_mult)

    for i, c in enumerate(candles):
        if i < anchor_index:
            vwap_values.append(None)
            upper_band.append(None)
            lower_band.append(None)
            continue

        _close = float(c.get("close", 0))
        typical_price = (
            float(c.get("high", _close)) + float(c.get("low", _close)) + _close
        ) / 3.0
        vol = float(c.get("vol", 0) or 0.0)
        if vol > 0:
            cum_tp_vol += typical_price * vol
            cum_vol += vol

        if cum_vol <= 0:
            vwap_values.append(None)
            upper_band.append(None)
            lower_band.append(None)
            continue

        vwap_val = cum_tp_vol / cum_vol
        vwap_values.append(round(vwap_val, 8))

        # ATR band
        atr_val = atr_list[i] if i < len(atr_list) else (atr_list[-1] if atr_list else 0)
        band_offset = atr_val * band_mult if atr_val else 0
        upper_band.append(round(vwap_val + band_offset, 8))
        lower_band.append(round(vwap_val - band_offset, 8))

    return {
        "vwap": vwap_values,
        "upper_band": upper_band,
        "lower_band": lower_band,
        "atr_band_width": band_mult,
    }


def detect_absorption(candles: list, *, vol_mult: float = 2.0, max_move_atr: float = 0.3, sma_period: int = 20) -> list:
    """Detect absorption candles: high volume + small price movement.

    Based on Fabio Valentini's Pine Script absorption formula:
        volume > SMA(volume, 20) * 2.0  AND  |close - open| / ATR < 0.3

    Args:
        candles: list of dicts with keys: open, high, low, close, vol
        vol_mult: volume must exceed SMA * this multiplier (default 2.0, range 1.5-3.5)
        max_move_atr: max candle body as fraction of ATR (default 0.3)
        sma_period: SMA period for average volume (default 20)

    Returns:
        list of dicts per candle: {absorbed: bool, vol_ratio: float, move_ratio: float, direction: str}
    """
    if not candles or len(candles) < sma_period + 1:
        return []

    highs = [float(c.get("high", 0)) for c in candles]
    lows = [float(c.get("low", 0)) for c in candles]
    closes = [float(c.get("close", 0)) for c in candles]
    opens = [float(c.get("open", 0)) for c in candles]
    raw_vols = [float(c.get("vol", 0)) for c in candles]
    # Fallback to range proxy if no tick volume is available (common for forex MT5 historical data)
    if max(raw_vols) <= 0:
        vols = [max(highs[i] - lows[i], 1e-10) for i in range(len(candles))]
    else:
        vols = raw_vols

    atr_list = calc_atr(highs, lows, closes, 14)
    vol_sma = calc_sma(vols, sma_period)

    results = []
    for i in range(len(candles)):
        if i < sma_period or i >= len(atr_list) or i >= len(vol_sma):
            results.append({"absorbed": False, "vol_ratio": 0.0, "move_ratio": 0.0, "direction": "neutral"})
            continue

        atr_val = atr_list[i] if atr_list[i] and atr_list[i] > 0 else 1e-10
        avg_vol = vol_sma[i] if vol_sma[i] and vol_sma[i] > 0 else 1e-10

        body = abs(closes[i] - opens[i])
        vol_ratio = vols[i] / avg_vol
        move_ratio = body / atr_val

        absorbed = vol_ratio >= vol_mult and move_ratio < max_move_atr

        # Direction of absorption: which side absorbed the aggression
        if absorbed:
            if closes[i] >= opens[i]:
                direction = "bullish"  # buyers absorbed selling pressure
            else:
                direction = "bearish"  # sellers absorbed buying pressure
        else:
            direction = "neutral"

        results.append({
            "absorbed": absorbed,
            "vol_ratio": round(vol_ratio, 3),
            "move_ratio": round(move_ratio, 3),
            "direction": direction,
        })

    return results


def calc_cvd(candles: list, *, smooth_period: int = 5) -> dict:
    """Cumulative Volume Delta — candle-based buy/sell pressure approximation.

    Approximation method (matches Fabio's Pine Script):
        - Bullish candle (close > open): buy_vol = vol * (close - low) / (high - low)
        - Bearish candle (close < open): sell_vol = vol * (high - close) / (high - low)
        - Delta per bar = buy_vol - sell_vol
        - CVD = cumulative sum of delta
        - Smoothed delta = SMA(delta, smooth_period)

    Args:
        candles: list of dicts with keys: open, high, low, close, vol
        smooth_period: smoothing period for delta (default 5)

    Returns:
        {delta: list[float], cvd: list[float], smoothed_delta: list[float]}
    """
    if not candles:
        return {"delta": [], "cvd": [], "smoothed_delta": []}

    deltas = []
    cvd_values = []
    cumulative = 0.0

    for c in candles:
        o = float(c.get("open", 0))
        h = float(c.get("high", 0))
        lo = float(c.get("low", 0))
        cl = float(c.get("close", 0))
        vol = float(c.get("vol", 0))

        candle_range = h - lo
        if candle_range <= 0:
            deltas.append(0.0)
            cumulative += 0.0
            cvd_values.append(round(cumulative, 4))
            continue
        # Use range as proxy volume when tick volume is zero (forex MT5 historical data)
        if vol <= 0:
            vol = candle_range

        # Buy volume: proportion of range from low to close
        buy_vol = vol * (cl - lo) / candle_range
        # Sell volume: proportion of range from close to high (inverted)
        sell_vol = vol * (h - cl) / candle_range
        delta = buy_vol - sell_vol

        deltas.append(round(delta, 4))
        cumulative += delta
        cvd_values.append(round(cumulative, 4))

    # Smooth delta
    smoothed = calc_sma(deltas, smooth_period) if len(deltas) >= smooth_period else deltas[:]

    return {
        "delta": deltas,
        "cvd": cvd_values,
        "smoothed_delta": smoothed,
    }


def detect_range_contraction(candles: list, *, lookback: int = 10, threshold: float = 0.5) -> dict:
    """Detect range contraction (accumulation phase of AAA framework).

    Compares the ATR of the last `lookback` bars to the ATR of the preceding
    `lookback` bars. If current ATR is below `threshold` fraction of prior ATR,
    accumulation is flagged.

    Args:
        candles: list of dicts with keys: high, low, close
        lookback: number of bars to compare (default 10)
        threshold: contraction ratio threshold (default 0.5 = 50% of prior ATR)

    Returns:
        {contracting: bool, ratio: float, current_atr: float, prior_atr: float}
    """
    if not candles or len(candles) < lookback * 2 + 14:
        return {"contracting": False, "ratio": 1.0, "current_atr": 0.0, "prior_atr": 0.0}

    highs = [float(c.get("high", 0)) for c in candles]
    lows = [float(c.get("low", 0)) for c in candles]
    closes = [float(c.get("close", 0)) for c in candles]
    atr_list = calc_atr(highs, lows, closes, 14)

    if len(atr_list) < lookback * 2:
        return {"contracting": False, "ratio": 1.0, "current_atr": 0.0, "prior_atr": 0.0}

    current_slice = atr_list[-lookback:]
    prior_slice = atr_list[-(lookback * 2):-lookback]

    current_avg = sum(current_slice) / len(current_slice) if current_slice else 0
    prior_avg = sum(prior_slice) / len(prior_slice) if prior_slice else 0

    if prior_avg <= 0:
        return {"contracting": False, "ratio": 1.0, "current_atr": current_avg, "prior_atr": prior_avg}

    ratio = current_avg / prior_avg

    return {
        "contracting": ratio < threshold,
        "ratio": round(ratio, 4),
        "current_atr": round(current_avg, 8),
        "prior_atr": round(prior_avg, 8),
    }
