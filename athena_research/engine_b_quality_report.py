"""Pure, diagnostic-only Engine B quality-layer reporting helpers."""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable


QUALITY_LAYERS = (
    ("candidate_floor", "engine_b_candidate_passed"),
    ("manual_absolute_6_5", "engine_b_manual_quality_passed_score_6_5"),
    ("manual_normalized_0_75", "engine_b_manual_quality_passed_ratio_0_75"),
    ("strict_autotrade_quality_0_80", "engine_b_autotrade_quality_passed_ratio_0_80"),
    ("normal_scheduler_autotrade_reachable", "autotradeIngressEligible"),
)


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def annotate_engine_b_trade(trade: dict, *, default_auto_min: float = 0.50) -> dict:
    """Return a copy with reporting fields; never changes trading decisions."""
    row = dict(trade)
    nested = row.get("naked_data") or row.get("engine_b") or row.get("engine_b_status") or {}
    if not isinstance(nested, dict):
        nested = {}
    score = _number(row.get("engine_b_score", row.get("score", nested.get("score"))), 0.0) or 0.0
    max_score = _number(
        row.get("engine_b_max", row.get("max_possible", nested.get("max_possible"))), 0.0
    ) or 0.0
    ratio = score / max_score if max_score > 0 else 0.0
    min_score = _number(
        row.get("engine_b_min_score_used", row.get("min_score_used", nested.get("min_score_used"))),
        0.0,
    ) or 0.0
    aligned = bool(row.get("alignedDiscountApplied", row.get("enginesAligned", False)))
    meta_delta = _number(row.get("metaThresholdDelta"), 0.0) or 0.0
    auto_min = _number(row.get("autoMinConviction"))
    if auto_min is None:
        auto_min = max(0.0, min(1.0, default_auto_min * (0.85 if aligned else 1.0) + meta_delta))
    base = _number(row.get("executionConvictionBase"), ratio)
    effective = _number(row.get("executionConvictionEffective"), base)
    tier = str(row.get("signalTier") or "")
    b_only_reason = row.get("b_only_watchlist_reason")
    if not b_only_reason and tier.lower() == "watchlist":
        b_only_reason = row.get("signalTierReason") or row.get("watchlistReason")
    blocked_reason = row.get("engine_b_execution_block_reason")
    b_only = bool(
        tier.lower() == "watchlist"
        and (
            row.get("engine_b_confidence_passed") is True
            or blocked_reason == "engine_b_only_scan_watchlist"
            or "engine b-only" in str(b_only_reason or "").lower()
        )
    )
    ingress = bool(row.get("autotradeIngressEligible", tier.lower() == "trade" and not b_only))

    row.update(
        {
            "engine_b_score": score,
            "engine_b_max": max_score,
            "engine_b_score_ratio": round(ratio, 6),
            "engine_b_min_score_used": min_score,
            "engine_b_candidate_passed": bool(score >= min_score),
            "engine_b_manual_quality_passed_score_6_5": bool(score >= 6.5),
            "engine_b_manual_quality_passed_ratio_0_75": bool(ratio >= 0.75),
            "engine_b_autotrade_quality_passed_ratio_0_80": bool(ratio >= 0.80),
            "signalTier": tier or None,
            "autotradeIngressEligible": ingress,
            "b_only_watchlist_reason": b_only_reason,
            "executionConvictionBase": round(float(base or 0.0), 6),
            "executionConvictionEffective": round(float(effective or 0.0), 6),
            "autoMinConviction": round(float(auto_min), 6),
            "alignedDiscountApplied": aligned,
            "metaThresholdDelta": meta_delta,
            "failedStage": row.get("failedStage") or (row.get("autoDecisionTrace") or {}).get("failedStage"),
            "blockReason": row.get("blockReason") or (row.get("autoDecisionTrace") or {}).get("blockReason"),
            "engine_b_default_conviction_passed": bool((effective or 0.0) >= auto_min),
        }
    )
    return row


def _metrics(rows: list[dict], total: int) -> dict:
    r_values = [float(row.get("resultR", row.get("r_multiple", 0.0)) or 0.0) for row in rows]
    scores = [float(row["engine_b_score"]) for row in rows]
    ratios = [float(row["engine_b_score_ratio"]) for row in rows]
    maxima = [float(row["engine_b_max"]) for row in rows]
    wins = [value for value in r_values if value > 0]
    losses = [value for value in r_values if value <= 0]
    n = len(rows)
    mean = statistics.fmean(r_values) if r_values else 0.0
    sd = statistics.stdev(r_values) if len(r_values) > 1 else 0.0
    equity = peak = max_dd = 0.0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    gross_loss = abs(sum(losses))
    return {
        "n": n,
        "retained_pct": round(100.0 * n / total, 2) if total else 0.0,
        "win_pct": round(100.0 * len(wins) / n, 2) if n else None,
        "expR": round(mean, 4) if n else None,
        "SQN": round(max(-10.0, min(10.0, mean / sd * math.sqrt(n))), 3) if sd else None,
        "profit_factor": round(sum(wins) / gross_loss, 3) if gross_loss else None,
        "avg_win_R": round(statistics.fmean(wins), 4) if wins else None,
        "avg_loss_R": round(statistics.fmean(losses), 4) if losses else None,
        "max_drawdown_R": round(max_dd, 3),
        "median_score": round(statistics.median(scores), 4) if scores else None,
        "median_score_ratio": round(statistics.median(ratios), 6) if ratios else None,
        "min_score": min(scores) if scores else None,
        "max_score": max(scores) if scores else None,
        "min_maxScore": min(maxima) if maxima else None,
        "max_maxScore": max(maxima) if maxima else None,
    }


def build_engine_b_quality_rows(trades: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Return annotated trades and pair/direction quality-layer metrics."""
    annotated = [annotate_engine_b_trade(row) for row in trades]
    cohorts: dict[tuple[str, str], list[dict]] = {}
    for row in annotated:
        pair = str(row.get("pair") or row.get("display") or "UNKNOWN")
        direction = str(row.get("direction") or "UNKNOWN").upper()
        cohorts.setdefault((pair, direction), []).append(row)
    report = []
    for (pair, direction), cohort in sorted(cohorts.items()):
        for layer, field in QUALITY_LAYERS:
            kept = [row for row in cohort if row.get(field) is True]
            report.append(
                {"pair": pair, "direction": direction, "quality_layer": layer}
                | _metrics(kept, len(cohort))
            )
    return annotated, report
