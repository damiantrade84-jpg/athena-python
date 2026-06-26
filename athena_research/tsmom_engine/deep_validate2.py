"""Can MORE TRADES at stable edge quality push long-only SQN100 >= 2.0 robustly?
Small FIXED grid (3 signal speeds x 4 groups), long-only, reported in FULL (no
cherry-pick). A config only counts as robust if it clears 2.0 in OOS too."""
from __future__ import annotations

import os
import pandas as pd
from engine import Params, simulate, metrics, split_is_oos

DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
COST = 0.0002

GROUPS = {
    "gold":        ["gold"],
    "precious4":   ["gold", "silver", "platinum", "palladium"],
    "energy2":     ["crude", "brent"],
    "commodity8":  ["gold", "silver", "copper", "crude", "platinum", "palladium", "natgas", "brent"],
}


def load_fut(name): return pd.read_parquet(os.path.join(DAT, f"{name}.parquet"))
def longs(tr): return [t for t in tr if t.direction == 1]


def run_long(syms, p):
    allt = []
    for s in syms:
        allt.extend(longs(simulate(load_fut(s), p)))
    allt.sort(key=lambda x: x.exit_time)
    return allt


print("Long-only, 25yr futures.  Each cell:  SQN100(full) / SQN100(OOS) / N / exp_per_std\n")
print(f"{'group':12s} " + "  ".join(f"{f'ema{a}/{b}':>22s}" for a, b in [(10,40),(15,60),(20,80)]))
for gname, syms in GROUPS.items():
    row = f"{gname:12s} "
    for fast, slow in [(10, 40), (15, 60), (20, 80)]:
        p = Params(signal="ema_cross", fast=fast, slow=slow, atr_mult=3.0, cost=COST)
        allt = run_long(syms, p)
        m = metrics(allt)
        _, oos = split_is_oos(allt, 0.6)
        mo = metrics(oos)
        q = m["exp"] / m["std"] if m.get("N", 0) >= 2 else float("nan")
        cell = f"{m['sqn100']:.2f}/{mo.get('sqn100', float('nan')):.2f}/{m['N']}/{q:.3f}"
        row += f"  {cell:>22s}"
    print(row)

print("\nNote: SQN100(OOS) uses the last 40% of trades by entry date.")
print("'Robust >=2.0' requires BOTH full and OOS >= 2.0 across the diversified groups.")
