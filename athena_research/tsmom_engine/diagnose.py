"""Diagnostics for the TSMOM engine: where the edge lives, is it symmetric, what
exits dominate, and does a higher-timeframe regime filter help (IS vs OOS)."""
from __future__ import annotations

from engine import GROUPS, COST_PER_SIDE, Params, run_group, metrics, simulate, load_candles, split_is_oos


def sub(trades, pred):
    return [t for t in trades if pred(t)]


def line(tag, m):
    if m.get("N", 0) < 2:
        return f"  {tag:16s} N={m.get('N',0):4d}  (insufficient)"
    return (f"  {tag:16s} N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  exp={m['exp']:+.3f}R  "
            f"win={m['win']*100:4.1f}%  PF={m['pf']:4.2f}  avgW={m['avg_w']:+.2f} avgL={m['avg_l']:+.2f}")


def exit_mix(trades):
    from collections import Counter
    c = Counter(t.reason for t in trades)
    tot = max(len(trades), 1)
    return "  ".join(f"{k}={v}({100*v/tot:.0f}%)" for k, v in c.most_common())


print("############ PART A — baseline ema_cross 20/80, long/short symmetry & exit mix\n")
for g in ("metals", "commodity", "crypto", "indices"):
    p = Params(signal="ema_cross", cost=COST_PER_SIDE[g])
    trades, _ = run_group(GROUPS[g], p)
    print(f"== {g.upper()} ==")
    print(line("ALL", metrics(trades)))
    print(line("LONG only", metrics(sub(trades, lambda t: t.direction == 1))))
    print(line("SHORT only", metrics(sub(trades, lambda t: t.direction == -1))))
    print(f"  exits: {exit_mix(trades)}")
    print()

print("\n############ PART B — METALS: regime filter (EMA200) on/off, full + IS/OOS\n")
for reg in (0, 200):
    p = Params(signal="ema_cross", cost=COST_PER_SIDE["metals"], regime=reg)
    trades, per = run_group(GROUPS["metals"], p)
    is_t, oos_t = split_is_oos(trades, 0.6)
    tag = f"regime={reg or 'off'}"
    print(f"== METALS {tag} ==")
    print(line("FULL", metrics(trades)))
    print(line("IN-SAMPLE", metrics(is_t)))
    print(line("OUT-SAMPLE", metrics(oos_t)))
    for s in GROUPS["metals"]:
        df = load_candles(s, "H4")
        print(line(s, metrics(simulate(df, p))))
    print()
