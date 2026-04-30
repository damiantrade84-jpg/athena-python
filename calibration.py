"""Lightweight empirical probability calibration for Athena.

Uses auditable score buckets with smoothed empirical win rates and
graceful fallback across engine / asset_class / style scopes.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

_DEFAULT_BUCKET_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.000001)
_DEFAULT_MIN_BUCKET_SAMPLES = 5
_DEFAULT_MIN_TOTAL_SAMPLES = 20
_DEFAULT_PRIOR_WEIGHT = 3.0
_CACHE_TTL_SEC = 60.0

_RECORD_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CALIBRATOR_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}


def _default_db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")


def _norm_text(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _clamp01(value: Any) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_score(
    raw_score: Any,
    max_score: Any = None,
    score_pct: Any = None,
    default_max_score: Any = None,
) -> float | None:
    pct = _safe_float(score_pct)
    if pct is not None:
        if pct > 1.0:
            pct /= 100.0
        return _clamp01(pct)

    score = _safe_float(raw_score)
    if score is None:
        return None

    max_candidate = _safe_float(max_score)
    if not max_candidate or max_candidate <= 0:
        max_candidate = _safe_float(default_max_score)
    if max_candidate and max_candidate > 0:
        return _clamp01(score / max_candidate)

    if 0.0 <= score <= 1.0:
        return score
    if 0.0 <= score <= 100.0:
        return _clamp01(score / 100.0)
    return _clamp01(score)


def _normalize_won(record: dict[str, Any]) -> int | None:
    won = record.get("won")
    if won is not None:
        return 1 if bool(won) else 0
    realized = _safe_float(record.get("realized_win"))
    if realized is not None:
        return 1 if realized > 0 else 0
    pnl = _safe_float(record.get("pnl"))
    if pnl is not None:
        return 1 if pnl > 0 else 0
    expectancy_r = _safe_float(record.get("expectancy_r"))
    if expectancy_r is not None:
        return 1 if expectancy_r > 0 else 0
    result_r = _safe_float(record.get("resultR"))
    if result_r is not None:
        return 1 if result_r > 0 else 0
    r_multiple = _safe_float(record.get("r_multiple"))
    if r_multiple is not None:
        return 1 if r_multiple > 0 else 0
    return None


def _record_scope_match(
    record: dict[str, Any],
    engine: str | None,
    asset_class: str | None,
    style: str | None,
) -> bool:
    if engine and _norm_text(record.get("engine")) != _norm_text(engine):
        return False
    if asset_class and _norm_text(record.get("asset_class")) != _norm_text(asset_class):
        return False
    if style and _norm_text(record.get("style")) != _norm_text(style):
        return False
    return True


def _bucket_index(score_norm: float, bucket_edges: tuple[float, ...]) -> int:
    for idx in range(len(bucket_edges) - 1):
        low = bucket_edges[idx]
        high = bucket_edges[idx + 1]
        if low <= score_norm < high or (idx == len(bucket_edges) - 2 and score_norm <= high):
            return idx
    return max(0, len(bucket_edges) - 2)


def _infer_engine_from_audit_row(row: sqlite3.Row) -> str | None:
    style = _norm_text(row["style"])
    grade = _norm_text(row["grade"])
    max_score = _safe_float(row["max_score"])

    if style == "scalp" or grade == "scalp":
        return "scalp"
    if style in ("engine_c", "consensus"):
        return "engine_c"
    if max_score is not None:
        return "engine_a"
    if style in ("structural", "naked"):
        return "engine_b"
    return None


def _load_records_from_audit_db(db_path: str | None = None) -> list[dict[str, Any]]:
    path = db_path or _default_db_path()
    now = time.time()
    cached = _RECORD_CACHE.get(path)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]

    records: list[dict[str, Any]] = []
    if not os.path.exists(path):
        _RECORD_CACHE[path] = (now, records)
        return records

    try:
        with sqlite3.connect(path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT score, max_score, score_pct, asset_class, style, grade,
                       pnl, r_multiple, exit_price, error_tag
                FROM audit_log
                WHERE exit_price IS NOT NULL
                  AND pnl IS NOT NULL
                  AND (error_tag IS NULL OR error_tag = '')
                """
            ).fetchall()
    except Exception:
        rows = []

    for row in rows:
        engine = _infer_engine_from_audit_row(row)
        if not engine:
            continue
        records.append(
            {
                "engine": engine,
                "asset_class": row["asset_class"],
                "style": row["style"],
                "raw_score": row["score"],
                "max_score": row["max_score"],
                "score_pct": row["score_pct"],
                "won": bool(row["pnl"] > 0) if row["pnl"] is not None else None,
                "pnl": row["pnl"],
                "r_multiple": row["r_multiple"],
            }
        )

    _RECORD_CACHE[path] = (now, records)
    return records


def fit_calibrator(
    records: list[dict[str, Any]],
    *,
    engine: str | None = None,
    asset_class: str | None = None,
    style: str | None = None,
    bucket_edges: tuple[float, ...] | None = None,
    min_bucket_samples: int = _DEFAULT_MIN_BUCKET_SAMPLES,
    min_total_samples: int = _DEFAULT_MIN_TOTAL_SAMPLES,
    prior_weight: float = _DEFAULT_PRIOR_WEIGHT,
    default_max_score: float | None = None,
) -> dict[str, Any]:
    edges = tuple(bucket_edges or _DEFAULT_BUCKET_EDGES)
    scoped = [
        record
        for record in (records or [])
        if _record_scope_match(record, engine, asset_class, style)
    ]

    usable: list[dict[str, Any]] = []
    for record in scoped:
        score_norm = _normalize_score(
            record.get("raw_score", record.get("score")),
            max_score=record.get("max_score"),
            score_pct=record.get("score_pct"),
            default_max_score=default_max_score,
        )
        won = _normalize_won(record)
        if score_norm is None or won is None:
            continue
        usable.append(
            {
                "score_norm": score_norm,
                "won": won,
            }
        )

    total = len(scoped)
    usable_count = len(usable)
    overall_win_rate = (
        sum(item["won"] for item in usable) / usable_count if usable_count else None
    )
    prior_prob = overall_win_rate if overall_win_rate is not None else 0.5

    buckets = []
    for idx in range(len(edges) - 1):
        low = edges[idx]
        high = edges[idx + 1]
        bucket_rows = [
            item for item in usable if idx == _bucket_index(item["score_norm"], edges)
        ]
        n = len(bucket_rows)
        wins = sum(item["won"] for item in bucket_rows)
        empirical_prob = (wins / n) if n else None
        smoothed_prob = ((wins + prior_prob * prior_weight) / (n + prior_weight)) if (n or prior_weight) else prior_prob
        buckets.append(
            {
                "index": idx,
                "low": round(low, 6),
                "high": round(high if high <= 1.0 else 1.0, 6),
                "sample_count": n,
                "wins": wins,
                "empirical_prob": round(empirical_prob, 6) if empirical_prob is not None else None,
                "smoothed_prob": round(smoothed_prob, 6),
                "status": "ok" if n >= min_bucket_samples else ("low_sample" if n > 0 else "empty"),
            }
        )

    return {
        "available": usable_count > 0,
        "engine": _norm_text(engine),
        "asset_class": _norm_text(asset_class),
        "style": _norm_text(style),
        "scope": {
            "engine": _norm_text(engine),
            "asset_class": _norm_text(asset_class),
            "style": _norm_text(style),
        },
        "bucket_edges": list(edges),
        "bucket_count": len(buckets),
        "total_records": total,
        "usable_records": usable_count,
        "overall_win_rate": round(overall_win_rate, 6) if overall_win_rate is not None else None,
        "prior_prob": round(prior_prob, 6),
        "min_bucket_samples": int(min_bucket_samples),
        "min_total_samples": int(min_total_samples),
        "default_max_score": default_max_score,
        "buckets": buckets,
        "warnings": _fit_warnings(usable_count, min_total_samples, buckets, min_bucket_samples),
    }


def _fit_warnings(
    usable_count: int,
    min_total_samples: int,
    buckets: list[dict[str, Any]],
    min_bucket_samples: int,
) -> list[str]:
    warnings = []
    if usable_count == 0:
        warnings.append("no_calibration_records")
        return warnings
    if usable_count < min_total_samples:
        warnings.append(
            f"low_total_sample_count ({usable_count} < {min_total_samples})"
        )
    low_sample_buckets = sum(1 for bucket in buckets if bucket["sample_count"] and bucket["sample_count"] < min_bucket_samples)
    empty_buckets = sum(1 for bucket in buckets if bucket["sample_count"] == 0)
    if low_sample_buckets:
        warnings.append(f"{low_sample_buckets}_low_sample_buckets")
    if empty_buckets:
        warnings.append(f"{empty_buckets}_empty_buckets")
    return warnings


def _cache_key(
    path: str | None,
    engine: str | None,
    asset_class: str | None,
    style: str | None,
    bucket_edges: tuple[float, ...],
    min_bucket_samples: int,
    min_total_samples: int,
    prior_weight: float,
    default_max_score: float | None,
) -> tuple:
    return (
        path or _default_db_path(),
        _norm_text(engine),
        _norm_text(asset_class),
        _norm_text(style),
        tuple(bucket_edges),
        int(min_bucket_samples),
        int(min_total_samples),
        float(prior_weight),
        default_max_score,
    )


def _get_cached_calibrator(
    *,
    db_path: str | None,
    records: list[dict[str, Any]] | None,
    engine: str | None,
    asset_class: str | None,
    style: str | None,
    bucket_edges: tuple[float, ...],
    min_bucket_samples: int,
    min_total_samples: int,
    prior_weight: float,
    default_max_score: float | None,
) -> dict[str, Any]:
    if records is not None:
        return fit_calibrator(
            records,
            engine=engine,
            asset_class=asset_class,
            style=style,
            bucket_edges=bucket_edges,
            min_bucket_samples=min_bucket_samples,
            min_total_samples=min_total_samples,
            prior_weight=prior_weight,
            default_max_score=default_max_score,
        )

    path = db_path or _default_db_path()
    key = _cache_key(
        path,
        engine,
        asset_class,
        style,
        bucket_edges,
        min_bucket_samples,
        min_total_samples,
        prior_weight,
        default_max_score,
    )
    now = time.time()
    cached = _CALIBRATOR_CACHE.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]

    calibrator = fit_calibrator(
        _load_records_from_audit_db(path),
        engine=engine,
        asset_class=asset_class,
        style=style,
        bucket_edges=bucket_edges,
        min_bucket_samples=min_bucket_samples,
        min_total_samples=min_total_samples,
        prior_weight=prior_weight,
        default_max_score=default_max_score,
    )
    _CALIBRATOR_CACHE[key] = (now, calibrator)
    return calibrator


def _scope_candidates(engine: str | None, asset_class: str | None, style: str | None) -> list[tuple[str | None, str | None, str | None]]:
    candidates = [
        (engine, asset_class, style),
        (engine, asset_class, None),
        (engine, None, style),
        (engine, None, None),
        (None, asset_class, style),
        (None, asset_class, None),
        (None, None, style),
        (None, None, None),
    ]
    deduped = []
    seen = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _pick_best_calibrator(
    *,
    records: list[dict[str, Any]] | None,
    db_path: str | None,
    engine: str | None,
    asset_class: str | None,
    style: str | None,
    bucket_edges: tuple[float, ...],
    min_bucket_samples: int,
    min_total_samples: int,
    prior_weight: float,
    default_max_score: float | None,
) -> tuple[dict[str, Any] | None, str | None]:
    fallback_reason = None
    best_low_sample: dict[str, Any] | None = None

    for cand_engine, cand_asset, cand_style in _scope_candidates(engine, asset_class, style):
        calibrator = _get_cached_calibrator(
            db_path=db_path,
            records=records,
            engine=cand_engine,
            asset_class=cand_asset,
            style=cand_style,
            bucket_edges=bucket_edges,
            min_bucket_samples=min_bucket_samples,
            min_total_samples=min_total_samples,
            prior_weight=prior_weight,
            default_max_score=default_max_score,
        )
        if not calibrator.get("available"):
            continue
        if calibrator.get("usable_records", 0) >= min_total_samples:
            scope = calibrator["scope"]
            if scope != {"engine": _norm_text(engine), "asset_class": _norm_text(asset_class), "style": _norm_text(style)}:
                fallback_reason = f"fallback_to_broader_scope:{scope}"
            return calibrator, fallback_reason
        if best_low_sample is None:
            best_low_sample = calibrator

    if best_low_sample is not None:
        fallback_reason = "low_sample_broader_scope"
        return best_low_sample, fallback_reason
    return None, "no_calibration_records"


def predict_calibrated_prob(
    raw_score: float | None,
    *,
    engine: str | None = None,
    asset_class: str | None = None,
    style: str | None = None,
    max_score: float | None = None,
    score_pct: float | None = None,
    calibrator: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
    bucket_edges: tuple[float, ...] | None = None,
    min_bucket_samples: int = _DEFAULT_MIN_BUCKET_SAMPLES,
    min_total_samples: int = _DEFAULT_MIN_TOTAL_SAMPLES,
    prior_weight: float = _DEFAULT_PRIOR_WEIGHT,
    default_max_score: float | None = None,
) -> dict[str, Any]:
    score_norm = _normalize_score(
        raw_score,
        max_score=max_score,
        score_pct=score_pct,
        default_max_score=default_max_score,
    )
    if score_norm is None:
        return {
            "available": False,
            "calibrated_prob": None,
            "raw_score_norm": None,
            "fallback_reason": "invalid_raw_score",
            "sample_count": 0,
            "bucket_samples": 0,
            "scope_used": None,
        }

    edges = tuple(bucket_edges or _DEFAULT_BUCKET_EDGES)
    selected = calibrator
    fallback_reason = None
    if selected is None:
        selected, fallback_reason = _pick_best_calibrator(
            records=records,
            db_path=db_path,
            engine=engine,
            asset_class=asset_class,
            style=style,
            bucket_edges=edges,
            min_bucket_samples=min_bucket_samples,
            min_total_samples=min_total_samples,
            prior_weight=prior_weight,
            default_max_score=default_max_score,
        )

    if not selected or not selected.get("available"):
        return {
            "available": False,
            "calibrated_prob": round(score_norm, 6),
            "raw_score_norm": round(score_norm, 6),
            "fallback_reason": fallback_reason or "no_calibration_records",
            "sample_count": 0,
            "bucket_samples": 0,
            "scope_used": None,
        }

    bucket = selected["buckets"][_bucket_index(score_norm, tuple(selected["bucket_edges"]))]
    overall = selected.get("overall_win_rate")
    if bucket["sample_count"] > 0:
        calibrated_prob = bucket["smoothed_prob"]
    elif overall is not None:
        calibrated_prob = overall
    else:
        calibrated_prob = score_norm

    used_smoothed = bucket["status"] != "ok"
    if bucket["sample_count"] == 0 and fallback_reason is None:
        fallback_reason = "empty_bucket_overall_fallback"

    return {
        "available": True,
        "calibrated_prob": round(float(calibrated_prob), 6),
        "raw_score_norm": round(score_norm, 6),
        "scope_used": selected.get("scope"),
        "sample_count": int(selected.get("usable_records", 0)),
        "bucket_samples": int(bucket.get("sample_count", 0)),
        "bucket": {
            "low": bucket.get("low"),
            "high": bucket.get("high"),
            "status": bucket.get("status"),
        },
        "used_smoothed_bucket": used_smoothed,
        "fallback_reason": fallback_reason,
        "warnings": list(selected.get("warnings", [])),
    }


def calibration_report(
    *,
    engine: str | None = None,
    asset_class: str | None = None,
    style: str | None = None,
    records: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
    bucket_edges: tuple[float, ...] | None = None,
    min_bucket_samples: int = _DEFAULT_MIN_BUCKET_SAMPLES,
    min_total_samples: int = _DEFAULT_MIN_TOTAL_SAMPLES,
    prior_weight: float = _DEFAULT_PRIOR_WEIGHT,
    default_max_score: float | None = None,
) -> dict[str, Any]:
    edges = tuple(bucket_edges or _DEFAULT_BUCKET_EDGES)
    calibrator, fallback_reason = _pick_best_calibrator(
        records=records,
        db_path=db_path,
        engine=engine,
        asset_class=asset_class,
        style=style,
        bucket_edges=edges,
        min_bucket_samples=min_bucket_samples,
        min_total_samples=min_total_samples,
        prior_weight=prior_weight,
        default_max_score=default_max_score,
    )
    if not calibrator:
        return {
            "available": False,
            "scope_requested": {
                "engine": _norm_text(engine),
                "asset_class": _norm_text(asset_class),
                "style": _norm_text(style),
            },
            "scope_used": None,
            "fallback_reason": fallback_reason,
            "sample_count": 0,
            "bucket_count": len(edges) - 1,
            "buckets": [],
            "warnings": ["no_calibration_records"],
        }

    return {
        "available": calibrator.get("available", False),
        "scope_requested": {
            "engine": _norm_text(engine),
            "asset_class": _norm_text(asset_class),
            "style": _norm_text(style),
        },
        "scope_used": calibrator.get("scope"),
        "fallback_reason": fallback_reason,
        "sample_count": calibrator.get("usable_records", 0),
        "bucket_count": calibrator.get("bucket_count", 0),
        "overall_win_rate": calibrator.get("overall_win_rate"),
        "min_bucket_samples": calibrator.get("min_bucket_samples"),
        "min_total_samples": calibrator.get("min_total_samples"),
        "buckets": calibrator.get("buckets", []),
        "warnings": calibrator.get("warnings", []),
    }


def calibration_quality_summary(prediction: dict | None) -> dict[str, Any]:
    """Reduce a prediction payload to a simple quality/readiness summary."""
    pred = prediction if isinstance(prediction, dict) else {}
    available = bool(pred.get("available"))
    sample_count = int(pred.get("sample_count", 0) or 0)
    bucket_samples = int(pred.get("bucket_samples", 0) or 0)
    fallback_reason = pred.get("fallback_reason")
    used_smoothed = bool(pred.get("used_smoothed_bucket"))
    exact_scope = available and not fallback_reason

    # Conservative auditable quality score, 0-1.
    score = 0.25
    if available:
        score = 0.45
        score += min(0.35, sample_count / 100.0 * 0.35)
        score += min(0.20, bucket_samples / 20.0 * 0.20)
        if used_smoothed:
            score -= 0.10
        if fallback_reason:
            score -= 0.10
    score = max(0.0, min(1.0, score))

    notes = []
    if not available:
        notes.append("calibration_unavailable")
    if fallback_reason:
        notes.append(f"fallback:{fallback_reason}")
    if used_smoothed:
        notes.append("smoothed_bucket")

    return {
        "available": available,
        "quality_score": round(score, 6),
        "sample_count": sample_count,
        "bucket_samples": bucket_samples,
        "exact_scope": exact_scope,
        "fallback_reason": fallback_reason,
        "used_smoothed_bucket": used_smoothed,
        "notes": notes,
    }


# ── Stage 4.3: Score-to-probability calibration monitor ──────────────────────
# Detects model decay by comparing predicted win probabilities (from score
# buckets) against actual empirical win rates.  Alerts when calibration
# diverges beyond tolerance — a leading indicator of regime shift or
# model degradation.

_CALIBRATION_DECAY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CALIBRATION_DECAY_TTL_SEC = 300.0

_DEFAULT_DECAY_BUCKET_EDGES = (0.0, 0.35, 0.50, 0.65, 0.80, 1.000001)
_DEFAULT_DECAY_MIN_BUCKET = 8
_DEFAULT_DECAY_MIN_TOTAL = 40
# Maximum allowed Brier-score-equivalent miscalibration per bucket.
# 0.15 means a bucket predicted 70% but realized 55% (or 85%) triggers alert.
_DEFAULT_DECAY_TOLERANCE = 0.15


def _brier_miscalibration(predicted_prob: float, empirical_rate: float) -> float:
    """Return absolute miscalibration magnitude (Brier component without square)."""
    return abs(predicted_prob - empirical_rate)


def calibration_decay_report(
    *,
    engine: str | None = None,
    asset_class: str | None = None,
    style: str | None = None,
    records: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
    bucket_edges: tuple[float, ...] | None = None,
    min_bucket_samples: int = _DEFAULT_DECAY_MIN_BUCKET,
    min_total_samples: int = _DEFAULT_DECAY_MIN_TOTAL,
    decay_tolerance: float = _DEFAULT_DECAY_TOLERANCE,
    prior_weight: float = _DEFAULT_PRIOR_WEIGHT,
    default_max_score: float | None = None,
) -> dict[str, Any]:
    """Compare smoothed predicted probabilities against empirical win rates.

    Returns a decay report with per-bucket miscalibration and an overall
    health flag.  If any bucket diverges by > decay_tolerance, the model
    is flagged as potentially decayed.
    """
    edges = tuple(bucket_edges or _DEFAULT_DECAY_BUCKET_EDGES)
    calibrator = fit_calibrator(
        records or _load_records_from_audit_db(db_path),
        engine=engine,
        asset_class=asset_class,
        style=style,
        bucket_edges=edges,
        min_bucket_samples=min_bucket_samples,
        min_total_samples=min_total_samples,
        prior_weight=prior_weight,
        default_max_score=default_max_score,
    )

    if not calibrator.get("available"):
        return {
            "available": False,
            "healthy": None,
            "scope": calibrator.get("scope"),
            "usable_records": calibrator.get("usable_records", 0),
            "reason": "insufficient_data",
            "buckets": [],
            "max_miscalibration": None,
            "decayed_buckets": 0,
            "recommendation": "collect_more_trades",
        }

    buckets = calibrator.get("buckets", [])
    decayed = []
    max_misc = 0.0
    for b in buckets:
        n = int(b.get("sample_count", 0))
        if n < min_bucket_samples:
            continue
        predicted = float(b.get("smoothed_prob", 0.0))
        empirical = float(b.get("empirical_prob", 0.0)) if b.get("empirical_prob") is not None else predicted
        misc = _brier_miscalibration(predicted, empirical)
        max_misc = max(max_misc, misc)
        if misc > decay_tolerance:
            decayed.append(
                {
                    "bucket_index": b["index"],
                    "range": [b["low"], b["high"]],
                    "predicted": round(predicted, 4),
                    "empirical": round(empirical, 4),
                    "miscalibration": round(misc, 4),
                    "samples": n,
                }
            )

    healthy = len(decayed) == 0 and calibrator.get("usable_records", 0) >= min_total_samples
    recommendation = "ok"
    if not healthy:
        if len(decayed) >= 2:
            recommendation = "recalibrate_model"
        elif calibrator.get("usable_records", 0) < min_total_samples:
            recommendation = "collect_more_trades"
        else:
            recommendation = "review_recent_regime"

    return {
        "available": True,
        "healthy": healthy,
        "scope": calibrator.get("scope"),
        "usable_records": calibrator.get("usable_records", 0),
        "overall_win_rate": calibrator.get("overall_win_rate"),
        "max_miscalibration": round(max_misc, 4) if max_misc > 0 else None,
        "decayed_buckets": len(decayed),
        "decayed_bucket_details": decayed,
        "decay_tolerance": decay_tolerance,
        "buckets": buckets,
        "recommendation": recommendation,
        "warnings": calibrator.get("warnings", []),
    }


def cached_calibration_decay_report(
    *,
    engine: str | None = None,
    asset_class: str | None = None,
    style: str | None = None,
    db_path: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """TTL-cached wrapper for calibration_decay_report."""
    path = db_path or _default_db_path()
    cache_key = f"{path}::{engine}:{asset_class}:{style}"
    now = time.time()
    cached = _CALIBRATION_DECAY_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CALIBRATION_DECAY_TTL_SEC:
        return cached[1]
    report = calibration_decay_report(
        engine=engine,
        asset_class=asset_class,
        style=style,
        db_path=path,
        **kwargs,
    )
    _CALIBRATION_DECAY_CACHE[cache_key] = (now, report)
    return report


def global_calibration_health(db_path: str | None = None) -> dict[str, Any]:
    """Run decay reports across all engine/asset_class combinations.

    Returns a summary with any decayed scopes so operators can spot
    model degradation at a glance.
    """
    engines = ("engine_a", "engine_b", "engine_c", "scalp")
    asset_classes = ("crypto", "forex", "commodity", "stock", "index")
    decayed_scopes = []
    healthy_count = 0
    unhealthy_count = 0
    insufficient_count = 0

    for engine in engines:
        for asset in asset_classes:
            report = cached_calibration_decay_report(
                engine=engine, asset_class=asset, db_path=db_path
            )
            if not report.get("available"):
                insufficient_count += 1
                continue
            if report.get("healthy"):
                healthy_count += 1
            else:
                unhealthy_count += 1
                decayed_scopes.append(
                    {
                        "engine": engine,
                        "asset_class": asset,
                        "recommendation": report.get("recommendation"),
                        "max_miscalibration": report.get("max_miscalibration"),
                        "decayed_buckets": report.get("decayed_buckets"),
                    }
                )

    return {
        "checked": len(engines) * len(asset_classes),
        "healthy": healthy_count,
        "unhealthy": unhealthy_count,
        "insufficient_data": insufficient_count,
        "decayed_scopes": decayed_scopes,
        "global_healthy": unhealthy_count == 0,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
