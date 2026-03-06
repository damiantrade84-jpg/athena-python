"""indicators.py — Technical indicator calculations for Athena Pro.

Pure math functions (no I/O) plus calc_levels which reads CONFIG for
per-asset-class ATR multipliers. Safe to import and unit-test in isolation.
"""
import math
import logging
from config import CONFIG

log = logging.getLogger("athena")


def calc_ema(c: list, p: int) -> list:
    """Exponential Moving Average. Returns list aligned with input, None-padded."""
    k = 2 / (p + 1)
    e = [None] * len(c)
    if len(c) < p:
        return e
    e[p - 1] = sum(c[:p]) / p
    for i in range(p, len(c)):
        e[i] = c[i] * k + e[i - 1] * (1 - k)
    return e


def calc_sma(a: list, p: int) -> list:
    """Simple Moving Average. Returns list aligned with input, None-padded."""
    r = [None] * len(a)
    for i in range(p - 1, len(a)):
        r[i] = sum(a[i - p + 1:i + 1]) / p
    return r


def calc_rsi(c: list, p: int) -> list:
    """Wilder RSI (smoothed). Returns list aligned with input, None-padded."""
    r = [None] * len(c)
    if len(c) < p + 1:
        return r
    g = l = 0
    for i in range(1, p + 1):
        d = c[i] - c[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    ag, al = g / p, l / p
    r[p] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(p + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (p - 1) + max(d, 0)) / p
        al = (al * (p - 1) + max(-d, 0)) / p
        r[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return r


def calc_macd(c: list, f: int = 12, s: int = 26, sig: int = 9) -> dict:
    ef, es = calc_ema(c, f), calc_ema(c, s)
    ml = [ef[i] - es[i] if ef[i] is not None and es[i] is not None else None for i in range(len(c))]
    valid = [v for v in ml if v is not None]
    se = calc_ema(valid, sig)
    sl2 = [None] * len(c)
    vf = next((i for i, v in enumerate(ml) if v is not None), len(c))
    si = 0
    for i in range(vf, len(c)):
        sl2[i] = se[si] if si < len(se) else None
        si += 1
    hist = [ml[i] - sl2[i] if ml[i] is not None and sl2[i] is not None else None for i in range(len(c))]
    return {"macd": ml, "signal": sl2, "hist": hist}


def calc_atr(h: list, l: list, c: list, p: int) -> list:
    tr = [0] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = [None] * len(c)
    if len(tr) <= p:
        return a
    a[p] = sum(tr[1:p + 1]) / p
    for i in range(p + 1, len(tr)):
        a[i] = (a[i - 1] * (p - 1) + tr[i]) / p
    return a


def calc_adx(hi: list, lo: list, c: list, p: int) -> dict:
    """Wilder ADX with +DI/-DI. Returns dict with aligned arrays, None-padded."""
    n = len(c)
    adx = [None] * n
    plus_di = [None] * n
    minus_di = [None] * n
    if n < p * 2:
        return {"adx": adx, "plusDI": plus_di, "minusDI": minus_di}
    true_range, dm_plus, dm_minus = [], [], []
    for i in range(1, n):
        up_move = hi[i] - hi[i - 1]
        down_move = lo[i - 1] - lo[i]
        dm_plus.append(up_move if up_move > down_move and up_move > 0 else 0)
        dm_minus.append(down_move if down_move > up_move and down_move > 0 else 0)
        true_range.append(max(hi[i] - lo[i], abs(hi[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    smooth_tr = sum(true_range[:p])
    smooth_dp = sum(dm_plus[:p])
    smooth_dm = sum(dm_minus[:p])
    dx_values = []
    for i in range(p, len(true_range)):
        smooth_tr = smooth_tr - smooth_tr / p + true_range[i]
        smooth_dp = smooth_dp - smooth_dp / p + dm_plus[i]
        smooth_dm = smooth_dm - smooth_dm / p + dm_minus[i]
        pdi_val = (smooth_dp / smooth_tr) * 100 if smooth_tr else 0
        mdi_val = (smooth_dm / smooth_tr) * 100 if smooth_tr else 0
        di_sum = pdi_val + mdi_val
        plus_di[i + 1] = pdi_val
        minus_di[i + 1] = mdi_val
        dx_values.append(abs(pdi_val - mdi_val) / di_sum * 100 if di_sum else 0)
    if len(dx_values) >= p:
        adx_avg = sum(dx_values[:p]) / p
        if p * 2 < n:
            adx[p * 2] = adx_avg
        for i in range(p, len(dx_values)):
            adx_avg = (adx_avg * (p - 1) + dx_values[i]) / p
            idx = i + p + 1
            if idx < n:
                adx[idx] = adx_avg
    return {"adx": adx, "plusDI": plus_di, "minusDI": minus_di}


def calc_bb(c: list, p: int, m: float) -> dict:
    u, mid, l = [], [], []
    for i in range(len(c)):
        if i < p - 1:
            u.append(None); mid.append(None); l.append(None)
            continue
        sl = c[i - p + 1:i + 1]
        mn = sum(sl) / p
        sd = math.sqrt(sum((x - mn) ** 2 for x in sl) / p)
        mid.append(mn); u.append(mn + m * sd); l.append(mn - m * sd)
    return {"upper": u, "mid": mid, "lower": l}


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
        phigh_idx = [i for i in range(1, n - 1) if hi[i] > hi[i - 1] and hi[i] > hi[i + 1] and rsi[i] is not None]
        plow_idx  = [i for i in range(1, n - 1) if lo[i] < lo[i - 1] and lo[i] < lo[i + 1] and rsi[i] is not None]
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
        price = cl[L]; ma = ma30w[L]; ma_prev = ma30w[L - 5]
        ma_rising = ma > ma_prev
        if price > ma and ma_rising:     return 2, "Stage 2 — Advancing"
        if price > ma and not ma_rising: return 3, "Stage 3 — Topping"
        if price < ma and not ma_rising: return 4, "Stage 4 — Declining"
        if price < ma and ma_rising:     return 1, "Stage 1 — Basing"
    except Exception as e:
        log.warning(f"calc_weinstein_stage: {e}")
    return None, None


def calc_fib_proximity(price: float, fib: dict) -> int:
    """Check if price is near a key Fibonacci level (within 1.5% range band).
    Returns +1 (support/bullish), -1 (resistance/bearish), or 0 (not near)."""
    try:
        levels = [fib["fib382"], fib["fib500"], fib["fib618"]]
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
    hi = [c["high"] for c in candles]
    lo = [c["low"] for c in candles]
    cl = [c["close"] for c in candles]
    n = len(cl)
    rawK = [None] * n
    for i in range(kp - 1, n):
        hh = max(hi[i - kp + 1:i + 1])
        ll = min(lo[i - kp + 1:i + 1])
        rawK[i] = ((cl[i] - ll) / (hh - ll)) * 100 if hh != ll else 50
    mapped = [v if v is not None else 0 for v in rawK]
    kL = calc_sma(mapped, ks)
    for i in range(kp - 1 + ks - 1):
        if i < len(kL):
            kL[i] = None
    mapped2 = [v if v is not None else 0 for v in kL]
    dL = calc_sma(mapped2, ds)
    for i in range(kp - 1 + ks - 1 + ds - 1):
        if i < len(dL):
            dL[i] = None
    return {"k": kL, "d": dL}


def calc_adx_momentum(adx_series: list, window: int = 5) -> tuple:
    """Detect ADX momentum — is the trend strengthening or exhausting?
    Returns (slope, state): 'strengthening', 'exhausting', 'collapsing', or 'stable'."""
    valid = [v for v in adx_series if v is not None]
    if len(valid) < window + 2:
        return 0, "stable"
    recent = valid[-(window + 1):]
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
    label = "expanding" if (len(valid) >= 3 and valid[-1] > valid[-2]) else "contracting"
    return pct, label


def calc_atr_percentile(atr_series: list, lookback: int = 100) -> tuple:
    """Rank current ATR vs its own history. Returns (percentile 0-100, label)."""
    valid = [v for v in atr_series if v is not None]
    if len(valid) < 20:
        return None, "insufficient data"
    cur = valid[-1]
    window = valid[-lookback:]
    pct = round(sum(1 for v in window if v <= cur) / len(window) * 100, 1)
    label = "expanding" if (len(valid) >= 3 and valid[-1] > valid[-2]) else "contracting"
    return pct, label


def calc_fib(candles: list) -> dict:
    """Calculate Fibonacci retracement levels from last 50 candles' high/low range."""
    r = candles[-50:]
    high = max(c["high"] for c in r)
    low  = min(c["low"]  for c in r)
    rng  = high - low
    return {
        "highest": round(high, 6), "lowest": round(low, 6),
        "fib236": round(high - rng * 0.236, 6), "fib382": round(high - rng * 0.382, 6),
        "fib500": round(high - rng * 0.5,   6), "fib618": round(high - rng * 0.618, 6),
        "fib786": round(high - rng * 0.786, 6), "ext1618": round(high + rng * 0.618, 6),
    }


def calc_indicators(candles: list) -> dict:
    """Compute all indicators for a candle series. Returns dict with 'snap' of latest values."""
    cl = [c["close"] for c in candles]
    hi = [c["high"]  for c in candles]
    lo = [c["low"]   for c in candles]
    e21, e50, e200 = calc_ema(cl, 21), calc_ema(cl, 50), calc_ema(cl, 200)
    rsi  = calc_rsi(cl, 14)
    macd = calc_macd(cl)
    atr  = calc_atr(hi, lo, cl, 14)
    adx  = calc_adx(hi, lo, cl, 14)
    bb   = calc_bb(cl, 20, 2)
    L    = len(cl) - 1
    adx_now  = adx["adx"][L]
    adx_prev = next((adx["adx"][j] for j in range(L - 1, -1, -1) if adx["adx"][j] is not None), None)
    rsi_prev = next((rsi[j]        for j in range(L - 1, -1, -1) if rsi[j] is not None), None)
    e200_slope = round((e200[L] - e200[L - 10]) / e200[L - 10] * 100, 3) if e200[L] and L >= 10 and e200[L - 10] else 0
    adx_pct,  adx_lbl  = calc_adx_percentile(adx["adx"])
    atr_pct,  atr_lbl  = calc_atr_percentile(atr)
    adx_slope, adx_mom = calc_adx_momentum(adx["adx"])
    return {"snap": {
        "ema21": e21[L], "ema50": e50[L], "ema200": e200[L], "close": cl[L],
        "rsi": rsi[L], "rsiPrev": rsi_prev,
        "macdLine": macd["macd"][L], "macdSignal": macd["signal"][L],
        "macdHist": macd["hist"][L], "macdHistPrev": macd["hist"][L - 1] if L > 0 else None,
        "atr": atr[L], "adx": adx_now, "adxPrev": adx_prev,
        "adxPct": adx_pct, "adxLabel": adx_lbl,
        "atrPct": atr_pct, "atrLabel": atr_lbl,
        "adxSlope": adx_slope, "adxMomentum": adx_mom,
        "plusDI": adx["plusDI"][L], "minusDI": adx["minusDI"][L],
        "bbUpper": bb["upper"][L], "bbMid": bb["mid"][L], "bbLower": bb["lower"][L],
        "ema200Slope10": e200_slope,
    }}
