"""Causal point-in-time replay for GROK research diagnostics."""

from __future__ import annotations

from typing import Any

from .config import GrokConfig
from .models import Candle, MarketSnapshot, TIMEFRAME_SECONDS
from .scoring import evaluate_snapshot


def _closed_prefix(rows: list[Candle], timeframe: str, as_of: float) -> list[Candle]:
    seconds = TIMEFRAME_SECONDS[timeframe]
    return [row for row in rows if row.time + seconds <= as_of + 1e-9]


def _outcome(signal: dict[str, Any], future: list[Candle]) -> dict[str, Any]:
    direction = str(signal["direction"])
    stop = float(signal["stop"])
    target = float(signal["target"])
    entry = float(signal["entry"])
    risk = abs(entry - stop)
    for index, candle in enumerate(future, 1):
        if direction == "LONG":
            stop_hit = candle.low <= stop
            target_hit = candle.high >= target
        else:
            stop_hit = candle.high >= stop
            target_hit = candle.low <= target
        if stop_hit:
            return {"outcome": "LOSS", "rMultiple": -1.0, "barsHeld": index, "exit": stop}
        if target_hit:
            return {
                "outcome": "WIN",
                "rMultiple": abs(target - entry) / risk if risk > 0 else 0.0,
                "barsHeld": index,
                "exit": target,
            }
    if not future:
        return {"outcome": "UNRESOLVED", "rMultiple": 0.0, "barsHeld": 0, "exit": entry}
    exit_price = future[-1].close
    signed = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    return {
        "outcome": "TIME_EXIT",
        "rMultiple": signed / risk if risk > 0 else 0.0,
        "barsHeld": len(future),
        "exit": exit_price,
    }


def run_causal_replay(
    *,
    pair: dict[str, Any],
    frames: dict[str, list[Candle]],
    provenance: dict[str, dict[str, Any]],
    config: GrokConfig,
    horizon_m5_bars: int | None = None,
) -> dict[str, Any]:
    m5 = frames.get("M5") or []
    horizon = int(horizon_m5_bars or config.replay["outcome_horizon_m5_bars"])
    minimum = int(config.scan["minimum_bars"]["M5"])
    trades: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    for index in range(minimum - 1, max(minimum - 1, len(m5) - horizon)):
        as_of = m5[index].closes_at("M5")
        prefixes = {
            timeframe: _closed_prefix(rows, timeframe, as_of)
            for timeframe, rows in frames.items()
        }
        snapshot = MarketSnapshot(
            pair=dict(pair),
            frames=prefixes,
            provenance=provenance,
            as_of_epoch=as_of,
            quality_errors=[],
        )
        signal = evaluate_snapshot(snapshot, config, generated_at_epoch=as_of)
        if signal.get("decision") != "READY":
            continue
        identity = str(signal.get("signalId") or "")
        if not identity or identity in seen_events:
            continue
        seen_events.add(identity)
        future = m5[index + 1 : index + 1 + horizon]
        result = _outcome(signal, future)
        trades.append(
            {
                "signalId": identity,
                "pair": signal["pair"],
                "direction": signal["direction"],
                "setup": signal["setup"],
                "score": signal["score"],
                "entry": signal["entry"],
                "stop": signal["stop"],
                "target": signal["target"],
                **result,
            }
        )
    wins = sum(1 for trade in trades if trade["outcome"] == "WIN")
    losses = sum(1 for trade in trades if trade["outcome"] == "LOSS")
    r_values = [float(trade["rMultiple"]) for trade in trades]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    minimum_trades = int(config.replay["minimum_trades_for_evidence"])
    return {
        "pair": str(pair.get("display") or pair.get("symbol") or "UNKNOWN"),
        "causal": True,
        "sameBarPolicy": "STOP_FIRST",
        "evidenceStatus": "SAMPLE_SUFFICIENT" if len(trades) >= minimum_trades else "INSUFFICIENT_SAMPLE",
        "minimumTradesForEvidence": minimum_trades,
        "metrics": {
            "tradeCount": len(trades),
            "wins": wins,
            "losses": losses,
            "winRatePct": round(100.0 * wins / len(trades), 2) if trades else None,
            "averageR": round(sum(r_values) / len(r_values), 4) if r_values else None,
            "totalR": round(sum(r_values), 4) if r_values else 0.0,
            "maxDrawdownR": round(abs(max_dd), 4),
        },
        "trades": trades[-40:],
    }
