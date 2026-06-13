from __future__ import annotations

from math import sqrt
from statistics import fmean, stdev
from typing import Any


def trade_path_diagnostics(
    future_window: list[dict],
    *,
    direction: str,
    entry: float,
    sl: float,
    tp1: float | None = None,
    tp2: float | None = None,
) -> dict[str, Any]:
    """Measure path quality (MFE/MAE) without changing exit behavior."""
    risk = abs(float(entry) - float(sl))
    empty = {
        "max_favorable_excursion_r": 0.0,
        "max_adverse_excursion_r": 0.0,
        "reached_one_r": False,
        "reached_tp1": False,
        "reached_tp2": False,
        "reached_sl": False,
    }
    if risk <= 0:
        return empty

    direction = str(direction or "").upper()
    max_favorable = 0.0
    max_adverse = 0.0
    reached_one_r = False
    reached_tp1 = False
    reached_tp2 = False
    reached_sl = False

    for bar in future_window or []:
        try:
            high = float(bar["high"])
            low = float(bar["low"])
        except (KeyError, TypeError, ValueError):
            continue

        if direction == "LONG":
            favorable_r = (high - entry) / risk
            adverse_r = (low - entry) / risk
            reached_sl = reached_sl or low <= sl
            reached_tp1 = reached_tp1 or (tp1 is not None and high >= tp1)
            reached_tp2 = reached_tp2 or (tp2 is not None and high >= tp2)
        else:
            favorable_r = (entry - low) / risk
            adverse_r = (entry - high) / risk
            reached_sl = reached_sl or high >= sl
            reached_tp1 = reached_tp1 or (tp1 is not None and low <= tp1)
            reached_tp2 = reached_tp2 or (tp2 is not None and low <= tp2)

        max_favorable = max(max_favorable, favorable_r)
        max_adverse = min(max_adverse, adverse_r)
        reached_one_r = reached_one_r or favorable_r >= 1.0

    return {
        "max_favorable_excursion_r": round(max_favorable, 4),
        "max_adverse_excursion_r": round(max_adverse, 4),
        "reached_one_r": reached_one_r,
        "reached_tp1": reached_tp1,
        "reached_tp2": reached_tp2,
        "reached_sl": reached_sl,
    }


def reverse_direction_result_r(
    future_window: list[dict],
    *,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    cost_r: float = 0.0,
) -> float:
    """Gross R if the opposite direction had been taken with mirrored levels."""
    risk = abs(float(entry) - float(sl))
    if risk <= 0:
        return 0.0
    opp = "SHORT" if str(direction).upper() == "LONG" else "LONG"
    opp_sl = entry + (entry - sl) if opp == "SHORT" else entry - (sl - entry)
    opp_tp = entry - (tp - entry) if opp == "SHORT" else entry + (tp - entry)

    outcome_r = 0.0
    resolved = False
    for bar in future_window or []:
        high = float(bar["high"])
        low = float(bar["low"])
        sl_hit = low <= opp_sl if opp == "LONG" else high >= opp_sl
        tp_hit = high >= opp_tp if opp == "LONG" else low <= opp_tp
        if sl_hit and tp_hit:
            outcome_r = -1.0
            resolved = True
            break
        if sl_hit:
            outcome_r = -1.0
            resolved = True
            break
        if tp_hit:
            outcome_r = abs(opp_tp - entry) / risk
            resolved = True
            break
    if not resolved and future_window:
        close = float(future_window[-1]["close"])
        signed = close - entry if opp == "LONG" else entry - close
        outcome_r = signed / risk
    return round(outcome_r - cost_r, 4)


def _sqn(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    sd = stdev(values)
    if sd <= 0:
        return 0.0
    return round(fmean(values) / sd * sqrt(len(values)), 4)


def _pooled_metrics(results_r: list[float]) -> dict[str, Any]:
    n = len(results_r)
    if n == 0:
        return {
            "n": 0,
            "winRate": None,
            "expectancyR": None,
            "sqn": None,
            "profitFactor": None,
            "totalR": 0.0,
        }
    wins = [r for r in results_r if r > 0]
    losses = [r for r in results_r if r <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    exp = fmean(results_r)
    return {
        "n": n,
        "winRate": round(len(wins) / n * 100, 2),
        "expectancyR": round(exp, 4),
        "sqn": _sqn(results_r),
        "profitFactor": round(gp / gl, 4) if gl > 0 else None,
        "totalR": round(sum(results_r), 4),
    }


def summarize_v3_trades(trades: list[dict]) -> dict[str, Any]:
    """Unified cohort summary: exits, reverse edge, MFE, regime."""
    results = [float(t["resultR"]) for t in trades if "resultR" in t]
    reverse = [float(t["reverseResultR"]) for t in trades if t.get("reverseResultR") is not None]
    pooled = _pooled_metrics(results)
    reverse_pooled = _pooled_metrics(reverse) if reverse else {"n": 0}

    outcome_counts: dict[str, int] = {}
    for trade in trades:
        key = str(trade.get("outcome") or "UNKNOWN")
        outcome_counts[key] = outcome_counts.get(key, 0) + 1
    n = max(len(trades), 1)
    exit_pct = {k: round(v / len(trades) * 100, 1) for k, v in outcome_counts.items()} if trades else {}

    mfe_trades = [t for t in trades if t.get("max_favorable_excursion_r") is not None]
    mfe_ge_1r = [t for t in mfe_trades if float(t.get("max_favorable_excursion_r") or 0) >= 1.0]
    mfe_giveback = [
        t
        for t in mfe_ge_1r
        if float(t.get("resultR") or 0) <= 0
    ]

    regime_buckets: dict[str, list[float]] = {}
    for trade in trades:
        label = str(trade.get("regime") or "unknown").lower()
        regime_buckets.setdefault(label, []).append(float(trade["resultR"]))

    trade_regime: dict[str, Any] = {}
    for label, rows in sorted(regime_buckets.items()):
        wins = [r for r in rows if r > 0]
        trade_regime[label] = {
            "n": len(rows),
            "avg_r": round(fmean(rows), 4) if rows else 0.0,
            "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0.0,
            "total_r": round(sum(rows), 4),
        }

    return {
        **pooled,
        "reverse_gross_sqn": reverse_pooled.get("sqn"),
        "reverse_gross_avg_r": reverse_pooled.get("expectancyR"),
        "reverse_gross_total_r": reverse_pooled.get("totalR"),
        "exit_breakdown": outcome_counts,
        "exit_breakdown_pct": exit_pct,
        "avg_mfe_r": round(
            fmean(float(t.get("max_favorable_excursion_r") or 0) for t in mfe_trades), 4
        )
        if mfe_trades
        else None,
        "mfe_ge_1r_pct": round(len(mfe_ge_1r) / len(mfe_trades) * 100, 1) if mfe_trades else None,
        "mfe_ge_1r_then_nonpositive_pct_of_mfe_ge_1r": round(
            len(mfe_giveback) / len(mfe_ge_1r) * 100, 1
        )
        if mfe_ge_1r
        else None,
        "trade_regime": trade_regime,
    }
