"""Standalone screen of the whole 30-market universe, LONG-ONLY, on ONE fixed config
(no per-market tuning = no overfit). Goal: find markets that INDEPENDENTLY carry a
gold-comparable trend edge, so a diversified book raises N without diluting quality.

Key column is edge quality = exp/std (mean R per unit of R-dispersion). Because a
broad book pushes N past 100, SQN100 saturates at ~edge_quality x 10, so the book
clears 2.0 iff its members' AVERAGE edge quality stays >= ~0.20 (gold's is ~0.29).
Correlation matrix included to distinguish genuine breadth from the same bet repeated."""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from engine import Params, simulate, metrics, split_is_oos

DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
COST = 0.0002

SECTOR = {
    "gold": "metal", "silver": "metal", "copper": "metal", "platinum": "metal", "palladium": "metal",
    "crude": "energy", "brent": "energy", "natgas": "energy",
    "corn": "ag", "soybean": "ag", "wheat": "ag", "coffee": "ag", "sugar": "ag", "cotton": "ag", "cocoa": "ag", "cattle": "ag",
    "sp500": "equity", "nasdaq": "equity", "dow": "equity", "russell": "equity",
    "tnote10": "rate", "tbond30": "rate", "tnote5": "rate", "tnote2": "rate",
    "eur": "fx", "jpy": "fx", "gbp": "fx", "aud": "fx", "cad": "fx", "chf": "fx",
}
NAMES = list(SECTOR.keys())


def load_fut(name): return pd.read_parquet(os.path.join(DAT, f"{name}.parquet"))
def longs(tr): return [t for t in tr if t.direction == 1]


# ONE canonical config applied uniformly. 15/60 = the gold headline plateau center.
P = Params(signal="ema_cross", fast=15, slow=60, atr_mult=3.0, cost=COST)

print("LONG-ONLY, EMA 15/60, 3xATR, 25yr daily futures.  Ranked by edge quality (exp/std).\n")
print(f"{'sector':7s} {'market':10s} {'N':>4s} {'tr/yr':>5s} {'SQN100':>6s} {'OOS':>5s} "
      f"{'exp':>7s} {'q=e/s':>6s} {'win':>5s} {'PF':>5s}")
rows = []
for nm in NAMES:
    df = load_fut(nm)
    a = longs(simulate(df, P))
    m = metrics(a)
    if m.get("N", 0) < 5:
        continue
    _, oos = split_is_oos(a, 0.6); mo = metrics(oos)
    yrs = (df["time"].max() - df["time"].min()).days / 365.25
    q = m["exp"] / m["std"]
    rows.append((SECTOR[nm], nm, m["N"], m["N"]/yrs, m["sqn100"], mo.get("sqn100", float("nan")),
                 m["exp"], q, m["win"], m["pf"]))

for sec, nm, N, tryr, sqn, oos, exp, q, win, pf in sorted(rows, key=lambda r: -r[7]):
    flag = "  <= gold-class" if q >= 0.20 else ("  <= weak" if q < 0.10 else "")
    print(f"{sec:7s} {nm:10s} {N:4d} {tryr:5.1f} {sqn:6.2f} {oos:5.2f} "
          f"{exp:+7.3f} {q:6.3f} {win*100:4.0f}% {pf:5.2f}{flag}")

# ---- correlation of daily returns among the gold-class qualifiers ----
qual = [nm for (_, nm, _, _, _, _, _, q, _, _) in rows if q >= 0.20]
print(f"\nGold-class qualifiers (edge q >= 0.20): {len(qual)} -> {qual}\n")
if len(qual) >= 2:
    rets = pd.DataFrame({nm: load_fut(nm).set_index("time")["close"].pct_change() for nm in qual})
    corr = rets.corr()
    print("Daily-return correlation among qualifiers:\n")
    print("        " + " ".join(f"{nm[:6]:>6s}" for nm in qual))
    for nm in qual:
        print(f"{nm[:7]:7s} " + " ".join(f"{corr.loc[nm, o]:+6.2f}" for o in qual))
