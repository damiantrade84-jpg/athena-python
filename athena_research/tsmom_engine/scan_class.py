"""FOREX / CRYPTO / COMMODITY deep dive, each tested the RIGHT way:
  - commodity: long-only primary (positive drift); symmetric shown to prove short adds nothing.
  - forex: SYMMETRIC primary (currencies have no drift; long-only is mis-specified).
  - crypto: BOTH (strong drift but violent two-sided swings); deep 2014/2017-> incl. 2018 & 2022 bears.
Realistic per-class costs. Same engine, uniform 15/60x3ATR (no per-market tuning); plateau + OOS gate."""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from engine import Params, simulate, metrics, split_is_oos

DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
COST = {"commodity": 0.0002, "forex": 0.0001, "crypto": 0.0006}  # per side
SPEEDS = [(8, 32), (10, 40), (12, 48), (15, 60), (20, 80)]

CLASSES = {
    "commodity": ["gold", "silver", "copper", "platinum", "palladium", "crude", "brent", "natgas"],
    "forex":     ["eur", "jpy", "gbp", "aud", "cad", "chf"],
    "crypto":    ["btc", "ltc", "eth", "xrp", "bnb", "ada", "sol", "doge"],
}


def load_fut(name): return pd.read_parquet(os.path.join(DAT, f"{name}.parquet"))
def side(tr, d): return [t for t in tr if (d == "all" or (d == "long" and t.direction == 1) or (d == "short" and t.direction == -1))]
def run(syms, p, d):
    a = []
    for s in syms: a.extend(side(simulate(load_fut(s), p), d))
    a.sort(key=lambda x: x.exit_time); return a
def sqn(syms, p, d): return metrics(run(syms, p, d)).get("sqn100", float("nan"))


for cls, syms in CLASSES.items():
    c = COST[cls]
    P = Params(fast=15, slow=60, atr_mult=3.0, cost=c)
    print(f"\n{'='*78}\n=== {cls.upper()}  ({c*1e4:.0f} bps/side, EMA 15/60 x 3ATR) ===\n")
    print(f"  {'market':9s} {'N(symm)':>7s} {'LONG':>6s} {'SHORT':>6s} {'SYMM':>6s}")
    for nm in syms:
        tr = simulate(load_fut(nm), P)
        mS = metrics(side(tr, "all"))
        print(f"  {nm:9s} {mS.get('N',0):7d} {metrics(side(tr,'long')).get('sqn100',float('nan')):6.2f} "
              f"{metrics(side(tr,'short')).get('sqn100',float('nan')):6.2f} {mS.get('sqn100',float('nan')):6.2f}")

    # pooled books, in the directions that matter for this class
    dirs = {"commodity": ["long", "all"], "forex": ["all", "long"], "crypto": ["long", "all"]}[cls]
    print(f"\n  pooled book ({len(syms)} mkts):")
    for d in dirs:
        a = run(syms, P, d); m = metrics(a); _, o = split_is_oos(a, 0.6)
        print(f"    {d:5s}  N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  OOS={metrics(o).get('sqn100',float('nan')):5.2f}  "
              f"exp={m['exp']:+.3f}R  q={m['exp']/m['std']:.3f}  PF={m['pf']:4.2f}  maxDD={m['maxdd']:5.1f}R")


# ---- deeper look at the most promising standalone in each class ----
print(f"\n{'='*78}\n=== STANDALONE PLATEAUS (best-direction) ===\n")
for nm, cls, d in [("btc", "crypto", "long"), ("btc", "crypto", "all"), ("gold", "commodity", "long")]:
    c = COST[cls]
    line = f"  {nm:5s} {cls:9s} {d:5s}: "
    for fast, slow in SPEEDS:
        p = Params(fast=fast, slow=slow, atr_mult=3.0, cost=c)
        a = run([nm], p, d); _, o = split_is_oos(a, 0.6)
        line += f"{f'{fast}/{slow}'}={metrics(a)['sqn100']:.2f}/{metrics(o).get('sqn100',float('nan')):.2f}  "
    print(line)

# crypto internal correlation (is a 'crypto book' real breadth or BTC repeated?)
print(f"\n  crypto daily-return correlation to BTC (independence check):")
btc = load_fut("btc").set_index("time")["close"].pct_change()
for nm in ["eth", "ltc", "xrp", "bnb", "ada", "sol", "doge"]:
    r = load_fut(nm).set_index("time")["close"].pct_change()
    corr = pd.concat([btc, r], axis=1).dropna().corr().iloc[0, 1]
    print(f"    btc~{nm:5s} {corr:+.2f}")

# per-era for crypto BTC long-only (regime robustness through the two bears)
print(f"\n  BTC long-only 15/60 per-era (regime robustness):")
a = run(["btc"], Params(fast=15, slow=60, atr_mult=3.0, cost=COST["crypto"]), "long")
for lo, hi, lab in [(2014,2017," 2017 bull"),(2018,2019," 2018 -84% bear"),(2020,2021," 2021 bull"),
                    (2022,2022," 2022 -77% bear"),(2023,2026," recovery")]:
    sub = [t for t in a if lo <= t.entry_time.year <= hi]
    m = metrics(sub)
    tag = f"{lo}-{hi}{lab}"
    print(f"    {tag:22s} N={m.get('N',0):3d}  SQN100={m.get('sqn100',float('nan')):5.2f}  exp={m.get('exp',float('nan')):+.3f}R  PF={m.get('pf',float('nan')):4.2f}")
