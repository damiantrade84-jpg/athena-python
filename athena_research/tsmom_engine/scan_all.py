"""Answer 'what ELSE clears SQN>2.0?' honestly, two ways, long-only:

PART 1 — every market across the WHOLE speed plateau (8/32..20/80). A market only counts
as a real qualifier if it clears 2.0 on the FULL sample across most of the plateau AND its
OOS also clears 2.0 (same bar gold/the book had to pass) — not a single lucky-config spike.

PART 2 — every SECTOR pooled as a long-only group at the fixed 15/60, to see which whole
groups (metals/energy/ag/equity/rate/fx) clear 2.0 vs dilute.

No per-market tuning beyond the shared plateau; OOS = last 40% of trades by entry date."""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from engine import Params, simulate, metrics, split_is_oos

DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
COST = 0.0002
SPEEDS = [(8, 32), (10, 40), (12, 48), (15, 60), (20, 80)]

SECTORS = {
    "metal":  ["gold", "silver", "copper", "platinum", "palladium"],
    "energy": ["crude", "brent", "natgas"],
    "ag":     ["corn", "soybean", "wheat", "coffee", "sugar", "cotton", "cocoa", "cattle"],
    "equity": ["sp500", "nasdaq", "dow", "russell"],
    "rate":   ["tnote10", "tbond30", "tnote5", "tnote2"],
    "fx":     ["eur", "jpy", "gbp", "aud", "cad", "chf"],
}
ALL = [m for v in SECTORS.values() for m in v]


def load_fut(name): return pd.read_parquet(os.path.join(DAT, f"{name}.parquet"))
def longs(tr): return [t for t in tr if t.direction == 1]
def run(syms, p):
    a = []
    for s in syms: a.extend(longs(simulate(load_fut(s), p)))
    a.sort(key=lambda x: x.exit_time); return a


print("PART 1 — single markets, LONG-ONLY, SQN100(full) across the speed plateau\n")
print(f"  {'market':10s} " + " ".join(f"{f'{a}/{b}':>6s}" for a, b in SPEEDS) +
      f"  {'#>=2':>4s} {'bestOOS':>7s}  verdict")
res = []
for nm in ALL:
    fulls, ooss = [], []
    for fast, slow in SPEEDS:
        a = run([nm], Params(fast=fast, slow=slow, atr_mult=3.0, cost=COST))
        m = metrics(a); _, o = split_is_oos(a, 0.6)
        fulls.append(m.get("sqn100", float("nan")))
        ooss.append(metrics(o).get("sqn100", float("nan")))
    nfull = sum(1 for x in fulls if x >= 2.0)
    best_oos = np.nanmax(ooss)
    # robust if clears 2.0 full across most of the plateau AND OOS clears 2.0 somewhere sensible
    oos_at_full = [ooss[i] for i in range(len(SPEEDS)) if fulls[i] >= 2.0]
    robust = nfull >= 3 and sum(1 for x in oos_at_full if x >= 2.0) >= 2
    verdict = "ROBUST >=2.0" if robust else ("marginal" if nfull >= 1 else "no")
    res.append((nm, fulls, nfull, best_oos, verdict))

for nm, fulls, nfull, best_oos, verdict in sorted(res, key=lambda r: (-r[2], -max(r[1]))):
    cells = " ".join(f"{x:6.2f}" for x in fulls)
    print(f"  {nm:10s} {cells}  {nfull:4d} {best_oos:7.2f}  {verdict}")

print("\nPART 2 — whole SECTORS pooled, LONG-ONLY, EMA 15/60 x 3ATR\n")
print(f"  {'sector':8s} {'N':>4s} {'SQN100':>6s} {'OOS':>5s} {'q=e/s':>6s} {'PF':>5s}  verdict")
P = Params(fast=15, slow=60, atr_mult=3.0, cost=COST)
for sec, syms in SECTORS.items():
    a = run(syms, P); m = metrics(a); _, o = split_is_oos(a, 0.6); mo = metrics(o)
    q = m["exp"] / m["std"]
    v = "clears 2.0" if (m["sqn100"] >= 2.0 and mo.get("sqn100", 0) >= 2.0) else \
        ("full only" if m["sqn100"] >= 2.0 else "dilutes")
    print(f"  {sec:8s} {m['N']:4d} {m['sqn100']:6.2f} {mo.get('sqn100',float('nan')):5.2f} "
          f"{q:6.3f} {m['pf']:5.2f}  {v}")
