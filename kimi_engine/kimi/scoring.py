"""KIMI Score — proprietary 0..100 composite with hard gates.

Component weights (long side shown; shorts mirror):

  tide       20  multi-TF trend alignment (bias TF agrees with context TF)
  pulse      15  momentum composite, direction-aware
  structure  18  sweep(10) + displacement(5) + fvg(3), recency-decayed
  flow       12  taker imbalance drift in direction
  pressure   10  signed relative volume
  vwap        8  price vs session VWAP, penalize over-extension
                 (neutral 0.5 when the symbol has no usable volume)
  session     9  group-aware timing: FX-style killzones for forex/metals/
                 indices/energy; flat for 24/7 crypto; US cash hours for
                 share CFDs (near-zero when the underlying market is closed)
  vol         8  ATR-percentile fit band

Grades: A+ >= Config.SCORE_A_PLUS (requires a RECENT sweep or displacement),
A >= Config.SCORE_A, B >= Config.SCORE_B (watch).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Config
from .structure import StructureEvent

WEIGHTS = {"tide": 20, "pulse": 15, "structure": 18, "flow": 12,
           "pressure": 10, "vwap": 8, "session": 9, "vol": 8}


@dataclass
class ScoreCard:
    symbol: str
    direction: int                      # +1 long, -1 short
    total: float
    grade: str                          # A+ / A / B / —
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    hard_event: bool = False
    ts: float = field(default_factory=time.time)


def _dir(v: float, direction: int) -> float:
    """Map a signed [-1,1] feature to a 0..1 directional fit."""
    return max(0.0, min(1.0, (v * direction + 1.0) / 2.0))


def session_weight(ts: float | None = None, group: str = "forex") -> tuple[float, str]:
    """Session timing weight (0..1), group-aware.

    forex/metals/indices/energy use the UTC killzones (London 07-10,
    NY 12-16, Asia 00-07). Crypto trades 24/7 — no killzone edge is claimed,
    so it scores a flat mid weight. Share CFDs follow the US cash session
    (13:30-20:00 UTC); outside it the underlying market is closed, spreads
    widen, and the score should not look tradeable."""
    tm = time.gmtime(ts or time.time())
    hour = tm.tm_hour
    if group == "crypto":
        return 0.8, "24/7 crypto"
    if group == "stock":
        mins = hour * 60 + tm.tm_min
        if 13 * 60 + 30 <= mins < 20 * 60:
            return 1.0, "US cash"
        return 0.2, "cash closed"
    if 7 <= hour < 10:
        return 1.0, "London"
    if 12 <= hour < 16:
        return 1.0, "NewYork"
    if 10 <= hour < 12:
        return 0.75, "London-NY handover"
    if 0 <= hour < 7:
        return 0.55, "Asia"
    if 16 <= hour < 21:
        return 0.6, "NY afternoon"
    return 0.35, "off-hours"


def vol_fit(pct: float) -> float:
    """Best in 0.20..0.85; linear decay outside."""
    if 0.20 <= pct <= 0.85:
        return 1.0
    if pct < 0.20:
        return max(0.0, pct / 0.20)
    return max(0.0, 1.0 - (pct - 0.85) / 0.15)


def structure_fit(events: list[StructureEvent], direction: int) -> tuple[float, bool, list[str]]:
    """Recency-decayed structure confluence. Returns (0..1, hard_event, reasons).

    ``hard`` — the trigger required for A+ and for signal gating — demands a
    RECENT sweep or displacement (within Config.STRUCT_HARD_MAX_AGE_BARS
    context bars). Older events keep their decayed points but can no longer
    qualify a grade or a signal on their own."""
    pts = 0.0
    hard = False
    reasons: list[str] = []
    for e in events:
        if e.direction != direction:
            continue
        decay = max(0.0, 1.0 - e.age / 24.0)
        if e.kind == "sweep":
            pts += 0.55 * decay
            if e.age <= Config.STRUCT_HARD_MAX_AGE_BARS:
                hard = True
            reasons.append(f"sweep {'lows' if direction > 0 else 'highs'} @{e.level:g} ({e.age}b ago)")
        elif e.kind == "displacement":
            pts += 0.28 * decay
            if e.age <= Config.STRUCT_HARD_MAX_AGE_BARS:
                hard = True
            reasons.append(f"displacement ({e.age}b ago)")
        elif e.kind == "fvg":
            pts += 0.17 * decay
            reasons.append(f"FVG @{e.level:g} ({e.age}b ago)")
    return min(1.0, pts), hard, reasons[:3]


def score(symbol: str, direction: int, *, tide_bias: float, tide_ctx: float,
          pulse_v: float, flow_v: float, pressure_v: float,
          vwap_rel: float | None, vol_pct: float, events: list[StructureEvent],
          group: str = "forex", ts: float | None = None) -> ScoreCard:
    """vwap_rel: (price - vwap) / vwap, signed; None when the symbol has no
    usable volume — VWAP degenerates to the typical price there and the
    component would be pure noise."""
    comp: dict[str, float] = {}
    reasons: list[str] = []

    # tide: need bias and context agreement
    tide_fit = 0.6 * _dir(tide_bias, direction) + 0.4 * _dir(tide_ctx, direction)
    comp["tide"] = round(WEIGHTS["tide"] * tide_fit, 1)
    if tide_bias * direction > 0.25 and tide_ctx * direction > 0.1:
        reasons.append(f"tide aligned (1h {tide_bias:+.2f} / 15m {tide_ctx:+.2f})")

    p_fit = _dir((pulse_v - 50.0) / 50.0, direction)
    comp["pulse"] = round(WEIGHTS["pulse"] * p_fit, 1)
    if p_fit > 0.7:
        reasons.append(f"pulse {pulse_v:.0f}")

    s_fit, hard, s_reasons = structure_fit(events, direction)
    comp["structure"] = round(WEIGHTS["structure"] * s_fit, 1)
    reasons.extend(s_reasons)

    comp["flow"] = round(WEIGHTS["flow"] * _dir(flow_v, direction), 1)
    comp["pressure"] = round(WEIGHTS["pressure"] * _dir(pressure_v, direction), 1)

    # vwap: right side good, but >2.5% extension decays (chasing)
    if vwap_rel is None:
        comp["vwap"] = round(WEIGHTS["vwap"] * 0.5, 1)
        reasons.append("vwap n/a (no volume)")
    else:
        side = 1.0 if vwap_rel * direction > 0 else 0.25
        ext = min(1.0, abs(vwap_rel) / 0.025)
        vwap_fit = side * (1.0 - 0.5 * max(0.0, ext - 1.0))
        comp["vwap"] = round(WEIGHTS["vwap"] * max(0.0, vwap_fit), 1)

    sw, sname = session_weight(ts, group)
    comp["session"] = round(WEIGHTS["session"] * sw, 1)
    reasons.append(f"{sname} session")

    comp["vol"] = round(WEIGHTS["vol"] * vol_fit(vol_pct), 1)

    total = round(sum(comp.values()), 1)
    if total >= Config.SCORE_A_PLUS and hard:
        grade = "A+"
    elif total >= Config.SCORE_A:
        grade = "A"
    elif total >= Config.SCORE_B:
        grade = "B"
    else:
        grade = "—"
    return ScoreCard(symbol, direction, total, grade, comp, reasons, hard)
