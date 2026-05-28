"""OpenAI Responses API provider for AI review."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from ai_review.provider_meta import ProviderChartReviewError, build_provider_meta
from config import (
    CONFIG,
    get_ai_base_url,
    get_ai_model,
)

log = logging.getLogger("sentinel.ai_review.openai")


def classify_openai_exception(exc: Exception) -> ProviderChartReviewError:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    code = getattr(exc, "status_code", None)
    if code in (401, 403) or "authentication" in name or "api key" in msg or "unauthorized" in msg:
        status, http_status = "failed_auth", 503
    elif code == 429 or "rate limit" in msg or "rate_limit" in msg:
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
        provider="openai",
        http_status=http_status,
    )


def extract_openai_response_text(resp: Any) -> str:
    direct = getattr(resp, "output_text", None)
    if isinstance(direct, str):
        return direct
    if isinstance(resp, dict):
        direct = resp.get("output_text")
        if isinstance(direct, str):
            return direct
        output = resp.get("output") or []
    else:
        output = getattr(resp, "output", []) or []
    parts: list[str] = []
    for item in output:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        for block in content or []:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if btype in ("output_text", "text") and isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def build_openai_responses_payload(
    payload: Any,
    *,
    cfg: dict[str, Any] | None = None,
    cfg_key: str = "AI_CHART_REVIEW",
) -> dict[str, Any]:
    cfg = CONFIG if cfg is None else cfg
    review_cfg = cfg.get(cfg_key) if isinstance(cfg.get(cfg_key), dict) else {}
    data_url = str(payload.screenshot_base64 or "")
    if not data_url.startswith("data:image/png;base64,"):
        raise ValueError("non-PNG data URL reached provider")
    model = (
        str(review_cfg.get("OPENAI_MODEL") or "").strip()
        or get_ai_model(cfg, provider="openai")
    )
    effort = str(
        os.environ.get("OPENAI_REVIEW_REASONING_EFFORT", "")
        or cfg.get("OPENAI_REVIEW_REASONING_EFFORT", "xhigh")
        or "xhigh"
    ).strip().lower()
    max_output_tokens = int(
        os.environ.get("OPENAI_REVIEW_MAX_OUTPUT_TOKENS", "")
        or cfg.get("OPENAI_REVIEW_MAX_OUTPUT_TOKENS", 12000)
        or 12000
    )
    return {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": str(payload.prompt or "")},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
    }


def call_openai_chart_review(
    payload: Any,
    *,
    cfg_key: str = "AI_CHART_REVIEW",
) -> dict[str, Any]:
    if not bool(CONFIG.get("OPENAI_REVIEW_ENABLED", True)):
        raise ProviderChartReviewError(
            "OPENAI_REVIEW_ENABLED is false",
            provider_status="failed_auth",
            provider="openai",
            http_status=503,
        )
    api_key = os.environ.get("OPENAI_API_KEY") or str(CONFIG.get("OPENAI_API_KEY") or "")
    if not api_key:
        raise ProviderChartReviewError(
            "OPENAI_API_KEY not configured",
            provider_status="failed_auth",
            provider="openai",
            http_status=503,
        )

    import openai

    timeout = float(
        os.environ.get("OPENAI_REVIEW_TIMEOUT_SECONDS", "")
        or CONFIG.get("OPENAI_REVIEW_TIMEOUT_SECONDS", 120)
        or 120
    )
    request_payload = build_openai_responses_payload(payload, cfg=CONFIG, cfg_key=cfg_key)
    client = openai.OpenAI(
        api_key=api_key,
        base_url=get_ai_base_url(CONFIG, provider="openai"),
    )
    t0 = time.monotonic()
    try:
        resp = client.responses.create(**request_payload, timeout=timeout)
    except Exception as exc:
        raise classify_openai_exception(exc) from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    raw_text = extract_openai_response_text(resp)
    model_used = str(getattr(resp, "model", "") or request_payload["model"])
    log.info(
        "[AI_REVIEW] openai model=%s latency_ms=%s data_url_len=%s",
        model_used,
        latency_ms,
        len(str(payload.screenshot_base64 or "")),
    )
    meta = build_provider_meta(
        provider="openai",
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
