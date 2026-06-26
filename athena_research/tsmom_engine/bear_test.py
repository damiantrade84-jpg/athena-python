"""Bear-regime test via self-made D1 bars (normalises the pre-2023 H4 granularity
break). Resample raw H4 -> one clean daily OHLC bar/day, then run the long-only
engine across 2020-2026 incl. the 2022 bear on AAPL/MSFT/TSLA (NVDA = corrupt).
Question: does long-only trend (a) clear SQN>2.0 through a full cycle, and
(b) protect capital in 2022?"""
from __future__ import annotations

import numpy as np
import pandas as pd
from engine import Params, simulate, metrics, load_candles


def to_d1(s):
    df = load_candles(s, "H4").set_index("time")
    d1 = (df.resample("1D")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "vol": "sum"})
            .dropna().reset_index())
    return d1


def longs(tr): return [t for t in tr if t.direction == 1]


def line(tag, m):
    if m.get("N", 0) < 2:
        return f"  {tag:14s} N={m.get('N',0):4d}  (insufficient)"
    return (f"  {tag:14s} N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  exp={m['exp']:+.3f}R  "
            f"win={m['win']*100:4.1f}%  PF={m['pf']:4.2f}  maxDD={m['maxdd']:5.1f}R")


print("########## data sanity after H4->D1 resample (min/max close per year)\n")
d1s = {}
for s in ("AAPL", "MSFT", "TSLA", "NVDA"):
    d = to_d1(s); d1s[s] = d
    d["yr"] = d["time"].dt.year
    g = d.groupby("yr")["close"].agg(["min", "max", "count"])
    print(f"== {s} ==  " + "  ".join(f"{y}:[{r['min']:.1f}-{r['max']:.1f}]n{int(r['count'])}"
                                     for y, r in g.iterrows()))
print("\n(NVDA min near 0 => corrupt, excluded from the clean group.)\n")

print("########## long-only trend on self-made D1, per stock + per year\n")
COST = 0.0003
for s in ("AAPL", "MSFT", "TSLA", "NVDA"):
    for reg in (0, 200):
        p = Params(fast=20, slow=80, atr_mult=3.0, cost=COST, regime=reg)
        L = longs(simulate(d1s[s], p))
        tag = f"{s} {'flt' if reg else 'raw'}"
        print(line(tag, metrics(L)))
    print()

print("########## CLEAN GROUP = AAPL+MSFT+TSLA long-only D1, full cycle + per year\n")
for reg in (0, 200):
    allt = []
    for s in ("AAPL", "MSFT", "TSLA"):
        p = Params(fast=20, slow=80, atr_mult=3.0, cost=COST, regime=reg)
        allt.extend(longs(simulate(d1s[s], p)))
    allt.sort(key=lambda x: x.exit_time)
    print(f"== group  regime-filter={'EMA200' if reg else 'off'} ==")
    print(line("FULL 2020-26", metrics(allt)))
    by = {}
    for t in allt:
        by.setdefault(t.entry_time.year, []).append(t)
    for y in sorted(by):
        print(line(f"  {y}", metrics(by[y])))
    print()

print("########## capital protection through 2022 (per stock, D1, EMA200 filter)\n")
def bh(d):
    c = d["close"].to_numpy(); eq = c/c[0]
    return c[-1]/c[0]-1, float((np.maximum.accumulate(eq)-eq).max()/np.maximum.accumulate(eq).max())
def eng(tr, f=0.01):
    eq=[1.0]
    for t in sorted(tr, key=lambda x: x.exit_time): eq.append(eq[-1]*(1+f*t.R))
    eq=np.array(eq); return eq[-1]-1, float((np.maximum.accumulate(eq)-eq).max()/np.maximum.accumulate(eq).max())
for s in ("AAPL", "MSFT", "TSLA"):
    p = Params(fast=20, slow=80, atr_mult=3.0, cost=COST, regime=200)
    br, bd = bh(d1s[s]); er, ed = eng(longs(simulate(d1s[s], p)))
    print(f"  {s:5s}  buy&hold ret={br*100:+7.1f}% maxDD={bd*100:5.1f}%   |   "
          f"engine ret={er*100:+7.1f}% maxDD={ed*100:5.1f}%")
