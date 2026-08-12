"""Anthropic provider for AI chart review."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from ai_review.payload_schema import review_image_inputs

from ai_review.provider_meta import (
    ProviderChartReviewError,
    build_provider_meta,
    classify_anthropic_exception,
)
from config import CONFIG

log = logging.getLogger("sentinel.ai_chart_review")


def call_anthropic_chart_review(payload: Any) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderChartReviewError(
            "ANTHROPIC_API_KEY not configured",
            provider_status="failed_auth",
            provider="claude",
            http_status=503,
        )

    cfg = CONFIG["AI_CHART_REVIEW"]
    model = cfg["ANTHROPIC_MODEL"]
    max_tokens = int(cfg.get("MAX_TOKENS", 1500))

    review_images = review_image_inputs(payload)
    for review_image in review_images:
        if not review_image["data_url"].startswith("data:image/png;base64,"):
            raise ValueError(f"non-PNG {review_image['role'].lower()} image reached provider")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    t0 = time.monotonic()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"IMAGE 1 — STRUCTURE ({review_images[0]['timeframe']})",
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": review_images[0]["data_url"].split(",", 1)[1],
                            },
                        },
                        {
                            "type": "text",
                            "text": f"IMAGE 2 — ENTRY/TRIGGER ({review_images[1]['timeframe']})",
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": review_images[1]["data_url"].split(",", 1)[1],
                            },
                        },
                        {"type": "text", "text": payload.prompt},
                    ],
                }
            ],
        )
    except Exception as exc:
        raise classify_anthropic_exception(exc) from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    raw_text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    log.info(
        "[AI_CHART_REVIEW] anthropic model=%s latency_ms=%s b64_len=%s",
        resp.model,
        latency_ms,
        sum(len(image["data_url"].split(",", 1)[1]) for image in review_images),
    )
    meta = build_provider_meta(
        provider="claude",
        model=resp.model,
        provider_status="success",
        fallback_used=False,
        latency_ms=latency_ms,
    )
    return {
        "raw_text": raw_text,
        "model": resp.model,
        "latency_ms": latency_ms,
        **meta,
    }
