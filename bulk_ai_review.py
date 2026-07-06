"""Bulk scan + Marcus Reid AI text review composer.

Composes the existing cascade scan shortlist with the Marcus Reid text
review (`run_ai`) so one request produces AI-graded candidates. Advisory
only: this module never executes trades, never touches gates, and does not
modify cascade_scan or its contract. Chart review remains a manual,
client-driven follow-up for A/B survivors.
"""

from __future__ import annotations

import time
from typing import Any, Callable
from uuid import uuid4

DEFAULT_TOP_N = 8
MAX_TOP_N = 15
DEFAULT_CACHE_TTL_S = 3600.0
DEFAULT_BUDGET_S = 120.0

PASS_GRADES = {"A+", "A", "B"}

# The bulk review defers to Engine A/B's own emission thresholds: any signal
# the engines produced is eligible for AI review. Cascade's extra
# min_confluence/min_rr shortlist rules are zeroed here (freshness and
# provenance hard blockers still apply inside cascade). Callers can still
# tighten via the `rules` payload.
DEFAULT_RULES: dict = {"min_confluence": 0.0, "min_rr": 0.0}

# In-process TTL cache: key -> (monotonic_ts, review dict). Safe-defaults are
# never cached so a transient provider failure does not stick for an hour.
_REVIEW_CACHE: dict[tuple, tuple[float, dict]] = {}


def run_bulk_ai_review(
    asset_class: str = "forex",
    style: str = "auto",
    top_n: int | None = None,
    rules: dict | None = None,
    *,
    run_cascade_scan_fn: Callable[..., dict],
    run_ai_fn: Callable[..., dict],
    persist_fn: Callable[[dict], Any] | None = None,
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
    budget_s: float = DEFAULT_BUDGET_S,
    now_fn: Callable[[], float] = time.monotonic,
) -> dict:
    """Scan, shortlist, review each candidate with Marcus, return A/B rows."""
    limit = _clamp_top_n(top_n)
    started = now_fn()

    merged_rules = dict(DEFAULT_RULES)
    if isinstance(rules, dict):
        merged_rules.update(rules)

    cascade = run_cascade_scan_fn(
        asset_class=asset_class,
        style=style,
        enable_triage=False,
        top_n=limit,
        rules=merged_rules,
    )
    if not isinstance(cascade, dict) or cascade.get("success") is not True:
        return cascade if isinstance(cascade, dict) else {
            "success": False,
            "error": "cascade_result_unavailable",
        }

    scan_run_id = str(cascade.get("scanRunId") or uuid4().hex)
    candidates = cascade.get("candidates")
    if not isinstance(candidates, list):
        candidates = []

    results: list[dict] = []
    filtered_out: list[dict] = []
    skipped: list[dict] = []
    reviewed = cache_hits = safe_defaults = a_grades = b_grades = 0

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        elapsed = now_fn() - started
        if elapsed >= budget_s:
            skipped.append(
                {"symbol": _symbol(candidate), "reason": "skipped_budget"}
            )
            continue

        cache_key = _cache_key(candidate)
        cached = _cache_get(cache_key, now_fn(), cache_ttl_s)
        if cached is not None:
            review = cached
            cache_hit = True
            cache_hits += 1
        else:
            review = run_ai_fn(_signal_from_candidate(candidate, asset_class))
            cache_hit = False
            if not isinstance(review, dict):
                review = {"error": "invalid_ai_result"}

        is_safe_default = bool(review.get("error"))
        grade = str(review.get("grade") or "").strip().upper()
        reviewed += 1
        if is_safe_default:
            safe_defaults += 1
        else:
            _cache_put(cache_key, now_fn(), review)
            if grade in ("A+", "A"):
                a_grades += 1
            elif grade == "B":
                b_grades += 1

        row = _result_row(
            candidate,
            review,
            grade=grade,
            scan_run_id=scan_run_id,
            asset_class=asset_class,
            style=style,
            is_safe_default=is_safe_default,
            cache_hit=cache_hit,
        )
        if persist_fn is not None:
            try:
                persist_fn(_persist_row(row, review))
            except Exception:
                # Persistence must never break the review response.
                pass

        if not is_safe_default and grade in PASS_GRADES:
            results.append(row)
        else:
            filtered_out.append(
                {
                    "symbol": row["symbol"],
                    "direction": row["direction"],
                    "grade": grade or None,
                    "isSafeDefault": is_safe_default,
                }
            )

    return {
        "success": True,
        "scanRunId": scan_run_id,
        "assetClass": asset_class,
        "style": style,
        "universeCount": cascade.get("universeCount"),
        "shortlistCount": len(candidates),
        "hardBlockedCount": cascade.get("hardBlockedCount"),
        "hardBlockedSummary": cascade.get("hardBlockedSummary"),
        "results": results,
        "filteredOut": filtered_out,
        "skipped": skipped,
        "summary": {
            "reviewed": reviewed,
            "cacheHits": cache_hits,
            "safeDefaults": safe_defaults,
            "aGrades": a_grades,
            "bGrades": b_grades,
            "skippedBudget": len(skipped),
            "elapsedMs": int((now_fn() - started) * 1000),
        },
        "nextStep": "manual chart review recommended for A/B survivors",
    }


def _clamp_top_n(top_n: Any) -> int:
    try:
        value = int(top_n)
    except (TypeError, ValueError):
        return DEFAULT_TOP_N
    return max(1, min(value, MAX_TOP_N))


def _symbol(candidate: dict) -> str:
    return str(candidate.get("symbol") or candidate.get("pair") or "").strip()


def _signal_from_candidate(candidate: dict, asset_class: str) -> dict:
    """Build the Marcus signal payload from a cascade shortlist row."""
    symbol = _symbol(candidate)
    return {
        "pair": symbol,
        "symbol": symbol,
        "type": asset_class,
        "direction": candidate.get("direction"),
        "confluenceScore": candidate.get("confluenceScore"),
        "rr": candidate.get("rr"),
        "regime": candidate.get("regime"),
        "session": candidate.get("session"),
        "dataFreshness": candidate.get("freshnessDiagnostics"),
        "engineAReasons": candidate.get("engineAReasons"),
        "warnings": candidate.get("warnings"),
        "style": candidate.get("reviewTimeframe"),
    }


def _cache_key(candidate: dict) -> tuple:
    try:
        confluence = round(float(candidate.get("confluenceScore") or 0.0), 1)
    except (TypeError, ValueError):
        confluence = 0.0
    return (
        _symbol(candidate).upper(),
        str(candidate.get("direction") or "").upper(),
        confluence,
    )


def _cache_get(key: tuple, now: float, ttl_s: float) -> dict | None:
    entry = _REVIEW_CACHE.get(key)
    if entry is None:
        return None
    ts, review = entry
    if now - ts >= ttl_s:
        _REVIEW_CACHE.pop(key, None)
        return None
    return review


def _cache_put(key: tuple, now: float, review: dict) -> None:
    _REVIEW_CACHE[key] = (now, review)


def _result_row(
    candidate: dict,
    review: dict,
    *,
    grade: str,
    scan_run_id: str,
    asset_class: str,
    style: str,
    is_safe_default: bool,
    cache_hit: bool,
) -> dict:
    return {
        "scanRunId": scan_run_id,
        "symbol": _symbol(candidate),
        "pair": _symbol(candidate),
        "direction": candidate.get("direction"),
        "assetClass": asset_class,
        "style": style,
        "confluenceScore": candidate.get("confluenceScore"),
        "rr": candidate.get("rr"),
        "shortlistRank": candidate.get("shortlistRank"),
        "engineBConviction": _nested_conviction(candidate, "engine_b"),
        "engineCConviction": _nested_conviction(candidate, "engine_c"),
        "grade": grade or None,
        "edgeProbability": review.get("edgeProbability"),
        "riskLevel": review.get("riskLevel"),
        "verdict": review.get("verdict"),
        "provider": review.get("provider"),
        "model": review.get("model"),
        "isSafeDefault": is_safe_default,
        "cacheHit": cache_hit,
        "review": review,
    }


def _nested_conviction(candidate: dict, key: str) -> Any:
    nested = candidate.get(key)
    if isinstance(nested, dict):
        return nested.get("conviction")
    return candidate.get(f"{key}_conviction")


def _persist_row(row: dict, review: dict) -> dict:
    return {
        "scan_run_id": row["scanRunId"],
        "symbol": row["symbol"],
        "pair": row["pair"],
        "direction": row["direction"],
        "asset_class": row["assetClass"],
        "style": row["style"],
        "confluence_score": row["confluenceScore"],
        "rr": row["rr"],
        "engine_b_conviction": row["engineBConviction"],
        "engine_c_conviction": row["engineCConviction"],
        "grade": row["grade"],
        "edge_probability": row["edgeProbability"],
        "risk_level": row["riskLevel"],
        "verdict": row["verdict"],
        "review": review,
        "is_safe_default": row["isSafeDefault"],
        "cache_hit": row["cacheHit"],
        "provider": row["provider"],
        "model": row["model"],
        "latency_ms": review.get("latency_ms"),
    }
