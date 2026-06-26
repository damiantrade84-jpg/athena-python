"""Final robustness lock-down for the headline: GOLD long-only trend, 25yr daily.
Confirm it's a parameter PLATEAU (not a spike), positive ACROSS eras, and cost-robust."""
from __future__ import annotations

import os
import math
import numpy as np
import pandas as pd
from engine import Params, simulate, metrics, split_is_oos

DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
COST = 0.0002


def load_fut(name): return pd.read_parquet(os.path.join(DAT, f"{name}.parquet"))
def longs(tr): return [t for t in tr if t.direction == 1]
def runL(syms, p):
    a = []
    for s in syms: a.extend(longs(simulate(load_fut(s), p)))
    a.sort(key=lambda x: x.exit_time); return a
def L(tag, m):
    if m.get("N", 0) < 2: return f"  {tag:22s} N={m.get('N',0):4d} (insufficient)"
    return (f"  {tag:22s} N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  exp={m['exp']:+.3f}R  "
            f"win={m['win']*100:4.1f}%  PF={m['pf']:4.2f}  maxDD={m['maxdd']:5.1f}R")


print("########## A — GOLD long-only signal-speed PLATEAU (full / OOS SQN100)\n")
print(f"  {'ema fast/slow':14s}  {'SQN100 full':>11s}  {'SQN100 OOS':>10s}  {'N':>4s}  {'edge q':>6s}")
for fast, slow in [(8, 32), (10, 40), (12, 48), (15, 60), (20, 80), (25, 100)]:
    p = Params(fast=fast, slow=slow, atr_mult=3.0, cost=COST)
    a = runL(["gold"], p); m = metrics(a)
    _, oos = split_is_oos(a, 0.6); mo = metrics(oos)
    print(f"  {f'{fast}/{slow}':14s}  {m['sqn100']:11.2f}  {mo.get('sqn100',float('nan')):10.2f}  "
          f"{m['N']:4d}  {m['exp']/m['std']:6.3f}")

print("\n########## B — GOLD long-only 15/60 per-era (regime robustness)\n")
p = Params(fast=15, slow=60, atr_mult=3.0, cost=COST)
a = runL(["gold"], p)
for lo, hi, lab in [(2000,2005,""),(2006,2010," 2008 crash"),(2011,2015," GOLD BEAR -44%"),
                    (2016,2020," 2020 COVID"),(2021,2026,"")]:
    sub = [t for t in a if lo <= t.entry_time.year <= hi]
    print(L(f"{lo}-{hi}{lab}", metrics(sub)))

print("\n########## C — GOLD long-only 15/60 cost sensitivity (bps/side -> SQN100)\n")
for mult in (0, 1, 2, 5, 10):
    p = Params(fast=15, slow=60, atr_mult=3.0, cost=COST * mult / 2)
    print(f"    {COST*mult/2*1e4:4.1f} bps  ->  SQN100 = {metrics(runL(['gold'], p))['sqn100']:.2f}")

print("\n########## D — diversified group: GOLD + CRUDE (low-corr trenders), 15/60\n")
p = Params(fast=15, slow=60, atr_mult=3.0, cost=COST)
a = runL(["gold", "crude"], p); _, oos = split_is_oos(a, 0.6)
print(L("gold+crude FULL", metrics(a)))
print(L("gold+crude OOS", metrics(oos)))
# correlation of the two return streams (daily)
g = load_fut("gold").set_index("time")["close"].pct_change()
c = load_fut("crude").set_index("time")["close"].pct_change()
corr = pd.concat([g, c], axis=1).dropna().corr().iloc[0, 1]
print(f"  (gold/crude daily-return correlation = {corr:.2f})")
