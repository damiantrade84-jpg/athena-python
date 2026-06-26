"""
TSMOM Engine v1 — a from-scratch, self-contained trend/momentum research engine.

Purpose: prove (or refute) SQN > 2.0 on a single asset group, with zero reuse of
Engine A/B/C/D/ASE indicators, scoring, weights, thresholds or exit policies.

Design (all parameters canonical / literature-standard, NOT tuned to this data):
  - Direction: long/short time-series momentum (symmetric, so a bull-market beta
    cannot masquerade as a timing edge).
  - Two interchangeable canonical signals:
      * ema_cross : sign(EMA_fast - EMA_slow)              (classic MA trend state)
      * donchian  : N-bar breakout regime (Turtle-style)
  - Risk unit (1R): M * ATR(14) at entry  -> every trade risks the same in vol terms.
  - Exit: Chandelier trailing stop (highest-close-since-entry - M*ATR) AND
    reverse-on-regime-flip, whichever comes first.
  - No look-ahead: signal decided on bar t close, executed at bar t+1 open;
    ATR/EMA/Donchian use only closed bars.
  - Costs: per-side fraction subtracted from each trade's R.

Metric: Van Tharp SQN.  SQN100 = mean(R)/std(R) * sqrt(min(N,100))  is the headline
(conservative; large N cannot inflate it). Raw SQN (= t-stat of mean R) also shown.

Pure offline research on frozen OHLC. Touches no execution/risk/config path.
"""
from __future__ import annotations

import glob
import json
import math
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FROZEN = os.path.join(_ROOT, "data", "frozen", "2026-05-30", "candles")

GROUPS: dict[str, list[str]] = {
    "crypto":     ["BTC_USDT", "ETH_USDT", "SOL_USDT", "DOGE_USDT", "XRP_USDT"],
    "metals":     ["XAU_USD", "XAG_USD"],
    "energy":     ["WTI_Oil"],
    "commodity":  ["XAU_USD", "XAG_USD", "WTI_Oil"],
    "forex":      ["EUR_USD", "GBP_USD", "USD_JPY", "GBP_JPY"],
    "indices":    ["DAX_40", "Dow_Jones", "NASDAQ-100", "S_P_500"],
    "stocks":     ["AAPL", "MSFT", "NVDA", "TSLA"],
}

# Realistic round-trip cost per side (fraction of price). Crypto perps taker+slip ~6bps;
# others a touch tighter. Reported with a sensitivity sweep in the runner.
COST_PER_SIDE = {
    "crypto": 0.0006, "metals": 0.0002, "energy": 0.0003, "commodity": 0.0002,
    "forex": 0.00008, "indices": 0.00015, "stocks": 0.0003,
}


@dataclass
class Params:
    signal: str = "ema_cross"     # "ema_cross" | "donchian"
    fast: int = 20                # ema fast / unused for donchian
    slow: int = 80                # ema slow / donchian lookback
    atr_n: int = 14
    atr_mult: float = 3.0         # Chandelier multiple and initial-stop distance
    cost: float = 0.0003
    regime: int = 0               # higher-timeframe EMA filter (0 = off); long only if
                                  # close>EMA(regime), short only if close<EMA(regime)


# ----------------------------------------------------------------------------- data
def load_candles(symbol: str, tf: str = "H4") -> pd.DataFrame | None:
    fs = glob.glob(f"{FROZEN}/{symbol}__{tf}__*.json")
    if not fs:
        return None
    rows = json.load(open(fs[0]))
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = (df.dropna(subset=["open", "high", "low", "close"])
            .drop_duplicates(subset=["time"])
            .sort_values("time")
            .reset_index(drop=True))
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


# ----------------------------------------------------------------- indicators / signal
def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def atr_wilder(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def desired_regime(df: pd.DataFrame, p: Params) -> np.ndarray:
    """+1 / -1 desired position per bar, using only information known at that bar close."""
    c = df["close"]
    if p.signal == "ema_cross":
        d = np.sign(ema(c, p.fast) - ema(c, p.slow))
    elif p.signal == "donchian":
        up = df["high"].rolling(p.slow).max().shift(1)     # prior-N high (no look-ahead)
        dn = df["low"].rolling(p.slow).min().shift(1)
        raw = np.where(c >= up, 1.0, np.where(c <= dn, -1.0, np.nan))
        d = pd.Series(raw, index=df.index).ffill().fillna(0.0).to_numpy()
    else:
        raise ValueError(p.signal)
    d = pd.Series(d).replace(0.0, np.nan).ffill().fillna(0.0).to_numpy()
    return d.astype(float)


# ---------------------------------------------------------------------------- simulate
@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry: float
    exit: float
    risk: float
    R: float
    bars: int
    reason: str


def simulate(df: pd.DataFrame, p: Params) -> list[Trade]:
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    t = df["time"].to_numpy()
    atr = atr_wilder(df, p.atr_n).to_numpy()
    des = desired_regime(df, p)
    reg = ema(df["close"], p.regime).to_numpy() if p.regime > 0 else None
    n = len(df)
    warm = max(p.slow, p.atr_n, p.regime) + 2

    trades: list[Trade] = []
    pos = 0
    entry = stop = risk = 0.0
    entry_i = 0
    extreme = 0.0  # highest close (long) / lowest close (short) since entry

    def close_trade(exit_px: float, i: int, reason: str):
        nonlocal pos
        cost = p.cost * (entry + exit_px)
        pnl = pos * (exit_px - entry) - cost
        R = pnl / risk if risk > 0 else 0.0
        trades.append(Trade(pd.Timestamp(t[entry_i]), pd.Timestamp(t[i]), pos,
                            float(entry), float(exit_px), float(risk), float(R),
                            i - entry_i, reason))
        pos = 0

    for i in range(warm, n):
        sig = des[i - 1]                      # actionable at open[i]
        flip = des[i - 1] != des[i - 2]

        if pos != 0:
            if flip and sig != pos:           # regime reversed -> exit at this open
                close_trade(o[i], i, "flip")
            elif pos == 1 and l[i] <= stop:   # long stop (gap-through fills at open)
                close_trade(min(o[i], stop), i, "stop")
            elif pos == -1 and h[i] >= stop:  # short stop
                close_trade(max(o[i], stop), i, "stop")
            if pos != 0:                      # survived -> ratchet Chandelier trail
                if pos == 1:
                    extreme = max(extreme, c[i])
                    stop = max(stop, extreme - p.atr_mult * atr[i])
                else:
                    extreme = min(extreme, c[i])
                    stop = min(stop, extreme + p.atr_mult * atr[i])

        ok_reg = True
        if reg is not None and sig != 0:      # higher-timeframe trend gate (prior bar)
            ok_reg = (sig == 1 and c[i - 1] > reg[i - 1]) or (sig == -1 and c[i - 1] < reg[i - 1])
        if pos == 0 and flip and sig != 0 and ok_reg:   # fresh entry only on a regime flip
            pos = int(sig)
            entry = o[i]
            entry_i = i
            risk = p.atr_mult * atr[i - 1]
            extreme = c[i]
            if pos == 1:
                stop = entry - risk
                stop = max(stop, extreme - p.atr_mult * atr[i])
            else:
                stop = entry + risk
                stop = min(stop, extreme + p.atr_mult * atr[i])

    if pos != 0:
        close_trade(c[n - 1], n - 1, "eod")
    return trades


# ----------------------------------------------------------------------------- metrics
def metrics(trades: list[Trade]) -> dict:
    R = np.array([tr.R for tr in trades], float)
    n = len(R)
    out = {"N": n}
    if n < 2:
        out.update(sqn100=float("nan"), sqn_raw=float("nan"), exp=float("nan"),
                   std=float("nan"), win=float("nan"), pf=float("nan"),
                   avg_w=float("nan"), avg_l=float("nan"), maxdd=float("nan"))
        return out
    mean, std = R.mean(), R.std(ddof=1)
    wins, losses = R[R > 0], R[R <= 0]
    eq = np.cumsum(R)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    out.update(
        sqn100=mean / std * math.sqrt(min(n, 100)) if std > 0 else float("nan"),
        sqn_raw=mean / std * math.sqrt(n) if std > 0 else float("nan"),
        exp=mean, std=std,
        win=len(wins) / n,
        pf=(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        avg_w=wins.mean() if len(wins) else 0.0,
        avg_l=losses.mean() if len(losses) else 0.0,
        maxdd=dd,
    )
    return out


def run_group(symbols: list[str], p: Params, tf: str = "H4"):
    all_trades: list[Trade] = []
    per_symbol = {}
    for s in symbols:
        df = load_candles(s, tf)
        if df is None or len(df) < max(p.slow, p.atr_n) + 50:
            per_symbol[s] = {"N": 0}
            continue
        tr = simulate(df, p)
        per_symbol[s] = metrics(tr)
        all_trades.extend(tr)
    all_trades.sort(key=lambda x: x.exit_time)
    return all_trades, per_symbol


def split_is_oos(trades: list[Trade], frac: float = 0.6):
    if not trades:
        return [], []
    times = sorted(tr.entry_time for tr in trades)
    cut = times[0] + (times[-1] - times[0]) * frac
    return ([t for t in trades if t.entry_time <= cut],
            [t for t in trades if t.entry_time > cut])


# -------------------------------------------------------------------------------- main
def _fmt(m: dict) -> str:
    if m.get("N", 0) < 2:
        return f"N={m.get('N',0):4d}  (insufficient)"
    return (f"N={m['N']:4d}  SQN100={m['sqn100']:5.2f}  rawSQN={m['sqn_raw']:5.2f}  "
            f"exp={m['exp']:+.3f}R  win={m['win']*100:4.1f}%  PF={m['pf']:4.2f}  "
            f"avgW={m['avg_w']:+.2f} avgL={m['avg_l']:+.2f}  maxDD={m['maxdd']:4.1f}R")


def main():
    tf = "H4"
    print(f"TSMOM Engine v1  |  timeframe={tf}  |  metric=SQN (Van Tharp)\n")

    for sig in ("ema_cross", "donchian"):
        print("=" * 100)
        print(f"SIGNAL = {sig}   (fast/slow = 20/80, ATR14, 3xATR Chandelier)")
        print("=" * 100)
        print(f"{'GROUP':10s} {'FULL PERIOD (pooled)':>0s}")
        for g, syms in GROUPS.items():
            p = Params(signal=sig, cost=COST_PER_SIDE.get(g, 0.0003))
            trades, per = run_group(syms, p, tf)
            full = metrics(trades)
            print(f"{g:10s} {_fmt(full)}")
        print()


if __name__ == "__main__":
    main()
