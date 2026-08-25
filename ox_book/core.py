"""OX Book core - pure signal, simulation, and metrics (no I/O).

No look-ahead contract: the desired position is decided on bar t close and is only
actionable at bar t+1 open; EMA/ATR use closed bars; intrabar stop fills at the stop
(gap-through fills at the open).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ox_book.contracts import OxMetrics, OxParams, OxTrade


def validate_candles(df: pd.DataFrame | None, min_bars: int) -> bool:
    if df is None or len(df) < min_bars:
        return False
    needed = {"time", "open", "high", "low", "close"}
    if not needed.issubset(set(df.columns)):
        return False
    return not df[["open", "high", "low", "close"]].isna().any().any()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr_wilder(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / float(n), adjust=False).mean()


def desired_position(df: pd.DataFrame, p: OxParams) -> np.ndarray:
    """Long-only trend state per bar from closed bars only (+1 long / 0 flat)."""
    c = df["close"]
    d = np.sign(ema(c, p.fast) - ema(c, p.slow))
    d = pd.Series(d).replace(0.0, np.nan).ffill().fillna(0.0)
    if p.long_only:
        d = d.clip(lower=0.0)
    return d.to_numpy(dtype=float)


def simulate(df: pd.DataFrame, p: OxParams) -> list[OxTrade]:
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    t = df["time"].to_numpy()
    atr = atr_wilder(df, p.atr_n).to_numpy()
    des = desired_position(df, p)
    n = len(df)
    warm = max(p.slow, p.atr_n) + 2

    trades: list[OxTrade] = []
    pos = 0
    entry = stop = risk = 0.0
    entry_i = 0
    extreme = 0.0

    def close_trade(exit_px: float, i: int, reason: str) -> None:
        nonlocal pos
        cost = p.cost_per_side * (entry + exit_px)
        pnl = pos * (exit_px - entry) - cost
        r = pnl / risk if risk > 0 else 0.0
        trades.append(
            OxTrade(
                entry_time=pd.Timestamp(t[entry_i]),
                exit_time=pd.Timestamp(t[i]),
                direction=pos,
                entry=float(entry),
                exit=float(exit_px),
                risk=float(risk),
                R=float(r),
                bars=i - entry_i,
                reason=reason,
            )
        )
        pos = 0

    for i in range(warm, n):
        sig = des[i - 1]
        prev_sig = des[i - 2]

        if pos != 0:
            if sig == 0 and prev_sig != 0:
                close_trade(o[i], i, "flip")
            elif l[i] <= stop:
                close_trade(min(o[i], stop), i, "stop")
            else:
                extreme = max(extreme, c[i])
                stop = max(stop, extreme - p.atr_mult * atr[i])

        if pos == 0 and sig != 0 and prev_sig == 0:
            pos = 1
            entry = o[i]
            entry_i = i
            risk = p.atr_mult * atr[i - 1]
            extreme = c[i]
            stop = max(entry - risk, extreme - p.atr_mult * atr[i])

    if pos != 0:
        close_trade(c[n - 1], n - 1, "eod")
    return trades


def metrics(trades: list[OxTrade]) -> OxMetrics:
    n = len(trades)
    if n < 2:
        return OxMetrics(n=n)
    rs = np.array([tr.R for tr in trades], dtype=float)
    mean = float(rs.mean())
    std = float(rs.std(ddof=1))
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    eq = np.cumsum(rs)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    sqn100 = mean / std * math.sqrt(min(n, 100)) if std > 0 else None
    t_stat = mean / std * math.sqrt(n) if std > 0 else None
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None
    return OxMetrics(
        n=n,
        exp_r=mean,
        std_r=std,
        sqn100=sqn100,
        t_stat=t_stat,
        win_rate=float(len(wins)) / float(n),
        profit_factor=pf,
        max_dd_r=dd,
    )


def split_is_oos(trades: list[OxTrade], frac: float = 0.6) -> tuple[list[OxTrade], list[OxTrade]]:
    if not trades:
        return [], []
    times = sorted(tr.entry_time for tr in trades)
    cut = times[0] + (times[-1] - times[0]) * frac
    return (
        [tr for tr in trades if tr.entry_time <= cut],
        [tr for tr in trades if tr.entry_time > cut],
    )


def era_expectancies(trades: list[OxTrade], era_years: int) -> list[float]:
    """Mean R per calendar era of era_years, anchored at the first trade year."""
    if not trades:
        return []
    first_year = min(pd.Timestamp(tr.entry_time).year for tr in trades)
    by_era: dict[int, list[float]] = {}
    for tr in trades:
        year = pd.Timestamp(tr.entry_time).year
        idx = (year - first_year) // era_years
        by_era.setdefault(int(idx), []).append(tr.R)
    out: list[float] = []
    for key in sorted(by_era):
        arr = np.array(by_era[key], dtype=float)
        out.append(float(arr.mean()) if len(arr) else 0.0)
    return out


def daily_returns(df: pd.DataFrame) -> pd.Series:
    s = df.set_index("time")["close"].astype(float)
    return s.pct_change().dropna()


def returns_correlation(a: pd.Series, b: pd.Series) -> float | None:
    joined = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    if len(joined) < 30:
        return None
    corr = joined["a"].corr(joined["b"])
    return None if pd.isna(corr) else float(corr)
