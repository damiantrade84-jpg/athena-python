"""Anti-overfit checks for promising diagnostic results (Part 9)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import fmean
from typing import Any


MIN_TRADES = 30
MIN_TRADES_STRICT = 60
MAX_AMBIGUITY_PCT = 15.0
MIN_PF = 1.2
MIN_SQN = 1.0


def _parse_quarter(ts: str | None) -> str:
    if not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"
    except (TypeError, ValueError):
        return "unknown"


def _profit_factor(values: list[float]) -> float | None:
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None
    return sum(wins) / gl


def _sqn(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    from math import sqrt
    from statistics import stdev

    sd = stdev(values)
    if sd <= 0:
        return 0.0
    return fmean(values) / sd * sqrt(len(values))


def evaluate_cohort(
    trades: list[dict],
    *,
    label: str,
    engine: str,
    symbol: str,
    signal_tf: str,
    entry_tf: str,
    indicator_profile: str,
    validated_by_athena: bool = True,
    vectorbt_only: bool = False,
) -> dict[str, Any]:
    """Run anti-overfit checks on a trade cohort."""
    if not trades:
        return {
            "label": label,
            "engine": engine,
            "symbol": symbol,
            "signal_tf": signal_tf,
            "entry_tf": entry_tf,
            "indicator_profile": indicator_profile,
            "verdict": "BLOCKED_DATA",
            "reasons": ["no_trades"],
            "trade_count": 0,
            "validated_by_athena": validated_by_athena,
            "vectorbt_only": vectorbt_only,
        }

    rs = [float(t.get("final_r") or t.get("resultR") or 0) for t in trades]
    rs_gross = [float(t.get("cost_adjusted_r") or t.get("final_r") or t.get("resultR") or 0) for t in trades]
    n = len(rs)
    reasons: list[str] = []
    verdict = "PASS"

    if n < MIN_TRADES:
        reasons.append(f"sample_too_small:n={n}<{MIN_TRADES}")
        verdict = "WATCHLIST"

    avg_r = fmean(rs)
    pf = _profit_factor(rs)
    sqn = _sqn(rs)
    ambiguous = sum(1 for t in trades if t.get("intrabar_ambiguous") or t.get("intrabar_exit_class") == "BOTH_TOUCHED_SAME_CANDLE")
    amb_pct = 100.0 * ambiguous / n

    if avg_r <= 0:
        verdict = "FAIL"
        reasons.append("negative_avg_r")
    if pf is not None and pf < MIN_PF:
        if verdict == "PASS":
            verdict = "WATCHLIST"
        reasons.append(f"pf_below_{MIN_PF}:pf={pf:.2f}")
    if sqn < MIN_SQN and avg_r > 0:
        verdict = "FAIL" if verdict != "BLOCKED_DATA" else verdict
        reasons.append(f"sqn_below_{MIN_SQN}:sqn={sqn:.2f}")
    if amb_pct > MAX_AMBIGUITY_PCT:
        verdict = "BLOCKED_INTRABAR_AMBIGUITY"
        reasons.append(f"ambiguity_rate={amb_pct:.1f}%")

    # Quarter concentration
    by_q: dict[str, list[float]] = defaultdict(list)
    for t, r in zip(trades, rs):
        by_q[_parse_quarter(t.get("entry_timestamp") or t.get("date"))].append(r)
    if by_q:
        q_totals = {q: sum(v) for q, v in by_q.items()}
        total_profit = sum(v for v in q_totals.values() if v > 0)
        if total_profit > 0:
            max_q_share = max(q_totals.values()) / total_profit if total_profit else 0
            if max_q_share > 0.6:
                reasons.append(f"one_quarter_dominates:{max_q_share:.0%}")
                if verdict == "PASS":
                    verdict = "WATCHLIST"

    # Direction split
    long_rs = [float(t.get("final_r") or 0) for t in trades if str(t.get("direction")).upper() == "LONG"]
    short_rs = [float(t.get("final_r") or 0) for t in trades if str(t.get("direction")).upper() == "SHORT"]
    if long_rs and short_rs:
        if fmean(long_rs) > 0 and fmean(short_rs) <= 0:
            reasons.append("long_hides_short_failure")
            if verdict == "PASS":
                verdict = "WATCHLIST"
        if fmean(short_rs) > 0 and fmean(long_rs) <= 0:
            reasons.append("short_hides_long_failure")
            if verdict == "PASS":
                verdict = "WATCHLIST"

    if vectorbt_only:
        verdict = "WATCHLIST" if verdict == "PASS" else verdict
        reasons.append("vectorbt_provisional_only")

    if not validated_by_athena and not vectorbt_only:
        verdict = "BLOCKED_ASSUMPTION_MISMATCH"
        reasons.append("not_validated_by_athena_harness")

    return {
        "label": label,
        "engine": engine,
        "symbol": symbol,
        "signal_tf": signal_tf,
        "entry_tf": entry_tf,
        "indicator_profile": indicator_profile,
        "verdict": verdict,
        "reasons": reasons,
        "trade_count": n,
        "avg_r": round(avg_r, 4),
        "avg_r_cost_adjusted": round(fmean(rs_gross), 4),
        "sqn": round(sqn, 4),
        "profit_factor": round(pf, 4) if pf is not None else None,
        "ambiguity_pct": round(amb_pct, 2),
        "validated_by_athena": validated_by_athena,
        "vectorbt_only": vectorbt_only,
    }
