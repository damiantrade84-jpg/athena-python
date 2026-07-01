"""ai_drift_detector.py — weekly AI output distribution drift detector.

Phase 3 of the Athena AI Edge Program. Compares week-over-week distributions
of AI output fields (e.g. `entryAllowedNow` rates, `decision` mix, average
`confidence`) and flags drift above configurable thresholds. Config-gated by
`AI_DRIFT_DETECTION_ENABLED` (default False).

Safety:
- Advisory-only. Reports drift findings; never auto-applies fixes.
- Never raises; on any error returns an empty findings list.
- Pure functions over in-memory sample lists; no DB writes, no network.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any

from config import CONFIG

log = logging.getLogger("athena.ai_drift_detector")


def is_drift_detection_enabled() -> bool:
    try:
        return bool(CONFIG.get("AI_DRIFT_DETECTION_ENABLED", False))
    except Exception:
        return False


def _get_float(key: str, default: float) -> float:
    try:
        return float(CONFIG.get(key, default))
    except Exception:
        return default


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _extract_decision(sample: dict[str, Any]) -> str | None:
    for key in ("decision", "ai_decision", "final_action"):
        v = sample.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def _extract_confidence(sample: dict[str, Any]) -> float | None:
    for key in ("confidence", "ai_confidence", " conviction"):
        v = sample.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return None


def _extract_entry_allowed(sample: dict[str, Any]) -> bool | None:
    for key in ("entryAllowedNow", "entry_allowed_now", "allowEntry"):
        v = sample.get(key)
        if isinstance(v, bool):
            return v
    return None


def _decision_distribution(samples: list[dict[str, Any]]) -> dict[str, float]:
    decisions = [_extract_decision(s) for s in samples]
    decisions = [d for d in decisions if d is not None]
    if not decisions:
        return {}
    counts = Counter(decisions)
    total = len(decisions)
    return {k: round(v / total, 4) for k, v in counts.items()}


def _entry_allowed_rate(samples: list[dict[str, Any]]) -> float | None:
    vals = [_extract_entry_allowed(s) for s in samples]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(sum(1 for v in vals if v) / len(vals), 4)


def _mean_confidence(samples: list[dict[str, Any]]) -> float | None:
    vals = [_extract_confidence(s) for s in samples]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _js_divergence(p: dict[str, float], q: dict[str, float]) -> float:
    """Symmetric Jensen-Shannon-style divergence over a shared key set (0..1)."""
    keys = set(p.keys()) | set(q.keys())
    if not keys:
        return 0.0
    p_total = sum(p.values()) or 1.0
    q_total = sum(q.values()) or 1.0
    m: dict[str, float] = {}
    for k in keys:
        pk = p.get(k, 0.0) / p_total
        qk = q.get(k, 0.0) / q_total
        m[k] = 0.5 * (pk + qk)
    score = 0.0
    for k in keys:
        pk = p.get(k, 0.0) / p_total
        qk = q.get(k, 0.0) / q_total
        if pk > 0:
            score += 0.5 * pk * math.log2((pk + 1e-12) / (m[k] + 1e-12))
        if qk > 0:
            score += 0.5 * qk * math.log2((qk + 1e-12) / (m[k] + 1e-12))
    return round(max(0.0, min(1.0, score)), 4)


def detect_drift(
    this_week: list[dict[str, Any]],
    last_week: list[dict[str, Any]],
    *,
    surface: str = "default",
) -> dict[str, Any]:
    """Compare two weekly sample batches and flag drift.

    Thresholds (configurable):
      AI_DRIFT_DECISION_JS_THRESHOLD  (default 0.20)
      AI_DRIFT_ENTRY_RATE_DELTA        (default 0.15)
      AI_DRIFT_CONFIDENCE_DELTA        (default 0.10)
      AI_DRIFT_MIN_SAMPLES             (default 10)

    Returns:
      {schema_version, surface, this_week_n, last_week_n, findings: [...],
       do_not_auto_apply: true}
    """
    findings: list[dict[str, Any]] = []
    tw = _as_list(this_week)
    lw = _as_list(last_week)
    min_samples = int(_get_float("AI_DRIFT_MIN_SAMPLES", 10.0))
    if len(tw) < min_samples or len(lw) < min_samples:
        return {
            "schema_version": "drift_report.v1",
            "surface": surface,
            "this_week_n": len(tw),
            "last_week_n": len(lw),
            "findings": findings,
            "skipped": "insufficient_samples",
            "do_not_auto_apply": True,
        }

    # Decision distribution drift
    dec_js = _js_divergence(_decision_distribution(tw), _decision_distribution(lw))
    if dec_js > _get_float("AI_DRIFT_DECISION_JS_THRESHOLD", 0.20):
        findings.append(
            {
                "metric": "decision_distribution",
                "this_week": _decision_distribution(tw),
                "last_week": _decision_distribution(lw),
                "score": dec_js,
                "threshold": _get_float("AI_DRIFT_DECISION_JS_THRESHOLD", 0.20),
                "direction": "drift_up",
            }
        )

    # Entry-allowed rate drift
    tw_er = _entry_allowed_rate(tw)
    lw_er = _entry_allowed_rate(lw)
    if tw_er is not None and lw_er is not None:
        delta = abs(tw_er - lw_er)
        if delta > _get_float("AI_DRIFT_ENTRY_RATE_DELTA", 0.15):
            findings.append(
                {
                    "metric": "entry_allowed_rate",
                    "this_week": tw_er,
                    "last_week": lw_er,
                    "delta": round(delta, 4),
                    "threshold": _get_float("AI_DRIFT_ENTRY_RATE_DELTA", 0.15),
                    "direction": "up" if tw_er > lw_er else "down",
                }
            )

    # Mean confidence drift
    tw_c = _mean_confidence(tw)
    lw_c = _mean_confidence(lw)
    if tw_c is not None and lw_c is not None:
        delta = abs(tw_c - lw_c)
        if delta > _get_float("AI_DRIFT_CONFIDENCE_DELTA", 0.10):
            findings.append(
                {
                    "metric": "mean_confidence",
                    "this_week": tw_c,
                    "last_week": lw_c,
                    "delta": round(delta, 4),
                    "threshold": _get_float("AI_DRIFT_CONFIDENCE_DELTA", 0.10),
                    "direction": "up" if tw_c > lw_c else "down",
                }
            )

    return {
        "schema_version": "drift_report.v1",
        "surface": surface,
        "this_week_n": len(tw),
        "last_week_n": len(lw),
        "findings": findings,
        "do_not_auto_apply": True,
    }


def detect_drift_if_enabled(
    this_week: list[dict[str, Any]],
    last_week: list[dict[str, Any]],
    *,
    surface: str = "default",
) -> dict[str, Any] | None:
    """Config-gated wrapper. Returns None when disabled or on error."""
    if not is_drift_detection_enabled():
        return None
    try:
        return detect_drift(this_week, last_week, surface=surface)
    except Exception as exc:
        log.warning("[AI_DRIFT] detection failed: %s", exc)
        return None
