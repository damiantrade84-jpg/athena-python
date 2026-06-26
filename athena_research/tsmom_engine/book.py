"""Build the diversified long-only BOOK from the screen's gold-class, low-correlation
qualifiers and stress it. Uniform config (EMA 15/60, 3xATR) across every market — no
per-market tuning. Each trade risks 1R, trades pooled by exit time = equal-risk book.

Headline candidate is the greedy max-diversification gold-class set: take highest edge
quality first, add the next only if its correlation to current members < 0.5 (rejects
the sp500/nasdaq pseudo-replication). Reference books and a deliberate over-stuffed
book included to show diversification payoff vs dilution honestly."""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from engine import Params, simulate, metrics, split_is_oos

DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
COST = 0.0002
P = Params(signal="ema_cross", fast=15, slow=60, atr_mult=3.0, cost=COST)


def load_fut(name): return pd.read_parquet(os.path.join(DAT, f"{name}.parquet"))
def longs(tr): return [t for t in tr if t.direction == 1]
def runbook(syms):
    a = []
    for s in syms: a.extend(longs(simulate(load_fut(s), P)))
    a.sort(key=lambda x: x.exit_time); return a
def row(tag, a):
    m = metrics(a)
    if m.get("N", 0) < 2: return f"  {tag:26s} N={m.get('N',0):4d} (insufficient)"
    _, oos = split_is_oos(a, 0.6); mo = metrics(oos)
    return (f"  {tag:26s} N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  OOS={mo.get('sqn100',float('nan')):5.2f}  "
            f"exp={m['exp']:+.3f}R  q={m['exp']/m['std']:.3f}  PF={m['pf']:4.2f}  maxDD={m['maxdd']:5.1f}R")


BOOKS = {
    "gold alone (baseline)":            ["gold"],
    "gold+crude (prior result)":        ["gold", "crude"],
    "gold+nasdaq (2 cleanest)":         ["gold", "nasdaq"],
    "** gold+brent+nasdaq (greedy) **": ["gold", "brent", "nasdaq"],
    "gold+brent+sp500 (longer eq hist)":["gold", "brent", "sp500"],
    "gold+crude+nasdaq (full-hist en)": ["gold", "crude", "nasdaq"],
    "all4 incl sp500&nasdaq (corr0.87)":["gold", "brent", "sp500", "nasdaq"],
    "over-stuffed +copper+crude+sp500": ["gold", "brent", "nasdaq", "copper", "crude", "sp500"],
}

print("LONG-ONLY books, EMA 15/60, 3xATR, ~25yr daily futures.  '**' = headline candidate.\n")
for tag, syms in BOOKS.items():
    print(row(tag, runbook(syms)))

HEAD = ["gold", "brent", "nasdaq"]
print(f"\n########## Headline book {HEAD}: per-era robustness\n")
a = runbook(HEAD)
for lo, hi, lab in [(2000,2005," dot-com bust"),(2006,2010," 2008 crash"),(2011,2015," gold bear"),
                    (2016,2020," 2020 COVID"),(2021,2026," 2022 bear")]:
    sub = [t for t in a if lo <= t.entry_time.year <= hi]
    m = metrics(sub)
    if m.get("N", 0) < 2: print(f"  {lo}-{hi}{lab:14s} N={m.get('N',0)} (insufficient)"); continue
    print(f"  {lo}-{hi}{lab:14s} N={m['N']:3d}  SQN100={m['sqn100']:5.2f}  exp={m['exp']:+.3f}R  PF={m['pf']:4.2f}")

print(f"\n########## Headline book {HEAD}: cost sensitivity (bps/side -> SQN100)\n")
for mult in (0, 1, 2, 5, 10):
    pc = Params(fast=15, slow=60, atr_mult=3.0, cost=COST * mult / 2)
    aa = []
    for s in HEAD: aa.extend(longs(simulate(load_fut(s), pc)))
    print(f"    {COST*mult/2*1e4:4.1f} bps  ->  SQN100 = {metrics(aa)['sqn100']:.2f}")

print(f"\n########## Headline book {HEAD}: drawdown vs gold-alone (diversification payoff)\n")
for tag, syms in [("gold alone", ["gold"]), ("gold+brent+nasdaq", HEAD)]:
    m = metrics(runbook(syms))
    print(f"  {tag:20s} maxDD={m['maxdd']:5.1f}R  (per 1R risk/trade)")
