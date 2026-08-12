"""xAI/Grok provider for AI chart review."""

from __future__ import annotations

import logging
import time
from typing import Any

from ai_review.payload_schema import review_image_inputs

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


def extract_xai_message_text(content: Any) -> str:
    """Extract assistant text from OpenAI-compatible chat completion message.content."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        else:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _chart_review_timeout_sec(cfg: dict[str, Any]) -> float:
    nested = cfg.get("TIMEOUT_SEC")
    if nested is not None:
        try:
            timeout = float(nested)
            if timeout > 0:
                return timeout
        except (TypeError, ValueError):
            pass
    return get_ai_timeout_sec(CONFIG, preferred_key="AI_CHART_REVIEW_TIMEOUT_SEC", fallback=120.0)


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
        fallback="grok-4.5",
        provider="grok",
    )
    max_tokens = int(cfg.get("XAI_MAX_TOKENS") or cfg.get("MAX_TOKENS", 1500))
    reasoning_effort = str(cfg.get("XAI_REASONING_EFFORT") or "low").strip().lower()
    timeout = _chart_review_timeout_sec(cfg)

    review_images = review_image_inputs(payload)
    for review_image in review_images:
        if not review_image["data_url"].startswith("data:image/png;base64,"):
            raise ValueError(f"non-PNG {review_image['role'].lower()} image reached provider")

    client = create_ai_client(
        CONFIG,
        api_key=api_key,
        max_retries=get_ai_max_retries(CONFIG),
        provider="grok",
    )
    request_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
        "timeout": timeout,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": payload.prompt},
                    {
                        "type": "text",
                        "text": f"IMAGE 1 — STRUCTURE ({review_images[0]['timeframe']})",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": review_images[0]["data_url"]},
                    },
                    {
                        "type": "text",
                        "text": f"IMAGE 2 — ENTRY/TRIGGER ({review_images[1]['timeframe']})",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": review_images[1]["data_url"]},
                    },
                ],
            }
        ],
    }
    if cfg.get("XAI_JSON_MODE"):
        request_kwargs["response_format"] = {"type": "json_object"}

    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(**request_kwargs)
    except Exception as exc:
        raise classify_xai_exception(exc) from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    choice = resp.choices[0] if getattr(resp, "choices", None) else None
    message = getattr(choice, "message", None)
    raw_text = extract_xai_message_text(getattr(message, "content", None))
    if not raw_text:
        raise ProviderChartReviewError(
            "Grok chart review returned empty content",
            provider_status="empty_response",
            provider="grok",
            http_status=503,
        )
    model_used = str(getattr(resp, "model", "") or model)
    log.info(
        "[AI_CHART_REVIEW] xai model=%s latency_ms=%s data_url_len=%s raw_len=%s",
        model_used,
        latency_ms,
        sum(len(image["data_url"]) for image in review_images),
        len(raw_text),
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
