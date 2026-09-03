"""Chronicle: causal closed-prefix replay of the FABLE narrative.

The chronicle walks the narrative series one closed bar at a time, evaluates
the same scorer the live scan uses on each prefix, and records what would have
happened to every EXECUTE decision. It is evidence, not a promise: fills are
taken at the next bar's open, a bar that touches both stop and target is
resolved as a loss, and a sample below ``minimum_trades_for_evidence`` is
labelled insufficient rather than summarised into a win rate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .config import FableConfig
from .models import Candle, MarketSnapshot, utc_iso
from .narrative import evaluate_snapshot


def _outcome(direction: str, entry: float, stop: float, target: float, bars: list[Candle]) -> tuple[str, float, int, float]:
    """Return (outcome, r_multiple, bars_held, exit_price) walking ``bars`` after entry."""
    risk = abs(entry - stop)
    if risk <= 0:
        return "INVALID", 0.0, 0, entry
    for held, bar in enumerate(bars, start=1):
        if direction == "LONG":
            hit_stop = bar.low <= stop
            hit_target = bar.high >= target
        else:
            hit_stop = bar.high >= stop
            hit_target = bar.low <= target
        if hit_stop:  # ambiguity resolves against the strategy
            return "STOP", -1.0, held, stop
        if hit_target:
            return "TARGET", abs(target - entry) / risk, held, target
    if not bars:
        return "OPEN", 0.0, 0, entry
    last = bars[-1].close
    r_value = (last - entry) / risk if direction == "LONG" else (entry - last) / risk
    return "HORIZON", r_value, len(bars), last


def run_chronicle(
    *,
    pair: dict[str, Any],
    frames: dict[str, list[Candle]],
    provenance: dict[str, dict[str, Any]],
    config: FableConfig,
    context: dict[str, Any] | None = None,
    bars: int | None = None,
) -> dict[str, Any]:
    chronicle_cfg = config.chronicle
    m15 = frames.get("M15", [])
    minimum = int(config.scan["minimum_bars"]["M15"])
    horizon = int(chronicle_cfg["outcome_horizon_bars"])
    requested = int(bars or chronicle_cfg["default_bars"])
    requested = max(minimum + 10, min(requested, int(chronicle_cfg["maximum_bars"])))
    total = len(m15)
    if total < minimum + 10:
        return {
            "pair": str(pair.get("display") or pair.get("symbol") or "UNKNOWN"),
            "evidenceStatus": "INSUFFICIENT_DATA",
            "chapters": [],
            "summary": {"trades": 0},
            "decisions": {},
            "bars": total,
        }
    start = max(minimum, total - requested)
    snapshot = MarketSnapshot(pair=dict(pair), frames=frames, provenance=provenance, as_of_epoch=m15[-1].closes_at("M15"))
    chapters: list[dict[str, Any]] = []
    seen: set[str] = set()
    decisions: Counter[str] = Counter()
    tiers: Counter[str] = Counter()
    for end in range(start, total):
        signal = evaluate_snapshot(
            snapshot,
            config,
            generated_at_epoch=m15[end - 1].closes_at("M15"),
            context=context,
            end_index=end,
        )
        decisions[str(signal["decision"])] += 1
        if signal["decision"] != "EXECUTE":
            continue
        signal_id = str(signal["signalId"])
        if signal_id in seen:
            continue
        seen.add(signal_id)
        tiers[str(signal["tier"])] += 1
        if end >= total:
            continue
        entry = m15[end].open
        stop = float(signal["stop"])
        target = float(signal["target"])
        direction = str(signal["direction"])
        future = m15[end + 1 : end + 1 + horizon]
        outcome, r_multiple, held, exit_price = _outcome(direction, entry, stop, target, future)
        risk = abs(entry - stop)
        chapters.append(
            {
                "signalId": signal_id,
                "decisionAt": utc_iso(m15[end - 1].closes_at("M15")),
                "direction": direction,
                "tier": signal["tier"],
                "coherence": signal["coherence"],
                "entry": entry,
                "stop": stop,
                "target": target,
                "plannedRr": round(abs(target - entry) / risk, 3) if risk > 0 else None,
                "outcome": outcome,
                "rMultiple": round(r_multiple, 4),
                "barsHeld": held,
                "exitPrice": exit_price,
                "raidPool": ((signal.get("annotations") or {}).get("raid") or {}).get("pool", {}).get("source"),
            }
        )
    closed = [chapter for chapter in chapters if chapter["outcome"] in {"STOP", "TARGET", "HORIZON"}]
    wins = [chapter for chapter in closed if chapter["rMultiple"] > 0]
    losses = [chapter for chapter in closed if chapter["rMultiple"] <= 0]
    total_r = sum(chapter["rMultiple"] for chapter in closed)
    minimum_trades = int(chronicle_cfg["minimum_trades_for_evidence"])
    summary: dict[str, Any] = {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": round(len(wins) / len(closed), 4) if closed else None,
        "totalR": round(total_r, 4),
        "expectancyR": round(total_r / len(closed), 4) if closed else None,
        "averageWinR": round(sum(c["rMultiple"] for c in wins) / len(wins), 4) if wins else None,
        "averageLossR": round(sum(c["rMultiple"] for c in losses) / len(losses), 4) if losses else None,
        "outcomes": dict(Counter(chapter["outcome"] for chapter in chapters)),
        "tiers": dict(tiers),
        "minimumTradesForEvidence": minimum_trades,
    }
    evidence = "INSUFFICIENT_SAMPLE" if len(closed) < minimum_trades else "SAMPLE_OK"
    return {
        "pair": str(pair.get("display") or pair.get("symbol") or "UNKNOWN"),
        "assetType": str(pair.get("type") or "unknown"),
        "evidenceStatus": evidence,
        "note": (
            "Closed-prefix replay with next-bar-open fills; same-bar stop/target resolves as a loss. "
            "This is an implementation check, not proof of edge."
        ),
        "bars": total,
        "barsEvaluated": total - start,
        "firstBarAt": utc_iso(m15[start].time),
        "lastBarAt": utc_iso(m15[-1].closes_at("M15")),
        "decisions": dict(decisions),
        "summary": summary,
        "chapters": chapters,
    }
