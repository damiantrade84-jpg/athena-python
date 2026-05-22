"""Context completeness diagnostics for scalp chart review."""

from __future__ import annotations

from typing import Any


def build_scalp_context_diagnostics(
    engine_d_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> dict[str, Any]:
    source = engine_d_ctx.get("sourceContract") or {}
    location = engine_d_ctx.get("marketLocation") or {}
    setup = engine_d_ctx.get("scalpSetup") or {}

    missing_required: list[str] = []
    missing_optional: list[str] = []

    if setup.get("entry") is None:
        missing_required.append("setup entry")
    if location.get("poc") is None:
        missing_optional.append("POC level")
    if not location.get("lvnLevels"):
        missing_optional.append("LVN levels")
    unavailable = source.get("unavailableReasons") or []
    if isinstance(unavailable, list):
        missing_optional.extend(str(u) for u in unavailable[:5])

    ai_missing = ai_review.get("missing_context") or []
    if isinstance(ai_missing, list):
        for item in ai_missing:
            text = str(item)
            if text not in missing_required and text not in missing_optional:
                missing_optional.append(text)

    score = 100
    score -= len(missing_required) * 25
    score -= min(40, len(missing_optional) * 8)
    score = max(0, min(100, score))
    if score >= 85:
        status = "complete"
    elif score >= 55:
        status = "partial"
    else:
        status = "insufficient"

    metadata = {
        "chartCapturedAt": engine_d_ctx.get("chart_captured_at"),
        "latestCandleTimestamp": engine_d_ctx.get("latest_candle_ts"),
        "candleSource": source.get("candleSource"),
        "orderflowSource": source.get("orderflowSource"),
        "vpSource": source.get("vpSource"),
    }

    completeness = {
        "score": score,
        "status": status,
        "missingRequired": missing_required,
        "missingOptional": missing_optional,
        "notApplicable": [],
        "metadata": metadata,
    }

    return {
        "contextCompleteness": completeness,
        "context_completeness": completeness,
    }
