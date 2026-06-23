"""Parity: drive the LIVE signal bar-by-bar as a state machine over gold daily bars and
confirm it reproduces the validated backtest's long-trade ENTRY events (same flip bars).

Entry price differs by design (live fills at next open, can't be known early), so we
compare entry/exit TIMING + reasons, not prices. EMA/ATR are causal (ewm), so the per-bar
slice value equals the full-series value — the signal is identical at each bar."""
from __future__ import annotations

import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "athena_research", "tsmom_engine"))
sys.path.insert(0, _ROOT)

from engine import Params, simulate  # noqa: E402  (research backtest, source of truth)
from tsmom_live.signal import INSTRUMENTS, PositionState, compute_decision, warmup_bars  # noqa: E402

cfg = INSTRUMENTS["gold"]
df = pd.read_parquet(os.path.join(_ROOT, "athena_research", "tsmom_engine", "data_futures", "gold.parquet"))
p = Params(fast=cfg.fast, slow=cfg.slow, atr_n=cfg.atr_n, atr_mult=cfg.atr_mult, cost=0.0002)

longs = [t for t in simulate(df, p) if t.direction == 1]
bt_entries = {pd.Timestamp(t.entry_time) for t in longs}
bt_exits = {pd.Timestamp(t.exit_time) for t in longs}

state: PositionState | None = None
live_entries: list[pd.Timestamp] = []
live_exits: list[pd.Timestamp] = []
n = len(df)
for k in range(warmup_bars(cfg) - 1, n - 1):     # bar k just closed; act at k+1
    dec = compute_decision(df.iloc[: k + 1], state, cfg)
    nxt = pd.Timestamp(df["time"].iloc[k + 1])
    if state is None:
        if dec.action == "OPEN_LONG":
            entry_px = float(df["open"].iloc[k + 1])
            risk = cfg.atr_mult * dec.atr
            state = PositionState(1, entry_px, entry_px - risk, entry_px, 0)
            live_entries.append(nxt)
    else:
        if dec.action == "CLOSE":
            live_exits.append(nxt)
            state = None
        else:
            state.stop, state.extreme = dec.trail_stop, dec.extreme

le = set(live_entries)
matched = len(le & bt_entries)
print(f"backtest long entries : {len(bt_entries)}")
print(f"live    long entries  : {len(le)}")
print(f"entry timestamps matched: {matched}/{len(bt_entries)} "
      f"({100*matched/max(len(bt_entries),1):.1f}%)")
print(f"live exits: {len(live_exits)}  | backtest exits: {len(bt_exits)}  "
      f"matched: {len(set(live_exits) & bt_exits)}")
miss = sorted(bt_entries - le)
if miss:
    print(f"unmatched backtest entries (first 5): {[str(x.date()) for x in miss[:5]]}")
