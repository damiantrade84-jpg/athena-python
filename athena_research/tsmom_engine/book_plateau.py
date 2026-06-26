"""Confirm the headline book (gold+brent+nasdaq, long-only) clears SQN>2.0 across a
PLATEAU of signal speeds and trail widths, not just at 15/60 -- i.e. it is not a
knife-edge. Also re-derive vs the 2-market gold+nasdaq for comparison."""
from __future__ import annotations

import os
import pandas as pd
from engine import Params, simulate, metrics, split_is_oos

DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
COST = 0.0002
BOOK = ["gold", "brent", "nasdaq"]


def load_fut(name): return pd.read_parquet(os.path.join(DAT, f"{name}.parquet"))
def longs(tr): return [t for t in tr if t.direction == 1]
def run(syms, p):
    a = []
    for s in syms: a.extend(longs(simulate(load_fut(s), p)))
    a.sort(key=lambda x: x.exit_time); return a


print(f"Book {BOOK}, long-only.  SQN100 full / OOS across the speed x trail plateau.\n")
print(f"  {'fast/slow':10s}" + "".join(f"  {f'{m}xATR':>12s}" for m in (2.5, 3.0, 3.5)))
for fast, slow in [(8, 32), (10, 40), (12, 48), (15, 60), (20, 80)]:
    line = f"  {f'{fast}/{slow}':10s}"
    for mult in (2.5, 3.0, 3.5):
        p = Params(fast=fast, slow=slow, atr_mult=mult, cost=COST)
        a = run(BOOK, p); _, oos = split_is_oos(a, 0.6)
        line += f"  {metrics(a)['sqn100']:5.2f}/{metrics(oos).get('sqn100',float('nan')):5.2f}"
    print(line)
print("\nEach cell = SQN100(full)/SQN100(OOS).  Plateau (not a spike) => robust.")
