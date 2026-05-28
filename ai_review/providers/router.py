"""Provider router for AI chart review."""

from __future__ import annotations

from typing import Any

from config import CONFIG
from config import get_ai_review_fallback_providers, get_ai_review_provider

from ai_review.providers.anthropic_provider import call_anthropic_chart_review
from ai_review.providers.openai_provider import call_openai_chart_review
from ai_review.providers.xai_provider import call_xai_chart_review
from ai_review.provider_meta import ProviderChartReviewError
from ai_review.validation import openai_review_enabled


def _apply_fallback_meta(
    out: dict[str, Any],
    *,
    selected: str,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
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


def _run_provider(provider: str, payload: Any) -> dict[str, Any]:
    cfg = CONFIG["AI_CHART_REVIEW"]
    resolved = str(provider or "").strip().lower()
    if resolved in ("anthropic", "claude"):
        out = call_anthropic_chart_review(payload)
        out["provider"] = "claude"
        return out
    if resolved in ("xai", "grok"):
        out = call_xai_chart_review(payload)
        out["provider"] = "grok"
        return out
    if resolved == "openai":
        enabled_cfg = dict(cfg)
        enabled_cfg.setdefault("OPENAI_REVIEW_ENABLED", CONFIG.get("OPENAI_REVIEW_ENABLED", True))
        if not openai_review_enabled(enabled_cfg):
            raise PermissionError("OpenAI provider disabled")
        return call_openai_chart_review(payload)
    if resolved == "dual":
        if not cfg.get("ALLOW_DUAL_PROVIDER"):
            raise PermissionError("Dual provider disabled")
        raise NotImplementedError("Dual provider not implemented for v1")
    raise ValueError(f"Unknown provider: {provider!r}")


def run_chart_review(provider: str | None, payload: Any) -> dict[str, Any]:
    selected = get_ai_review_provider(CONFIG, requested=provider)
    candidates = [selected]
    for fallback in get_ai_review_fallback_providers(CONFIG):
        if fallback not in candidates:
            candidates.append(fallback)
    first_failure: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            return _apply_fallback_meta(
                _run_provider(candidate, payload),
                selected=selected,
                failure=first_failure,
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
