"""AI Evaluation Engine — computes metrics across AI surfaces and produces reports.

Safety:
  - Read-only. Never modifies trade state, config, or thresholds.
  - Small-sample warnings prevent overclaiming.
  - All recommendations set do_not_auto_apply=True.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from ai_evaluation_contracts import AIEvaluationReport, AISurfaceMetrics
from ai_outcome_linker import classify_block_quality, link_samples_to_outcomes, load_ai_review_samples

log = logging.getLogger(__name__)

SAMPLE_INSUFFICIENT = 10
SAMPLE_MIN_FOR_METRICS = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_outcome(s: dict[str, Any]) -> bool:
    return bool(s.get("sample_valid"))


def _outcome_win(s: dict[str, Any]) -> bool:
    return str(s.get("actual_outcome", "")).upper() == "WIN"


def _outcome_loss(s: dict[str, Any]) -> bool:
    return str(s.get("actual_outcome", "")).upper() == "LOSS"


def _decision_is(s: dict[str, Any], target: str) -> bool:
    return str(s.get("ai_decision", "")).upper() == target.upper()


def _freshness_is_stale(value: Any) -> bool | None:
    """Classify measured freshness without treating missing/not-applicable as stale."""
    if value is None:
        return None
    status = str(value).strip().upper()
    if status in ("", "UNKNOWN", "NOT_APPLICABLE", "N/A", "NONE", "NULL", "SERVER_RENDER"):
        return None
    if status in ("FRESH", "CONFIRMED_CANDLES_VALID"):
        return False
    if status == "STALE" or status.startswith("STALE_DATA_"):
        return True
    return None


def compute_surface_metrics(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not samples:
        return {}
    surfaces: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        surface = str(s.get("ai_surface", "UNKNOWN"))
        surfaces.setdefault(surface, []).append(s)
    result: dict[str, dict[str, Any]] = {}
    for surface, group in surfaces.items():
        metrics = _compute_one_surface(surface, group)
        if metrics:
            result[surface] = metrics
    return result


def _compute_one_surface(surface: str, samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not samples:
        return None
    n = len(samples)
    valid = [s for s in samples if _valid_outcome(s)]
    valid_n = len(valid)
    insufficient_n = n - valid_n

    parse_fails = [s for s in samples if not s.get("parse_success", True)]
    parse_failure_rate = len(parse_fails) / n if n > 0 else 0.0

    missing_data = [s for s in samples if s.get("contradiction_flags") or s.get("missing_outcome_reason")]
    missing_data_rate = len(missing_data) / n if n > 0 else 0.0

    contradictions = [s for s in samples if s.get("contradiction_flags")]
    contradiction_rate = len(contradictions) / n if n > 0 else 0.0

    blocks = [s for s in samples if s.get("ai_blocked")]
    block_count = len(blocks)

    useful_blocks = sum(1 for s in samples if classify_block_quality(s).get("useful_block"))
    false_blocks = sum(1 for s in samples if classify_block_quality(s).get("false_block"))
    harmful_allows = sum(1 for s in samples if classify_block_quality(s).get("harmful_allow"))

    positive = [s for s in valid if _decision_is(s, "POSITIVE")]
    wins = [s for s in positive if _outcome_win(s)]
    win_rate = len(wins) / len(positive) if positive else None
    r_vals = [s.get("realized_r") for s in positive if s.get("realized_r") is not None]
    avg_r = (sum(r_vals) / len(r_vals)) if r_vals else None

    freshness = [_freshness_is_stale(s.get("data_freshness")) for s in samples]
    measured_freshness = [is_stale for is_stale in freshness if is_stale is not None]
    stale_rate = (
        sum(1 for is_stale in measured_freshness if is_stale) / len(measured_freshness)
        if measured_freshness
        else None
    )

    notes: list[str] = []
    if n < SAMPLE_INSUFFICIENT:
        notes.append(f"Sample too small ({n}) for reliable surface metrics.")
    if valid_n < SAMPLE_MIN_FOR_METRICS:
        notes.append(f"Only {valid_n}/{n} samples have linked outcomes.")
    if not positive:
        notes.append("No positive AI decisions to compute win rate / avg R.")

    return AISurfaceMetrics(
        ai_surface=surface,
        sample_count=n,
        valid_outcome_count=valid_n,
        insufficient_count=insufficient_n,
        win_rate_when_positive=round(win_rate, 3) if win_rate is not None else None,
        avg_r_when_positive=round(avg_r, 3) if avg_r is not None else None,
        block_count=block_count,
        useful_block_count=useful_blocks,
        false_block_count=false_blocks,
        harmful_allow_count=harmful_allows,
        contradiction_rate=round(contradiction_rate, 3),
        missing_data_rate=round(missing_data_rate, 3),
        parse_failure_rate=round(parse_failure_rate, 3),
        stale_context_rate=round(stale_rate, 3) if stale_rate is not None else None,
        notes=notes,
    ).model_dump()


def compute_engine_asset_breakdowns(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dimension in ("engine", "asset_type", "timeframe", "setup_type"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for s in samples:
            key = str(s.get(dimension, "unknown")).strip() or "unknown"
            groups.setdefault(key, []).append(s)
        dim_data: dict[str, Any] = {}
        for key, group in groups.items():
            valid = [s for s in group if _valid_outcome(s)]
            wins = [s for s in valid if _outcome_win(s)]
            losses = [s for s in valid if _outcome_loss(s)]
            r_vals = [s.get("realized_r") for s in valid if s.get("realized_r") is not None]
            dim_data[key] = {
                "total_samples": len(group),
                "valid_outcomes": len(valid),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(valid), 3) if valid else None,
                "avg_r": round(sum(r_vals) / len(r_vals), 3) if r_vals else None,
            }
        result[dimension] = dim_data
    return result


def evaluate_vision_accuracy(samples: list[dict[str, Any]]) -> dict[str, Any]:
    vision = [s for s in samples if str(s.get("ai_surface", "")).upper() == "VISION"]
    if not vision:
        return {"warning": "No Vision samples found.", "sample_count": 0}
    total = len(vision)
    valid = [s for s in vision if _valid_outcome(s)]
    stale = [s for s in vision if str(s.get("vision_freshness", "")).upper() in ("STALE", "UNKNOWN", "")]
    stale_rate = len(stale) / total if total > 0 else 0.0
    confirms = [s for s in vision if _decision_is(s, "POSITIVE")]
    contradicts = [s for s in vision if _decision_is(s, "NEGATIVE")]
    confirms_win = [s for s in confirms if s.get("sample_valid") and _outcome_win(s)]
    contradicts_loss = [s for s in contradicts if s.get("sample_valid") and _outcome_loss(s)]
    confirms_win_rate = round(len(confirms_win) / len(confirms), 3) if confirms else None
    contradicts_loss_rate = round(len(contradicts_loss) / len(contradicts), 3) if contradicts else None
    re_ok = [s for s in vision if s.get("data_freshness") in ("fresh", "FRESH") and s.get("parse_success")]
    re_reliable = (len([s for s in re_ok if _valid_outcome(s) and _outcome_win(s)]) / len(re_ok)) if re_ok else None
    return {
        "sample_count": total,
        "valid_outcome_count": len(valid),
        "stale_vision_rate": round(stale_rate, 3),
        "confirms_count": len(confirms),
        "confirms_win_rate": confirms_win_rate,
        "contradicts_count": len(contradicts),
        "contradicts_loss_rate": contradicts_loss_rate,
        "right_edge_reliability": round(re_reliable, 3) if re_reliable is not None else None,
        "parse_failures": len([s for s in vision if not s.get("parse_success")]),
        "missing_freshness": len(stale),
    }


def evaluate_strategist_accuracy(samples: list[dict[str, Any]]) -> dict[str, Any]:
    strat = [s for s in samples if str(s.get("ai_surface", "")).upper() == "STRATEGIST"]
    if not strat:
        return {"warning": "No Strategist samples found.", "sample_count": 0}
    total = len(strat)
    valid = [s for s in strat if _valid_outcome(s)]
    concurs = [s for s in strat if _decision_is(s, "POSITIVE") or _decision_is(s, "NEUTRAL")]
    objects = [s for s in strat if _decision_is(s, "NEGATIVE")]
    concur_wins = [s for s in concurs if _valid_outcome(s) and _outcome_win(s)]
    object_losses = [s for s in objects if _valid_outcome(s) and _outcome_loss(s)]
    return {
        "sample_count": total,
        "valid_outcome_count": len(valid),
        "concurs": len(concurs),
        "concur_win_rate": round(len(concur_wins) / len(concurs), 3) if concurs else None,
        "objects": len(objects),
        "object_accuracy": round(len(object_losses) / len(objects), 3) if objects else None,
        "false_objections": len(objects) - len(object_losses) if objects else 0,
        "warnings": [] if len(valid) >= 5 else ["Small sample - strategist metrics are preliminary."],
    }


def evaluate_debate_gate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    debate = [s for s in samples if str(s.get("ai_surface", "")).upper() == "DEBATE"]
    if not debate:
        return {"warning": "No Debate samples found.", "sample_count": 0}
    total = len(debate)
    blocks = [s for s in debate if s.get("ai_blocked")]
    allows = [s for s in debate if not s.get("ai_blocked")]
    useful_blocks = sum(1 for s in blocks if classify_block_quality(s).get("useful_block"))
    false_blocks = sum(1 for s in blocks if classify_block_quality(s).get("false_block"))
    harmful_allows = sum(1 for s in allows if classify_block_quality(s).get("harmful_allow"))
    parse_fails = [s for s in debate if not s.get("parse_success")]
    return {
        "sample_count": total,
        "valid_outcome_count": len([s for s in debate if _valid_outcome(s)]),
        "total_blocks": len(blocks),
        "useful_blocks": useful_blocks,
        "false_blocks": false_blocks,
        "harmful_allows": harmful_allows,
        "parse_failures": len(parse_fails),
        "safety_fallbacks": len([s for s in debate if _decision_is(s, "INSUFFICIENT")]),
    }


def generate_ai_evaluation_report(lookback_days: int = 30) -> dict[str, Any]:
    samples = load_ai_review_samples(lookback_days=lookback_days)
    linked = link_samples_to_outcomes(samples)

    surface_metrics = compute_surface_metrics(linked)
    breakdowns = compute_engine_asset_breakdowns(linked)
    vision = evaluate_vision_accuracy(linked)
    strategist = evaluate_strategist_accuracy(linked)
    debate = evaluate_debate_gate(linked)

    total = len(linked)
    valid = sum(1 for s in linked if _valid_outcome(s))
    wins = sum(1 for s in linked if _valid_outcome(s) and _outcome_win(s))
    losses = sum(1 for s in linked if _valid_outcome(s) and _outcome_loss(s))
    r_vals = [s.get("realized_r") for s in linked if _valid_outcome(s) and s.get("realized_r") is not None]
    overall_win_rate = round(wins / valid, 3) if valid else None
    overall_avg_r = round(sum(r_vals) / len(r_vals), 3) if r_vals else None

    best: list[str] = []
    worst: list[str] = []
    recs: list[str] = []
    warnings: list[str] = []

    for surface_name, metrics in surface_metrics.items():
        m = AISurfaceMetrics(**metrics)
        if m.useful_block_count >= 3 and m.false_block_count == 0:
            best.append(f"{surface_name}: {m.useful_block_count} useful blocks, 0 false blocks.")
        elif m.useful_block_count >= 3 and m.false_block_count <= m.useful_block_count * 0.3:
            best.append(f"{surface_name}: {m.useful_block_count} useful blocks, {m.false_block_count} false blocks.")
        if m.harmful_allow_count >= 3:
            worst.append(f"{surface_name}: {m.harmful_allow_count} harmful allows.")
        if m.false_block_count >= m.block_count * 0.5 and m.block_count >= 5:
            worst.append(f"{surface_name}: {m.false_block_count}/{m.block_count} blocks were false.")
        if m.parse_failure_rate and m.parse_failure_rate > 0.2:
            worst.append(f"{surface_name}: {round(m.parse_failure_rate * 100)}% parse failure rate.")
        if m.notes:
            warnings.extend(m.notes)
        if m.win_rate_when_positive is not None and m.win_rate_when_positive < 0.4:
            recs.append(f"{surface_name}: Low win rate ({round(m.win_rate_when_positive * 100)}%) when AI positive. Review positive-decision criteria.")
        if m.sample_count < SAMPLE_INSUFFICIENT:
            warnings.append(f"{surface_name}: Only {m.sample_count} samples. Metrics are not reliable.")
        if m.stale_context_rate and m.stale_context_rate > 0.3:
            recs.append(f"{surface_name}: High stale context rate ({round(m.stale_context_rate * 100)}%). Check freshness monitoring.")

    if total < SAMPLE_INSUFFICIENT:
        warnings.append(f"Total evaluation sample too small ({total}). Report is preliminary.")
        overall_summary = f"Insufficient data: {total} samples with {valid} linked outcomes. No strong conclusions possible."
    elif valid < SAMPLE_MIN_FOR_METRICS:
        overall_summary = f"Only {valid}/{total} samples have linked outcomes. Wait for more trade data."
    else:
        overall_summary = (
            f"Evaluated {total} AI samples ({valid} with outcomes, {wins} wins, {losses} losses). "
            f"Overall win rate: {overall_win_rate}, avg R: {overall_avg_r}. "
        )

    report = AIEvaluationReport(
        report_id=f"eval_{uuid.uuid4().hex[:12]}",
        generated_at=_now_iso(),
        lookback_days=lookback_days,
        surfaces=list(surface_metrics.keys()),
        overall_summary=overall_summary,
        surface_metrics={k: AISurfaceMetrics(**v) for k, v in surface_metrics.items()},
        best_ai_behaviors=best,
        worst_ai_behaviors=worst,
        recommendations=recs,
        do_not_auto_apply=True,
        warnings=warnings,
    )
    report_dict = report.model_dump()
    report_dict["vision"] = vision
    report_dict["strategist"] = strategist
    report_dict["debate"] = debate
    report_dict["breakdowns"] = breakdowns
    report_dict["total_samples"] = total
    report_dict["valid_outcomes"] = valid
    report_dict["overall_win_rate"] = overall_win_rate
    report_dict["overall_avg_r"] = overall_avg_r
    return report_dict
