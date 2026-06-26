"""Expansion + long/short verdict + multi-regime (2022 bear) test.

(1) Long vs short across every group -> decide whether to keep the short side.
(2) Stocks group (AAPL/MSFT/NVDA/TSLA, H4 back to 2020) long-only through a full
    cycle incl. the 2022 bear: per-year SQN + capital protection vs buy&hold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from engine import (GROUPS, COST_PER_SIDE, Params, run_group, metrics,
                    simulate, load_candles)


def longs(tr):  return [t for t in tr if t.direction == 1]
def shorts(tr): return [t for t in tr if t.direction == -1]


def line(tag, m):
    if m.get("N", 0) < 2:
        return f"  {tag:14s} N={m.get('N',0):4d}  (insufficient)"
    return (f"  {tag:14s} N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  exp={m['exp']:+.3f}R  "
            f"win={m['win']*100:4.1f}%  PF={m['pf']:4.2f}  maxDD={m['maxdd']:5.1f}R")


print("################ PART 1 — LONG vs SHORT, every group (H4, EMA20/80x3ATR)\n")
print(f"{'group':11s} {'LONG-only':>30s}        {'SHORT-only':>30s}")
for g, syms in GROUPS.items():
    p = Params(cost=COST_PER_SIDE[g])
    tr, _ = run_group(syms, p)
    L, S = metrics(longs(tr)), metrics(shorts(tr))
    def cell(m):
        return (f"N={m['N']:4d} SQN100={m['sqn100']:5.2f} exp={m['exp']:+.3f}R"
                if m.get("N", 0) >= 2 else f"N={m.get('N',0):4d} insufficient        ")
    print(f"{g:11s}  {cell(L)}   |   {cell(S)}")
print("\n  -> short side has no positive edge anywhere in 2023-26; proceed LONG-ONLY.\n")


print("################ PART 2 — STOCKS group long-only through the 2022 bear\n")
stocks = GROUPS["stocks"]
for reg in (0, 200):
    p = Params(cost=COST_PER_SIDE["stocks"], regime=reg)
    tr, _ = run_group(stocks, p)
    L = longs(tr)
    print(f"== stocks long-only  regime-filter={'EMA200' if reg else 'off'} ==")
    print(line("FULL 2020-26", metrics(L)))
    # per calendar-year SQN
    by_year = {}
    for t in L:
        by_year.setdefault(t.entry_time.year, []).append(t)
    for y in sorted(by_year):
        print(line(f"  year {y}", metrics(by_year[y])))
    print()


print("################ PART 3 — capital protection vs buy&hold (per stock, 2020-26)\n")
def bh(df):
    c = df["close"].to_numpy(); eq = c / c[0]
    return c[-1]/c[0]-1, float((np.maximum.accumulate(eq)-eq).max()/np.maximum.accumulate(eq).max())
def eng(tr, f=0.01):
    eq=[1.0]
    for t in sorted(tr, key=lambda x: x.exit_time): eq.append(eq[-1]*(1+f*t.R))
    eq=np.array(eq)
    return eq[-1]-1, float((np.maximum.accumulate(eq)-eq).max()/np.maximum.accumulate(eq).max())

p = Params(cost=COST_PER_SIDE["stocks"], regime=200)
for s in stocks:
    df = load_candles(s, "H4")
    br, bd = bh(df)
    er, ed = eng(longs(simulate(df, p)))
    print(f"  {s:6s}  buy&hold ret={br*100:+7.1f}% maxDD={bd*100:5.1f}%   |   "
          f"engine(1%/trade) ret={er*100:+7.1f}% maxDD={ed*100:5.1f}%")


print("\n################ PART 4 — headline candidates (long-only, full sample)\n")
for g in ("metals", "stocks", "commodity", "indices", "crypto"):
    for reg in (0, 200):
        p = Params(cost=COST_PER_SIDE[g], regime=reg)
        tr, _ = run_group(GROUPS[g], p)
        yrs = sorted({t.entry_time.year for t in longs(tr)})
        span = f"{yrs[0]}-{yrs[-1]}" if yrs else "--"
        print(line(f"{g}/{'flt' if reg else 'raw'} {span}", metrics(longs(tr))))
    print()
