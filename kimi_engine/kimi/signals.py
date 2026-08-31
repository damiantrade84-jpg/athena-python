"""Signal generation: fuse indicator features + structure into trade setups
with entry / SL / TP geometry. Gates are hard — a pretty score cannot pass
without direction agreement and a structural trigger.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

import numpy as np

from . import indicators as I
from . import structure as S
from .config import Config
from .datafeed import CandleSet, classify
from .scoring import ScoreCard, score

_ids = itertools.count(1)


@dataclass
class Signal:
    id: str
    symbol: str
    direction: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    atr: float
    card: ScoreCard
    created_at: float = field(default_factory=time.time)
    valid_until: float = 0.0
    status: str = "ACTIVE"          # ACTIVE | EXECUTED | EXPIRED | CANCELLED

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "symbol": self.symbol,
            "direction": "LONG" if self.direction > 0 else "SHORT",
            "entry": self.entry, "sl": self.sl, "tp1": self.tp1, "tp2": self.tp2,
            "atr": self.atr, "score": self.card.total, "grade": self.card.grade,
            "components": self.card.components, "reasons": self.card.reasons,
            "createdAt": self.created_at, "validUntil": self.valid_until,
            "status": self.status,
        }


def _features(cs: CandleSet) -> dict:
    o, h, l, c, v, tk = cs.o, cs.h, cs.l, cs.c, cs.v, cs.taker
    a = I.atr(h, l, c, 14)
    vw, _, _ = I.vwap_session(cs.t, h, l, c, v)
    return {
        "tide": I.tide(h, l, c),
        "pulse": I.pulse(c, h, l),
        "pressure": I.pressure(v, h, l, c),
        "flow": I.flow(tk, v),
        "vol": I.vol_regime(h, l, c),
        "atr": a,
        "vwap": vw,
        # closed flags keep forming-bar sweeps/FVGs out of the event stream
        "events": S.structure_events(o, h, l, c, closed=cs.closed),
        "swings": S.swings(h, l),
        "lo10": float(l[-10:].min()) if len(l) else 0.0,
        "hi10": float(h[-10:].max()) if len(h) else 0.0,
        "has_volume": bool(np.any(v > 0)),
    }


def signal_valid_until(bar_open_ms: float, tf: str, now: float | None = None) -> float:
    """Expiry at the close of SIGNAL_TTL_BARS entry bars from this bar's open.

    Anchored to the bar, not to wall-clock ``now + N*tf``. The old formula
    always printed a full 30m (6×5m) remaining, so the chip looked frozen.
    Future-dated bars are capped at N bars from ``now`` so clock skew cannot
    extend the window.
    """
    tf_sec = float(Config.TF_MINUTES.get(tf, 5)) * 60.0
    bars = max(1, int(Config.SIGNAL_TTL_BARS))
    deadline = float(bar_open_ms) / 1000.0 + bars * tf_sec
    if now is None:
        now = time.time()
    return min(deadline, now + bars * tf_sec)


def _build(cs_entry: CandleSet, f_ctx: dict,
           direction: int, card: ScoreCard) -> Signal | None:
    """Entry from the entry-TF close; SL/TP geometry from the CONTEXT TF.

    The SL anchor (context swing, else the 10-bar context extreme) and the
    ATR used for the buffer and the sanity band travel together from the
    same timeframe — anchoring to a 15m swing while buffering with a 5m ATR
    made legitimate stops read as absurd geometry and vice versa. The stamped
    ``atr`` is the context-TF ATR that actually built the SL."""
    entry = float(cs_entry.c[-1])
    atr_v = float(f_ctx["atr"][-1])
    if atr_v <= 0:
        return None
    sw = f_ctx["swings"]
    if direction > 0:
        ref = S.nearest_swing(sw, "low", below=entry)
        sl = (ref.price if ref else f_ctx["lo10"]) - Config.SL_ATR_BUFFER * atr_v
    else:
        ref = S.nearest_swing(sw, "high", above=entry)
        sl = (ref.price if ref else f_ctx["hi10"]) + Config.SL_ATR_BUFFER * atr_v
    risk = abs(entry - sl)
    if risk < 0.25 * atr_v or risk > 4.0 * atr_v:
        return None  # degenerate or absurd geometry
    tp1 = entry + direction * Config.TP1_R * risk
    tp2 = entry + direction * Config.TP2_R * risk
    bar_open_ms = float(cs_entry.t[-1]) if len(cs_entry.t) else time.time() * 1000.0
    return Signal(
        id=f"K{next(_ids):05d}", symbol=cs_entry.symbol, direction=direction,
        entry=entry, sl=sl, tp1=tp1, tp2=tp2, atr=atr_v, card=card,
        valid_until=signal_valid_until(bar_open_ms, cs_entry.tf),
    )


def evaluate(cs_entry: CandleSet, cs_ctx: CandleSet, cs_bias: CandleSet,
             min_score: float = Config.SIGNAL_MIN_SCORE) -> tuple[Signal | None, list[ScoreCard]]:
    """Returns (signal_or_None, [long_card, short_card]) — cards always returned
    so the UI can show live scores even when no signal fires."""
    f_ent, f_ctx, f_bias = _features(cs_entry), _features(cs_ctx), _features(cs_bias)
    group = classify(cs_entry.symbol)
    vw_last = float(f_ent["vwap"][-1])
    vwap_rel = ((float(cs_entry.c[-1]) - vw_last) / vw_last
                if f_ent["has_volume"] and vw_last > 0 else None)
    cards: list[ScoreCard] = []
    best: tuple[Signal | None, float] = (None, -1.0)

    for direction in (+1, -1):
        card = score(
            cs_entry.symbol, direction,
            tide_bias=float(f_bias["tide"][-1]),
            tide_ctx=float(f_ctx["tide"][-1]),
            pulse_v=float(f_ctx["pulse"][-1]),
            flow_v=float(f_ent["flow"][-1]),
            pressure_v=float(f_ent["pressure"][-1]),
            vwap_rel=vwap_rel,
            vol_pct=float(f_ctx["vol"][-1]),
            events=f_ctx["events"],
            group=group,
        )
        cards.append(card)

        # ---- hard gates -------------------------------------------------
        if card.total < min_score:
            continue
        if f_bias["tide"][-1] * direction < 0.15:
            continue                       # bias TF must agree
        if f_ctx["tide"][-1] * direction < 0.05:
            continue                       # context TF must not fight
        if not card.hard_event:
            continue                       # need a RECENT sweep/displacement
        sig = _build(cs_entry, f_ctx, direction, card)
        if sig and card.total > best[1]:
            best = (sig, card.total)

    return best[0], cards
