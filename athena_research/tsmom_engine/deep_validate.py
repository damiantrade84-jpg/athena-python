"""Deep multi-regime validation on 25+ yr daily commodity futures (yfinance).
Same engine as engine.py. Re-tests LONG vs SHORT vs SYMMETRIC now that real bear
regimes exist, with per-era robustness and the 2011-2015 gold bear capital test.

NOTE: independent data source (yfinance futures D1) from the frozen MT5 H4 study;
not directly comparable to the 2.02 there — this is a fresh, deeper test."""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from engine import Params, simulate, metrics, split_is_oos

DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
COST = 0.0002  # ~2bps/side, realistic for liquid futures

PRECIOUS = ["gold", "silver", "platinum", "palladium"]
COMMOD = ["gold", "silver", "copper", "crude", "platinum", "palladium", "natgas", "brent"]


def load_fut(name):
    return pd.read_parquet(os.path.join(DAT, f"{name}.parquet"))


def longs(tr):  return [t for t in tr if t.direction == 1]
def shorts(tr): return [t for t in tr if t.direction == -1]


def run(syms, p, side="all"):
    allt = []
    for s in syms:
        tr = simulate(load_fut(s), p)
        if side == "long":  tr = longs(tr)
        elif side == "short": tr = shorts(tr)
        allt.extend(tr)
    allt.sort(key=lambda x: x.exit_time)
    return allt


def line(tag, m):
    if m.get("N", 0) < 2:
        return f"  {tag:20s} N={m.get('N',0):4d}  (insufficient)"
    return (f"  {tag:20s} N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  rawSQN={m['sqn_raw']:6.2f}  "
            f"exp={m['exp']:+.3f}R  win={m['win']*100:4.1f}%  PF={m['pf']:4.2f}  maxDD={m['maxdd']:5.1f}R")


p = Params(signal="ema_cross", fast=20, slow=80, atr_mult=3.0, cost=COST)

print("########## A — per-symbol, LONG vs SHORT vs SYMMETRIC (25yr D1)\n")
for s in COMMOD:
    tr = simulate(load_fut(s), p)
    L, S, A = metrics(longs(tr)), metrics(shorts(tr)), metrics(tr)
    print(f"  {s:10s} LONG SQN100={L['sqn100']:5.2f} exp={L['exp']:+.3f}  |  "
          f"SHORT SQN100={S['sqn100']:5.2f} exp={S['exp']:+.3f}  |  "
          f"SYMM SQN100={A['sqn100']:5.2f} exp={A['exp']:+.3f}")

print("\n########## B — pooled groups, SYMMETRIC (long+short) full sample\n")
for gname, syms in [("metals(au,ag)", ["gold", "silver"]),
                    ("precious(4)", PRECIOUS),
                    ("commodity(8)", COMMOD)]:
    print(line(f"{gname} SYMM", metrics(run(syms, p))))
    print(line(f"{gname} LONG", metrics(run(syms, p, "long"))))
    print(line(f"{gname} SHORT", metrics(run(syms, p, "short"))))
    print()

print("########## C — per-era robustness, SYMMETRIC commodity(8) basket\n")
allt = run(COMMOD, p)
eras = [(2000, 2005), (2006, 2010), (2011, 2015), (2016, 2020), (2021, 2026)]
labels = {(2011, 2015): " <- incl. gold bear", (2006, 2010): " <- incl. 2008 crash",
          (2016, 2020): " <- incl. 2020 COVID"}
for a, b in eras:
    sub = [t for t in allt if a <= t.entry_time.year <= b]
    print(line(f"{a}-{b}{labels.get((a,b),'')}", metrics(sub)))

print("\n########## D — IS/OOS split (commodity(8) SYMMETRIC)\n")
is_t, oos_t = split_is_oos(allt, 0.6)
print(line("FULL", metrics(allt)))
print(line("IN-SAMPLE", metrics(is_t)))
print(line("OUT-SAMPLE", metrics(oos_t)))

print("\n########## E — 2011-2015 gold bear: capital protection (gold only)\n")
def bh(d):
    c = d["close"].to_numpy(); eq = c/c[0]
    return c[-1]/c[0]-1, float((np.maximum.accumulate(eq)-eq).max()/np.maximum.accumulate(eq).max())
def eng(tr, f=0.01):
    eq=[1.0]
    for t in sorted(tr, key=lambda x: x.exit_time): eq.append(eq[-1]*(1+f*t.R))
    eq=np.array(eq); return eq[-1]-1, float((np.maximum.accumulate(eq)-eq).max()/np.maximum.accumulate(eq).max())
gd = load_fut("gold")
bear = gd[(gd["time"].dt.year >= 2011) & (gd["time"].dt.year <= 2015)].reset_index(drop=True)
br, bd = bh(bear)
tr = simulate(bear, p)
er, ed = eng(tr); erL, edL = eng(longs(tr))
print(f"  gold 2011-2015  buy&hold ret={br*100:+6.1f}% maxDD={bd*100:4.1f}%")
print(f"  engine symmetric ret={er*100:+6.1f}% maxDD={ed*100:4.1f}%   |   long-only ret={erL*100:+6.1f}% maxDD={edL*100:4.1f}%")
