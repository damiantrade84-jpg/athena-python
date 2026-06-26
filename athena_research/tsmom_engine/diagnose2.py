"""(A) Verify whether pre-2023 stock H4 data is usable (splits / granularity).
   (B) Crypto long-only with EMA200 filter across the parameter plateau."""
from __future__ import annotations

import numpy as np
import pandas as pd
from engine import GROUPS, COST_PER_SIDE, Params, run_group, metrics, simulate, load_candles


def longs(tr): return [t for t in tr if t.direction == 1]

print("########## A — stock H4 data quality (bars/yr, biggest single-bar move)\n")
for s in ("NVDA", "TSLA", "AAPL", "XAU_USD"):
    df = load_candles(s, "H4")
    df["yr"] = df["time"].dt.year
    lr = np.log(df["close"] / df["close"].shift(1))
    bpy = df.groupby("yr").size()
    big = lr.abs().sort_values(ascending=False).head(3)
    print(f"== {s} ==  bars/yr: " + " ".join(f"{y}:{n}" for y, n in bpy.items()))
    for idx in big.index:
        print(f"     jump {lr[idx]*100:+6.1f}%  at {df['time'][idx].date()}  "
              f"({df['close'][idx-1]:.2f} -> {df['close'][idx]:.2f})")
    print()

print("########## B — CRYPTO long-only + EMA200 filter, parameter plateau (SQN100)\n")
print("            atr_mult:  2.5     3.0     4.0")
for fast, slow in [(15, 60), (20, 80), (25, 100)]:
    row = f"  ema {fast:>2d}/{slow:<3d}  "
    for am in (2.5, 3.0, 4.0):
        p = Params(fast=fast, slow=slow, atr_mult=am, cost=COST_PER_SIDE["crypto"], regime=200)
        tr, _ = run_group(GROUPS["crypto"], p)
        row += f"  {metrics(longs(tr))['sqn100']:5.2f} "
    print(row)

print("\n  per-symbol (crypto long-only, EMA20/80x3 + EMA200 filter):")
p = Params(cost=COST_PER_SIDE["crypto"], regime=200)
for s in GROUPS["crypto"]:
    df = load_candles(s, "H4")
    ms = metrics(longs(simulate(df, p)))
    print(f"    {s:9s} N={ms['N']:3d}  SQN100={ms['sqn100']:5.2f}  exp={ms['exp']:+.3f}R  "
          f"win={ms['win']*100:4.1f}%  PF={ms['pf']:.2f}")
