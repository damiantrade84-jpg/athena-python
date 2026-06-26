"""Validation of the long-only trend engine on the metals group.
Tests: OOS robustness, parameter-plateau (not a knife-edge), and whether timing
beats buy & hold (is the edge more than bull-market beta?)."""
from __future__ import annotations

import numpy as np
from engine import (GROUPS, COST_PER_SIDE, Params, run_group, metrics,
                    simulate, load_candles, split_is_oos)


def longs(trades):
    return [t for t in trades if t.direction == 1]


def line(tag, m):
    if m.get("N", 0) < 2:
        return f"  {tag:16s} N={m.get('N',0):4d}  (insufficient)"
    return (f"  {tag:16s} N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  rawSQN={m['sqn_raw']:5.2f}  "
            f"exp={m['exp']:+.3f}R  win={m['win']*100:4.1f}%  PF={m['pf']:4.2f}  maxDD={m['maxdd']:4.1f}R")


print("########## A — METALS long-only: regime filter on/off, FULL + IS/OOS\n")
for reg in (0, 200):
    p = Params(signal="ema_cross", cost=COST_PER_SIDE["metals"], regime=reg)
    trades, _ = run_group(GROUPS["metals"], p)
    L = longs(trades)
    is_t, oos_t = split_is_oos(L, 0.6)
    print(f"== METALS long-only  regime={reg or 'off'} ==")
    print(line("FULL", metrics(L)))
    print(line("IN-SAMPLE", metrics(is_t)))
    print(line("OUT-SAMPLE", metrics(oos_t)))
    print()


print("########## B — parameter plateau (long-only metals, FULL-sample SQN100)\n")
print("        atr_mult:   2.5      3.0      4.0")
for fast, slow in [(15, 60), (20, 80), (25, 100), (20, 100), (30, 120)]:
    row = f"  ema {fast:>2d}/{slow:<3d}  "
    for m in (2.5, 3.0, 4.0):
        p = Params(signal="ema_cross", fast=fast, slow=slow, atr_mult=m,
                   cost=COST_PER_SIDE["metals"])
        trades, _ = run_group(GROUPS["metals"], p)
        sq = metrics(longs(trades))["sqn100"]
        row += f"  {sq:6.2f} "
    print(row)
print()


print("########## C — timing vs buy&hold (per metal): is it more than beta?\n")
def buyhold_stats(df):
    c = df["close"].to_numpy()
    ret = c[-1] / c[0] - 1.0
    eq = c / c[0]
    dd = float((np.maximum.accumulate(eq) - eq).max() / np.maximum.accumulate(eq).max())
    return ret, dd

def engine_equity_stats(trades, risk_frac=0.01):
    eq = [1.0]
    for t in sorted(trades, key=lambda x: x.exit_time):
        eq.append(eq[-1] * (1 + risk_frac * t.R))
    eq = np.array(eq)
    ret = eq[-1] - 1.0
    dd = float((np.maximum.accumulate(eq) - eq).max() / np.maximum.accumulate(eq).max())
    return ret, dd

p = Params(signal="ema_cross", cost=COST_PER_SIDE["metals"])
for s in GROUPS["metals"]:
    df = load_candles(s, "H4")
    bh_ret, bh_dd = buyhold_stats(df)
    L = longs(simulate(df, p))
    e_ret, e_dd = engine_equity_stats(L)
    print(f"  {s:9s}  buy&hold: ret={bh_ret*100:+6.1f}%  maxDD={bh_dd*100:4.1f}%   |   "
          f"engine(1%/trade): ret={e_ret*100:+6.1f}%  maxDD={e_dd*100:4.1f}%  "
          f"(return/DD  B&H={bh_ret/bh_dd:4.2f} vs engine={e_ret/e_dd:4.2f})")
print()


print("########## D — diversified long-only trend book (metals+indices+crypto)\n")
book = GROUPS["metals"] + GROUPS["indices"] + GROUPS["crypto"]
allt = []
for s in book:
    df = load_candles(s, "H4")
    if df is None:
        continue
    g = ("metals" if s in GROUPS["metals"] else "indices" if s in GROUPS["indices"] else "crypto")
    allt.extend(longs(simulate(df, Params(signal="ema_cross", cost=COST_PER_SIDE[g]))))
allt.sort(key=lambda x: x.exit_time)
is_t, oos_t = split_is_oos(allt, 0.6)
print(line("FULL", metrics(allt)))
print(line("IN-SAMPLE", metrics(is_t)))
print(line("OUT-SAMPLE", metrics(oos_t)))
