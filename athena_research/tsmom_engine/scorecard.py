"""Final scorecard for the headline configuration + cost sensitivity + statistical
significance. Headline = precious metals (XAU+XAG), long-only, canonical EMA 20/80,
ATR14, 3x ATR Chandelier trail. No parameters tuned to maximise SQN."""
from __future__ import annotations

import math
import numpy as np
from engine import GROUPS, Params, run_group, metrics


def longs(trades):
    return [t for t in trades if t.direction == 1]


BASE_COST = 0.0002  # metals, per side (~2bps)

print("=" * 78)
print("HEADLINE  |  group=metals(XAU,XAG)  side=long-only  signal=EMA20/80  trail=3xATR14")
print("=" * 78)

p = Params(signal="ema_cross", fast=20, slow=80, atr_mult=3.0, cost=BASE_COST)
trades, per = run_group(GROUPS["metals"], p)
L = longs(trades)
m = metrics(L)
R = np.array([t.R for t in L])
t_stat = R.mean() / (R.std(ddof=1) / math.sqrt(len(R)))

print(f"  trades N           : {m['N']}")
print(f"  SQN100 (headline)  : {m['sqn100']:.2f}   [target > 2.0]")
print(f"  raw SQN (= t-stat) : {m['sqn_raw']:.2f}   (two-sided p ~ {2*(1-0.5*(1+math.erf(abs(t_stat)/math.sqrt(2)))):.3f})")
print(f"  expectancy / trade : {m['exp']:+.3f} R")
print(f"  mean/std per trade : {m['exp']/m['std']:.3f}   (this is the edge quality; SQN100 = this x sqrt(min(N,100)))")
print(f"  win rate           : {m['win']*100:.1f}%")
print(f"  profit factor      : {m['pf']:.2f}")
print(f"  avg win / avg loss : {m['avg_w']:+.2f}R / {m['avg_l']:+.2f}R")
print(f"  max drawdown       : {m['maxdd']:.1f} R")

print("\n  per-symbol (long-only):")
from engine import simulate, load_candles
for s in GROUPS["metals"]:
    df = load_candles(s, "H4")
    ms = metrics(longs(simulate(df, p)))
    print(f"    {s:9s} N={ms['N']:3d}  SQN100={ms['sqn100']:.2f}  exp={ms['exp']:+.3f}R  win={ms['win']*100:.1f}%  PF={ms['pf']:.2f}")

print("\n  cost sensitivity (per-side bps -> SQN100):")
for mult in (0.5, 1.0, 2.0, 3.0, 5.0):
    pc = Params(signal="ema_cross", fast=20, slow=80, atr_mult=3.0, cost=BASE_COST * mult)
    tr, _ = run_group(GROUPS["metals"], pc)
    print(f"    {BASE_COST*mult*1e4:4.1f} bps/side  ->  SQN100 = {metrics(longs(tr))['sqn100']:.2f}")

print("\n  CONTRAST symmetric long/short (same config):")
print(f"    SQN100 = {metrics(trades)['sqn100']:.2f}  (short side has no edge in 2023-26 bull)")
