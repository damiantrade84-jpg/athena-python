"""Execution lifecycle check: scan -> execute active signal -> mark -> close.
Run: python tests/execute_check.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kimi.engine import Engine

eng = Engine()
eng.scan_once()
sigs = list(eng.signals.values())
print("active signals:", [(s.id, s.symbol, s.direction, s.card.grade, s.card.total) for s in sigs])

if not sigs:
    # market is quiet at default threshold — fabricate a candidate from live data
    # at a relaxed bar so the *execution lifecycle* is still exercised end to end.
    from kimi.config import Config
    from kimi.signals import evaluate
    best = None
    for sym in Config.SYMBOLS:
        sig, _ = evaluate(eng.feed.klines(sym, Config.TF_ENTRY),
                          eng.feed.klines(sym, Config.TF_CONTEXT),
                          eng.feed.klines(sym, Config.TF_BIAS), min_score=60)
        if sig and (best is None or sig.card.total > best.card.total):
            best = sig
    assert best, "no candidate even at relaxed threshold"
    eng.signals[best.id] = best
    sigs = [best]
    print("injected candidate:", best.id, best.symbol, best.direction, best.card.total)

r = eng.execute(sigs[0].id)
print("execute:", r["ok"], r.get("venue"), r.get("error", ""))
assert r["ok"]
print("position:", json.dumps(r["position"], default=str)[:220])

snap = eng.snapshot()
print("open positions:", len(snap["account"]["positions"]))
sym = sigs[0].symbol
px = snap["symbols"][sym]["price"]
fills = eng.paper.mark({sym: px})
print("mark fills:", fills)
rec = eng.close_position(r["position"]["id"])
print("manual close ok:", rec["ok"], "pnl:", rec.get("trade", {}).get("pnl"))
print("trades in log:", len(eng.paper.trades))
print("EXECUTION LIFECYCLE OK")
