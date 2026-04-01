"""Unified forensic observability summary built from existing monitors.

This module reuses:
- guardian boot checks (code invariants)
- divergence_monitor divergence feed (signal truth)
- stability_monitor SSI snapshots (drift/degradation + execution drift)
- audit_log error_tag rows (execution hygiene)

It intentionally does not create a parallel monitoring pipeline.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from stability_monitor import get_engine_stability_snapshot, get_signal_stability_index


_ENGINES = ("engine_a", "engine_b", "engine_c", "scalp")


def _default_db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")


def _severity_rank(status: str) -> int:
    s = str(status or "").strip().lower()
    if s == "critical":
        return 3
    if s == "warning":
        return 2
    if s == "watch":
        return 1
    return 0


def _status_from_score(score: float) -> str:
    if score < 40:
        return "critical"
    if score < 65:
        return "warning"
    if score < 80:
        return "watch"
    return "healthy"


def _build_signal_truth_view(guardian: dict, divergence: dict) -> dict:
    critical = int(divergence.get("critical_count") or 0)
    warning = int(divergence.get("warning_count") or 0)
    checks = int(divergence.get("total_checks") or 0)
    pairs_affected = divergence.get("pairs_affected") or []
    guardian_failures = guardian.get("failures") or []

    score = 100.0
    score -= min(60.0, critical * 20.0)
    score -= min(25.0, warning * 5.0)
    if checks < 5:
        score -= 10.0
    if guardian.get("passed") is False:
        score -= min(35.0, len(guardian_failures) * 7.0)
    score = max(0.0, round(score, 2))

    issues = []
    if critical > 0:
        issues.append(f"{critical} critical live-vs-backtest divergence event(s)")
    if warning > 0:
        issues.append(f"{warning} warning divergence event(s)")
    if checks < 5:
        issues.append("low divergence sample count in lookback window")
    if guardian.get("passed") is False and guardian_failures:
        issues.append(f"{len(guardian_failures)} guardian invariant failure(s)")
    if not issues:
        issues.append("signal path agreement stable")

    return {
        "name": "signal_truth",
        "status": _status_from_score(score),
        "score": score,
        "issues": issues[:4],
        "metrics": {
            "divergence_total_checks": checks,
            "divergence_critical": critical,
            "divergence_warning": warning,
            "pairs_affected_count": len(pairs_affected),
            "guardian_failures": len(guardian_failures),
        },
    }


def _build_drift_view(db_path: str) -> dict:
    system_ssi = get_signal_stability_index(db_path=db_path)
    ssi = float(system_ssi.get("ssi") or 50.0)
    status = _status_from_score(ssi)
    issues = []

    engine_rows = []
    for eng in _ENGINES:
        snap = get_engine_stability_snapshot(eng, db_path=db_path)
        eng_ssi = float(snap.get("ssi") or 50.0)
        band = str(snap.get("band") or "UNKNOWN")
        engine_rows.append({"engine": eng, "ssi": eng_ssi, "band": band})
        if band in ("RED", "ORANGE"):
            issues.append(f"{eng} SSI {band.lower()} ({eng_ssi:.1f})")

    if not issues and system_ssi.get("warnings"):
        issues.extend(system_ssi.get("warnings", [])[:3])
    if not issues:
        issues.append("no material stability drift detected")

    return {
        "name": "drift_degradation",
        "status": status,
        "score": round(ssi, 2),
        "issues": issues[:4],
        "metrics": {
            "system_ssi": round(ssi, 2),
            "system_band": system_ssi.get("band"),
            "engines": engine_rows,
        },
    }


def _fetch_execution_error_tags(db_path: str, lookback_hours: int) -> tuple[int, list[dict[str, Any]]]:
    if not os.path.exists(db_path):
        return 0, []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            total = con.execute(
                """
                SELECT COUNT(1)
                FROM audit_log
                WHERE ts >= ?
                  AND error_tag IS NOT NULL
                  AND TRIM(error_tag) <> ''
                """,
                (cutoff,),
            ).fetchone()[0]
            rows = con.execute(
                """
                SELECT error_tag, COUNT(1) AS cnt
                FROM audit_log
                WHERE ts >= ?
                  AND error_tag IS NOT NULL
                  AND TRIM(error_tag) <> ''
                GROUP BY error_tag
                ORDER BY cnt DESC
                LIMIT 5
                """,
                (cutoff,),
            ).fetchall()
    except Exception:
        return 0, []

    top = [{"tag": str(r[0]), "count": int(r[1])} for r in rows if r and r[0]]
    return int(total or 0), top


def _build_execution_hygiene_view(db_path: str, lookback_hours: int, shield: dict) -> dict:
    top_errors_total, top_errors = _fetch_execution_error_tags(db_path, lookback_hours)
    shield_open = shield.get("circuit_breaker_open") is True
    shield_failures = int(shield.get("failure_count") or 0)

    exec_drift_scores: list[float] = []
    exec_issues = []
    for eng in _ENGINES:
        snap = get_engine_stability_snapshot(eng, db_path=db_path)
        comp = ((snap.get("components") or {}).get("execution_drift") or {})
        s = comp.get("score")
        if s is not None:
            try:
                s_f = float(s)
                exec_drift_scores.append(s_f)
                if s_f < 60.0:
                    exec_issues.append(f"{eng} execution_drift degraded ({s_f:.1f})")
            except (TypeError, ValueError):
                pass

    base_score = (sum(exec_drift_scores) / len(exec_drift_scores)) if exec_drift_scores else 75.0
    penalty = 0.0
    if shield_open:
        penalty += 35.0
    penalty += min(25.0, top_errors_total * 2.0)
    if shield_failures > 0:
        penalty += min(15.0, shield_failures * 1.5)
    score = max(0.0, round(base_score - penalty, 2))

    issues = []
    if shield_open:
        issues.append("circuit breaker is OPEN")
    if top_errors_total > 0:
        top = top_errors[0]
        issues.append(f"{top_errors_total} execution failure tag(s) in {lookback_hours}h")
        if top:
            issues.append(f"top failure: {top['tag']} ({top['count']})")
    issues.extend(exec_issues[:2])
    if not issues:
        issues.append("execution lifecycle hygiene stable")

    return {
        "name": "execution_hygiene",
        "status": _status_from_score(score),
        "score": score,
        "issues": issues[:4],
        "metrics": {
            "circuit_breaker_open": shield_open,
            "circuit_breaker_failures": shield_failures,
            "error_tags_lookback_hours": lookback_hours,
            "error_tags_total": top_errors_total,
            "top_error_tags": top_errors,
            "execution_drift_mean": round(base_score, 2),
        },
    }


def _compact_telegram_lines(views: dict[str, dict]) -> list[str]:
    lines = []
    order = ("signal_truth", "drift_degradation", "execution_hygiene")
    label_map = {
        "signal_truth": "Signal Truth",
        "drift_degradation": "Drift/Degradation",
        "execution_hygiene": "Execution Hygiene",
    }
    for key in order:
        v = views.get(key) or {}
        status = str(v.get("status") or "unknown").upper()
        score = v.get("score")
        emoji = "🟢" if status == "HEALTHY" else "🟡" if status in ("WATCH", "WARNING") else "🔴"
        head = f"{emoji} {label_map[key]}: {status}"
        if score is not None:
            head += f" ({float(score):.1f})"
        issues = v.get("issues") or []
        if issues:
            head += f" — {issues[0]}"
        lines.append(head)
    return lines


def build_forensic_summary(
    guardian: dict,
    shield: dict,
    divergence: dict,
    db_path: str | None = None,
    lookback_hours: int = 24,
) -> dict:
    """Compose a unified forensic observability payload."""
    path = db_path or _default_db_path()

    signal_truth = _build_signal_truth_view(guardian=guardian or {}, divergence=divergence or {})
    drift = _build_drift_view(db_path=path)
    hygiene = _build_execution_hygiene_view(
        db_path=path,
        lookback_hours=max(1, int(lookback_hours or 24)),
        shield=shield or {},
    )
    views = {
        "signal_truth": signal_truth,
        "drift_degradation": drift,
        "execution_hygiene": hygiene,
    }

    worst = max((_severity_rank(v.get("status")) for v in views.values()), default=0)
    overall = "critical" if worst >= 3 else "warning" if worst >= 2 else "watch" if worst >= 1 else "healthy"

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "views": views,
        "telegram_brief": _compact_telegram_lines(views),
    }
