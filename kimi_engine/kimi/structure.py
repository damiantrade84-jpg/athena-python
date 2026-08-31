"""KIMI structure layer — price-action events detected from raw candles.

  swings        — fractal swing highs/lows (left/right confirmed)
  sweeps        — liquidity sweep: wick takes a prior swing extreme, closes back
  displacements — body & range expansion vs ATR (initiative move)
  fvgs          — 3-candle fair-value gaps, tracks fill state
  structure_events — fused event stream used by the scorer
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .indicators import atr


@dataclass
class Swing:
    idx: int
    price: float
    kind: str          # "high" | "low"


@dataclass
class StructureEvent:
    idx: int           # bar index where it occurred
    kind: str          # "sweep" | "displacement" | "fvg"
    direction: int     # +1 bullish, -1 bearish
    level: float       # reference price
    age: int = 0       # bars since event (filled by caller)


def swings(h: np.ndarray, l: np.ndarray, left: int = 3, right: int = 3) -> list[Swing]:
    out: list[Swing] = []
    n = len(h)
    for i in range(left, n - right):
        if h[i] == h[i - left:i + right + 1].max() and \
           (h[i - left:i + right + 1] < h[i]).sum() >= left + right - 1:
            out.append(Swing(i, float(h[i]), "high"))
        if l[i] == l[i - left:i + right + 1].min() and \
           (l[i - left:i + right + 1] > l[i]).sum() >= left + right - 1:
            out.append(Swing(i, float(l[i]), "low"))
    return out


def detect_sweeps(h: np.ndarray, l: np.ndarray, c: np.ndarray,
                  lookback: int = 60) -> list[StructureEvent]:
    """A sweep at bar i: bar's wick exceeds a *confirmed* prior swing extreme
    from the last `lookback` bars, but the body closes back inside it."""
    out: list[StructureEvent] = []
    sw = swings(h, l)
    sw_by_kind = {"high": [s for s in sw if s.kind == "high"],
                  "low": [s for s in sw if s.kind == "low"]}
    n = len(c)
    for i in range(10, n):
        for s in sw_by_kind["high"]:
            if 0 < i - s.idx <= lookback and h[i] > s.price and c[i] < s.price:
                out.append(StructureEvent(i, "sweep", -1, s.price))
                break
        for s in sw_by_kind["low"]:
            if 0 < i - s.idx <= lookback and l[i] < s.price and c[i] > s.price:
                out.append(StructureEvent(i, "sweep", +1, s.price))
                break
    return out


def detect_displacements(o: np.ndarray, h: np.ndarray, l: np.ndarray,
                         c: np.ndarray) -> list[StructureEvent]:
    """Initiative candle: body > 1.6*ATR and full range > 2.0*ATR."""
    out: list[StructureEvent] = []
    a = atr(h, l, c, 14)
    for i in range(15, len(c)):
        if a[i] <= 0:
            continue
        body = c[i] - o[i]
        rng = h[i] - l[i]
        if abs(body) > 1.6 * a[i] and rng > 2.0 * a[i]:
            out.append(StructureEvent(i, "displacement", +1 if body > 0 else -1,
                                      float(c[i])))
    return out


def detect_fvgs(h: np.ndarray, l: np.ndarray) -> list[StructureEvent]:
    """3-candle imbalance. Bullish FVG at bar i: low[i] > high[i-2];
    bearish: high[i] < low[i-2]. Level = gap midpoint."""
    out: list[StructureEvent] = []
    for i in range(2, len(h)):
        if l[i] > h[i - 2]:
            out.append(StructureEvent(i, "fvg", +1, float((h[i - 2] + l[i]) / 2)))
        elif h[i] < l[i - 2]:
            out.append(StructureEvent(i, "fvg", -1, float((l[i - 2] + h[i]) / 2)))
    return out


def structure_events(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                     max_age: int = 24,
                     closed: np.ndarray | None = None) -> list[StructureEvent]:
    """Fused recent event stream, newest last, ages relative to last bar.

    When ``closed`` flags are supplied, events on the still-forming last bar
    are dropped: a sweep only counts once the bar CLOSES back inside the
    level — on the forming bar the "close" is the live price and the event
    would repaint as price moves."""
    evts = detect_sweeps(h, l, c) + detect_displacements(o, h, l, c) + detect_fvgs(h, l)
    last = len(c) - 1
    forming = closed is not None and len(closed) > 0 and not bool(closed[-1])
    out: list[StructureEvent] = []
    for e in evts:
        e.age = last - e.idx
        if e.age > max_age:
            continue
        if forming and e.idx == last:
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.idx)


def nearest_swing(sw: list[Swing], kind: str, below: float | None = None,
                  above: float | None = None) -> Swing | None:
    cand = [s for s in sw if s.kind == kind]
    if below is not None:
        cand = [s for s in cand if s.price < below]
        return max(cand, key=lambda s: s.price) if cand else None
    if above is not None:
        cand = [s for s in cand if s.price > above]
        return min(cand, key=lambda s: s.price) if cand else None
    return None
