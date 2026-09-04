"""MUSE sounding — causal closed-prefix replay over stored candles."""

from __future__ import annotations

import time
from typing import Any, Callable

from .config import MuseConfig
from .market_data import MuseMarketDataProvider
from .models import MarketSnapshot
from .scoring import evaluate_snapshot


def run_sounding(*, pair: dict[str, Any], bars: int, config: MuseConfig,
                 market_data: MuseMarketDataProvider,
                 context_fn: Callable[[dict[str, Any]], dict[str, Any]] | None,
                 now_fn=time.time) -> dict[str, Any]:
    """Replay the vector/spark prefix bar-by-bar; score each closed prefix once."""
    requested = max(10, min(int(bars), int(config.sounding["maximum_bars"])))
    full = market_data.snapshot(pair, now_epoch=float(now_fn()))
    vector = full.frames.get("M15") or []
    spark = full.frames.get("M5") or []
    if len(vector) < 12:
        return {"pair": full.display, "bars": 0, "signals": 0, "prime": 0,
                "rows": [], "reason": "INSUFFICIENT_VECTOR"}
    horizon = int(config.sounding["outcome_horizon_m5_bars"])
    rows: list[dict[str, Any]] = []
    prime = 0
    # Walk the last `requested` M15 prefixes; each prefix is scored causally.
    prefixes = vector[-requested:]
    for offset in range(6, len(prefixes)):
        window = prefixes[: offset + 1]
        as_of = window[-1].closes_at("M15") + 0.5
        snap = MarketSnapshot(pair=pair,
                              frames={**full.frames, "M15": list(window),
                                      "M5": [c for c in spark if c.time < as_of]},
                              provenance=full.provenance, as_of_epoch=as_of)
        context = context_fn(pair) if context_fn else {}
        signal = evaluate_snapshot(snap, config, context)
        if signal["decision"] not in ("PRIME", "STAGE"):
            continue
        # Outcome: M5 excursion toward target vs stop over the horizon.
        future = [c for c in spark if as_of <= c.time < as_of + horizon * 300.0]
        outcome = _outcome(signal, future)
        rows.append({"signalId": signal["signalId"], "generatedAt": signal["generatedAt"],
                     "direction": signal["direction"], "setup": signal["setup"],
                     "decision": signal["decision"], "score": signal["score"],
                     "entry": signal["entry"], "stop": signal["stop"],
                     "target": signal["target"], "outcome": outcome})
        if signal["decision"] == "PRIME":
            prime += 1
    wins = sum(1 for r in rows if r["outcome"] == "TARGET")
    losses = sum(1 for r in rows if r["outcome"] == "STOP")
    return {"pair": full.display, "bars": len(prefixes), "signals": len(rows), "prime": prime,
            "wins": wins, "losses": losses,
            "hitRate": round(wins / max(1, wins + losses), 4),
            "rows": rows[-int(config.sounding["default_bars"]):]}


def _outcome(signal: dict[str, Any], future: list) -> str:
    direction = signal.get("direction")
    entry = signal.get("entry")
    stop = signal.get("stop")
    target = signal.get("target")
    if entry is None or stop is None or target is None or not future:
        return "UNKNOWN"
    for candle in future:
        if direction == "LONG":
            if candle.low <= stop:
                return "STOP"
            if candle.high >= target:
                return "TARGET"
        elif direction == "SHORT":
            if candle.high >= stop:
                return "STOP"
            if candle.low <= target:
                return "TARGET"
    last = future[-1].close
    if direction == "LONG":
        return "FAVORABLE" if last > entry else "ADRIFT"
    if direction == "SHORT":
        return "FAVORABLE" if last < entry else "ADRIFT"
    return "UNKNOWN"
