"""Provider router for AI chart review."""

from __future__ import annotations

from typing import Any

from config import CONFIG

from ai_review.providers.anthropic_provider import call_anthropic_chart_review
from ai_review.providers.openai_provider import call_openai_chart_review
from ai_review.providers.xai_provider import call_xai_chart_review


def run_chart_review(provider: str | None, payload: Any) -> dict[str, Any]:
    cfg = CONFIG["AI_CHART_REVIEW"]
    resolved = str(provider or "default").strip().lower()
    if resolved in ("", "default", "none"):
        resolved = str(cfg.get("DEFAULT_PROVIDER") or "anthropic").lower()

    if resolved == "anthropic":
        out = call_anthropic_chart_review(payload)
        out.setdefault("provider", "anthropic")
        return out
    if resolved in ("xai", "grok"):
        out = call_xai_chart_review(payload)
        out.setdefault("provider", "xai")
        return out
    if resolved == "openai":
        if not cfg.get("ALLOW_OPENAI_PROVIDER"):
            raise PermissionError("OpenAI provider disabled")
        return call_openai_chart_review(payload)
    if resolved == "dual":
        if not cfg.get("ALLOW_DUAL_PROVIDER"):
            raise PermissionError("Dual provider disabled")
        raise NotImplementedError("Dual provider not implemented for v1")
    raise ValueError(f"Unknown provider: {provider!r}")
