"""Anthropic provider for scalp chart review."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from ai_review.provider_meta import (
    ProviderChartReviewError,
    build_provider_meta,
    classify_anthropic_exception,
)
from ai_review.providers.openai_provider import call_openai_chart_review
from ai_review.providers.xai_provider import classify_xai_exception
from ai_review.validation import openai_review_enabled
from config import (
    CONFIG,
    create_ai_client,
    get_ai_api_key,
    get_ai_max_retries,
    get_ai_model,
    get_ai_timeout_sec,
)

log = logging.getLogger("sentinel.ai_scalp_chart_review")


def call_anthropic_scalp_chart_review(payload: Any) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderChartReviewError(
            "ANTHROPIC_API_KEY not configured",
            provider_status="failed_auth",
            provider="claude",
            http_status=503,
        )

    cfg = CONFIG["AI_SCALP_CHART_REVIEW"]
    model = cfg["ANTHROPIC_MODEL"]
    max_tokens = int(cfg.get("MAX_TOKENS", 1500))

    data_url = payload.screenshot_base64
    if not data_url.startswith("data:image/png;base64,"):
        raise ValueError("non-PNG data URL reached provider")
    raw_b64 = data_url.split(",", 1)[1]

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
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": raw_b64,
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
        "[AI_SCALP_CHART_REVIEW] anthropic model=%s latency_ms=%s b64_len=%s",
        resp.model,
        latency_ms,
        len(raw_b64),
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


def call_xai_scalp_chart_review(payload: Any) -> dict[str, Any]:
    api_key = get_ai_api_key(CONFIG, provider="grok")
    if not api_key:
        raise ProviderChartReviewError(
            "XAI_API_KEY not configured",
            provider_status="failed_auth",
            provider="grok",
            http_status=503,
        )

    cfg = CONFIG["AI_SCALP_CHART_REVIEW"]
    model = get_ai_model(
        CONFIG,
        preferred_key="AI_SCALP_CHART_REVIEW_XAI_MODEL",
        fallback="grok-4.3",
        provider="grok",
    )
    max_tokens = int(cfg.get("MAX_TOKENS", 1500))
    timeout = get_ai_timeout_sec(CONFIG, preferred_key="AI_SCALP_CHART_REVIEW_TIMEOUT_SEC")

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
        "[AI_SCALP_CHART_REVIEW] xai model=%s latency_ms=%s data_url_len=%s",
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


def _apply_scalp_fallback_meta(out: dict[str, Any], selected: str, failure: dict[str, Any] | None) -> dict[str, Any]:
    if failure:
        out["selectedProvider"] = selected
        out["fallbackUsed"] = True
        out["fallback_used"] = True
        out["providerFailure"] = failure
        out["provider_failure"] = failure
    else:
        out.setdefault("selectedProvider", selected)
        out.setdefault("fallbackUsed", False)
    return out


def _run_scalp_provider(resolved: str, payload: Any) -> dict[str, Any]:
    cfg = CONFIG["AI_SCALP_CHART_REVIEW"]
    if resolved in ("anthropic", "claude"):
        out = call_anthropic_scalp_chart_review(payload)
        out["provider"] = "claude"
        return out
    if resolved in ("xai", "grok"):
        out = call_xai_scalp_chart_review(payload)
        out["provider"] = "grok"
        return out
    if resolved == "openai":
        enabled_cfg = dict(cfg)
        enabled_cfg.setdefault("OPENAI_REVIEW_ENABLED", CONFIG.get("OPENAI_REVIEW_ENABLED", True))
        if not openai_review_enabled(enabled_cfg):
            raise PermissionError("OpenAI provider disabled")
        return call_openai_chart_review(payload, cfg_key="AI_SCALP_CHART_REVIEW")
    raise ValueError(f"Unknown provider: {resolved!r}")


def run_scalp_chart_review(provider: str | None, payload: Any) -> dict[str, Any]:
    from config import get_ai_review_fallback_providers, get_ai_review_provider

    selected = get_ai_review_provider(CONFIG, requested=provider)
    candidates = [selected]
    for fallback in get_ai_review_fallback_providers(CONFIG):
        if fallback not in candidates:
            candidates.append(fallback)
    first_failure: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            return _apply_scalp_fallback_meta(
                _run_scalp_provider(candidate, payload),
                selected,
                first_failure,
            )
        except (ProviderChartReviewError, PermissionError, RuntimeError, NotImplementedError) as exc:
            if first_failure is None:
                first_failure = {
                    "provider": candidate,
                    "error": str(exc),
                    "providerStatus": getattr(exc, "provider_status", "unknown"),
                }
            if candidate == candidates[-1]:
                raise
    raise ValueError(f"Unknown provider: {provider!r}")
