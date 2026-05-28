"""xAI/Grok provider for AI chart review."""

from __future__ import annotations

import logging
import time
from typing import Any

from ai_review.provider_meta import ProviderChartReviewError, build_provider_meta
from config import (
    CONFIG,
    create_ai_client,
    get_ai_api_key,
    get_ai_max_retries,
    get_ai_model,
    get_ai_timeout_sec,
)

log = logging.getLogger("sentinel.ai_chart_review")


def classify_xai_exception(exc: Exception) -> ProviderChartReviewError:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "authentication" in name or "auth" in msg or "api key" in msg or "unauthorized" in msg:
        status, http_status = "failed_auth", 503
    elif "permission" in name or "forbidden" in msg:
        status, http_status = "failed_auth", 403
    elif "ratelimit" in name or "rate limit" in msg or "rate_limit" in msg:
        status, http_status = "rate_limited", 503
    elif "credit" in msg or "billing" in msg or "balance" in msg or "insufficient" in msg:
        status, http_status = "insufficient_credit", 503
    elif "timeout" in name or "timed out" in msg:
        status, http_status = "timeout", 503
    else:
        status, http_status = "unknown", 503
    return ProviderChartReviewError(
        str(exc),
        provider_status=status,
        provider="grok",
        http_status=http_status,
    )


def call_xai_chart_review(payload: Any) -> dict[str, Any]:
    api_key = get_ai_api_key(CONFIG, provider="grok")
    if not api_key:
        raise ProviderChartReviewError(
            "XAI_API_KEY not configured",
            provider_status="failed_auth",
            provider="grok",
            http_status=503,
        )

    cfg = CONFIG["AI_CHART_REVIEW"]
    model = get_ai_model(
        CONFIG,
        preferred_key="AI_CHART_REVIEW_XAI_MODEL",
        fallback="grok-4.3",
        provider="grok",
    )
    max_tokens = int(cfg.get("MAX_TOKENS", 1500))
    timeout = get_ai_timeout_sec(CONFIG, preferred_key="AI_CHART_REVIEW_TIMEOUT_SEC")

    data_url = payload.screenshot_base64
    if not data_url.startswith("data:image/png;base64,"):
        raise ValueError("non-PNG data URL reached provider")

    client = create_ai_client(
        CONFIG,
        api_key=api_key,
        max_retries=get_ai_max_retries(CONFIG),
        provider="grok",
    )
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": payload.prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
    except Exception as exc:
        raise classify_xai_exception(exc) from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    choice = resp.choices[0] if getattr(resp, "choices", None) else None
    message = getattr(choice, "message", None)
    raw_text = str(getattr(message, "content", "") or "")
    model_used = str(getattr(resp, "model", "") or model)
    log.info(
        "[AI_CHART_REVIEW] xai model=%s latency_ms=%s data_url_len=%s",
        model_used,
        latency_ms,
        len(data_url),
    )
    meta = build_provider_meta(
        provider="grok",
        model=model_used,
        provider_status="success",
        fallback_used=False,
        latency_ms=latency_ms,
    )
    return {
        "raw_text": raw_text,
        "model": model_used,
        "latency_ms": latency_ms,
        **meta,
    }
