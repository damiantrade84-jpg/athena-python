"""Deterministic live TSMOM decision — byte-for-byte the same math as the validated
backtest (athena_research/tsmom_engine/engine.py): EMA(fast)-EMA(slow) regime, 3xATR
Chandelier trail, long-only. No look-ahead: decisions use only CLOSED bars.

A live decision is computed after each daily bar closes:
  - flat + fresh long regime-flip  -> OPEN_LONG  (broker SL = entry - 3*ATR; far synthetic TP)
  - long + regime flips to short   -> CLOSE (flip)
  - long + prior close trail broken -> CLOSE (stop)   [belt-and-suspenders; broker SL also set]
  - long otherwise                 -> HOLD  (ratchet trail stop up)
Short flips are ignored — the validated edge is long-only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TsmomConfig:
    instrument: str = "gold"          # internal name
    broker_symbol: str = "XAUUSD"     # MT5 symbol
    display: str = "XAU/USD"          # athena display
    asset_type: str = "commodity"     # risk_engine / executor 'type'
    venue: str = "mt5"
    fast: int = 15
    slow: int = 60
    atr_n: int = 14
    atr_mult: float = 3.0             # Chandelier multiple = initial risk unit
    rr_target: float = 3.0            # synthetic TP (real exit is the trail) -> clears RR floor


# Validated deployment set (long-only). Only gold is wired for the first increment;
# nasdaq/brent require broker-symbol verification before they are enabled.
INSTRUMENTS: dict[str, TsmomConfig] = {
    "gold": TsmomConfig(instrument="gold", broker_symbol="XAUUSD", display="XAU/USD",
                        asset_type="commodity", fast=15, slow=60, atr_mult=3.0),
}


@dataclass
class PositionState:
    direction: int          # +1 long (only long supported)
    entry: float
    stop: float             # current trail stop
    extreme: float          # highest close since entry
    entry_time_ms: int
    ticket: int | None = None
    volume: float | None = None


@dataclass(frozen=True)
class LiveDecision:
    action: str             # OPEN_LONG | HOLD | CLOSE | NONE
    reason: str
    direction: str          # LONG | NONE
    entry_ref: float
    sl: float
    tp1: float
    tp2: float
    trail_stop: float
    extreme: float
    atr: float
    ema_fast: float
    ema_slow: float
    bar_time_ms: int
    instrument: str


# --- indicators: identical to engine.py (parity is mandatory) -----------------
def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _atr_wilder(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def _desired(df: pd.DataFrame, cfg: TsmomConfig) -> np.ndarray:
    c = df["close"]
    d = np.sign(_ema(c, cfg.fast) - _ema(c, cfg.slow))
    return pd.Series(d).replace(0.0, np.nan).ffill().fillna(0.0).to_numpy().astype(float)


def warmup_bars(cfg: TsmomConfig) -> int:
    return max(cfg.slow, cfg.atr_n) + 2


def compute_decision(
    df: pd.DataFrame,
    state: PositionState | None,
    cfg: TsmomConfig,
    *,
    now_ms: int | None = None,
) -> LiveDecision:
    """`df`: closed daily OHLC bars (oldest first, newest last, forming bar excluded)."""
    now_ms = now_ms or int(time.time() * 1000)
    n = len(df)

    def _flat(reason: str) -> LiveDecision:
        return LiveDecision("NONE", reason, "NONE", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                            0.0, 0.0, 0.0, now_ms, cfg.instrument)

    if n < warmup_bars(cfg):
        return _flat("insufficient_bars")

    des = _desired(df, cfg)
    atr = _atr_wilder(df, cfg.atr_n).to_numpy()
    ef = _ema(df["close"], cfg.fast).to_numpy()
    es = _ema(df["close"], cfg.slow).to_numpy()
    close = df["close"].to_numpy()
    low = df["low"].to_numpy()

    des_now, des_prev = des[-1], des[-2]
    flip = des_now != des_prev
    close_now, low_now, atr_now = float(close[-1]), float(low[-1]), float(atr[-1])
    ef_now, es_now = float(ef[-1]), float(es[-1])
    bar_ms = int(pd.Timestamp(df["time"].iloc[-1]).value // 1_000_000) if "time" in df else now_ms
    risk = cfg.atr_mult * atr_now

    if state is None or state.direction == 0:
        if flip and des_now == 1 and risk > 0:
            entry = close_now
            sl = entry - risk
            return LiveDecision("OPEN_LONG", "regime_flip_long", "LONG", entry, sl,
                                entry + cfg.rr_target * risk, entry + (cfg.rr_target + 1.0) * risk,
                                sl, entry, atr_now, ef_now, es_now, bar_ms, cfg.instrument)
        return _flat("no_long_flip" if des_now != 1 else "no_flip")

    # in a long position
    extreme = max(state.extreme, close_now)
    new_stop = max(state.stop, extreme - cfg.atr_mult * atr_now)
    base = LiveDecision("HOLD", "trail", "LONG", state.entry, new_stop,
                        state.entry + cfg.rr_target * risk, state.entry + (cfg.rr_target + 1.0) * risk,
                        new_stop, extreme, atr_now, ef_now, es_now, bar_ms, cfg.instrument)
    if flip and des_now == -1:
        return LiveDecision("CLOSE", "regime_flip_exit", "LONG", state.entry, new_stop,
                            base.tp1, base.tp2, new_stop, extreme, atr_now, ef_now, es_now,
                            bar_ms, cfg.instrument)
    if low_now <= state.stop:
        return LiveDecision("CLOSE", "trail_stop_hit", "LONG", state.entry, state.stop,
                            base.tp1, base.tp2, state.stop, extreme, atr_now, ef_now, es_now,
                            bar_ms, cfg.instrument)
    return base
