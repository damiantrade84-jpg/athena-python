"""Offline calibration of ENGINE_A_TREND_MAGNITUDE.ATR_K on frozen candles.

Measures the distribution of |fast_EMA - slow_EMA| / ATR per trend layer
(D1 trend-vs-long, H4/H1 trend-vs-momentum) per score group, using the repo's
own calc_ema / calc_atr so the math matches the live snap exactly.

Goal: pick ATR_K + MIN_MULT so an *established* trend (gap at or above the
median of trending bars) keeps mult >= ~0.85 while a barely-crossed pair
(bottom decile) is meaningfully damped.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indicators import calc_atr, calc_ema  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "frozen", "2026-05-30", "candles")

# symbol file prefix -> (score_group, trend_period, momentum_period)
SYMBOLS = {
    "BTC_USDT": ("crypto_btc", 18, 40),
    "ETH_USDT": ("crypto_eth", 18, 40),
    "SOL_USDT": ("crypto_alt_majors", 18, 40),
    "XRP_USDT": ("crypto_alt_majors", 18, 40),
    "DOGE_USDT": ("crypto_doge", 18, 40),
    "EUR_USD": ("forex_majors", 26, 60),
    "GBP_USD": ("forex_majors", 26, 60),
    "USD_JPY": ("forex_majors", 26, 60),
    "GBP_JPY": ("forex_crosses", 26, 60),
    "AAPL": ("us_stock_single", 21, 50),
    "MSFT": ("us_stock_single", 21, 50),
    "NVDA": ("us_stock_single", 21, 50),
    "TSLA": ("us_stock_single", 21, 50),
    "S_P_500": ("us_indices_trackers", 21, 50),
    "NASDAQ-100": ("us_indices_trackers", 21, 50),
    "Dow_Jones": ("us_indices_trackers", 21, 50),
    "DAX_40": ("eu_indices", 21, 50),
    "WTI_Oil": ("energy_oil", 21, 50),
    "XAU_USD": ("precious_trackers", 21, 50),
    "XAG_USD": ("precious_trackers", 21, 50),
}

LONG_ANCHOR = 200
ATR_P = 14
PERSIST_BARS = 10  # sign stable this many bars = "established" trend


def load(symbol, tf):
    for fn in os.listdir(DATA):
        if fn.startswith(f"{symbol}__{tf}__"):
            with open(os.path.join(DATA, fn), encoding="utf-8") as f:
                return json.load(f)
    return None


def layer_strengths(candles, fast_p, slow_p):
    cl = [c["close"] for c in candles]
    hi = [c["high"] for c in candles]
    lo = [c["low"] for c in candles]
    fast = calc_ema(cl, fast_p)
    slow = calc_ema(cl, slow_p)
    atr = calc_atr(hi, lo, cl, ATR_P)
    fresh, established, all_bars = [], [], []
    sign_run = 0
    prev_sign = 0
    for i in range(len(cl)):
        if fast[i] is None or slow[i] is None or atr[i] is None or atr[i] <= 0:
            continue
        gap = fast[i] - slow[i]
        sign = 1 if gap > 0 else (-1 if gap < 0 else 0)
        if sign != 0 and sign == prev_sign:
            sign_run += 1
        else:
            sign_run = 1
        prev_sign = sign
        if sign == 0:
            continue
        s = abs(gap) / atr[i]
        all_bars.append(s)
        if sign_run <= 3:
            fresh.append(s)
        elif sign_run >= PERSIST_BARS:
            established.append(s)
    return fresh, established, all_bars


def pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def main():
    # group -> layer -> {"fresh": [...], "est": [...], "all": [...]}
    agg = {}
    for sym, (group, trend_p, mom_p) in SYMBOLS.items():
        layers = [("D1", trend_p, LONG_ANCHOR), ("H4", trend_p, mom_p), ("H1", trend_p, mom_p)]
        for tf, fp, sp in layers:
            candles = load(sym, tf)
            if not candles:
                continue
            fresh, est, alls = layer_strengths(candles, fp, sp)
            slot = agg.setdefault(group, {}).setdefault(tf, {"fresh": [], "est": [], "all": []})
            slot["fresh"] += fresh
            slot["est"] += est
            slot["all"] += alls

    print(f"{'group':22s} {'tf':3s} {'n_est':>6s} {'fresh_p50':>9s} {'est_p25':>8s} {'est_p50':>8s} {'est_p75':>8s}")
    for group in sorted(agg):
        for tf in ("D1", "H4", "H1"):
            d = agg[group].get(tf)
            if not d:
                continue
            print(f"{group:22s} {tf:3s} {len(d['est']):6d} {pct(d['fresh'], 0.5):9.3f} "
                  f"{pct(d['est'], 0.25):8.3f} {pct(d['est'], 0.5):8.3f} {pct(d['est'], 0.75):8.3f}")

    # candidate ATR_K sweep: show mult = MIN + (1-MIN)*tanh(s/K) at key quantiles
    print("\nmult at quantiles (MIN_MULT=0.25), weighted-layer view per group")
    print("Weighted strength = 0.5*D1 + 0.3*H4 + 0.2*H1 quantile (approximation of swing weights)")
    for K in (0.5, 0.75, 1.0, 1.5, 2.0):
        print(f"\n  ATR_K = {K}")
        print(f"  {'group':22s} {'fresh_p50':>9s} {'est_p25':>8s} {'est_p50':>8s} {'est_p75':>8s}")
        for group in sorted(agg):
            row = []
            for q, src in (("fresh_p50", ("fresh", 0.5)), ("est_p25", ("est", 0.25)),
                           ("est_p50", ("est", 0.5)), ("est_p75", ("est", 0.75))):
                wsum, wtot = 0.0, 0.0
                for tf, w in (("D1", 0.5), ("H4", 0.3), ("H1", 0.2)):
                    d = agg[group].get(tf)
                    if not d or not d[src[0]]:
                        continue
                    wsum += w * pct(d[src[0]], src[1])
                    wtot += w
                if wtot == 0:
                    row.append(float("nan"))
                    continue
                s = wsum / wtot
                row.append(0.25 + 0.75 * math.tanh(s / K))
            print(f"  {group:22s} {row[0]:9.3f} {row[1]:8.3f} {row[2]:8.3f} {row[3]:8.3f}")


if __name__ == "__main__":
    main()
