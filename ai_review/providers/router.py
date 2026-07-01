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
        return _run_dual_provider(payload, cfg)
    raise ValueError(f"Unknown provider: {provider!r}")


def _run_dual_provider(payload: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    """Run primary + secondary providers in parallel and reconcile.

    Primary/secondary resolved from config; defaults to (openai, claude).
    Tiebreaker is `AI_DUAL_PROVIDER_TIEBREAKER` (primary | secondary |
    fail_closed); default `primary`.
    """
    from ai_review.dual_provider import run_dual_provider

    primary = str(cfg.get("AI_DUAL_PROVIDER_PRIMARY") or "openai").strip().lower()
    secondary = str(cfg.get("AI_DUAL_PROVIDER_SECONDARY") or "claude").strip().lower()
    tiebreaker = str(cfg.get("AI_DUAL_PROVIDER_TIEBREAKER") or "primary").strip().lower()
    if primary == secondary:
        raise ValueError("dual provider requires two distinct providers")
    if primary not in ("openai", "claude", "grok") or secondary not in ("openai", "claude", "grok"):
        raise ValueError("dual provider supports only: openai, claude, grok")
    timeout = float(cfg.get("AI_DUAL_PROVIDER_TIMEOUT_SEC") or 60.0)

    def _call(name: str):
        if name in ("claude", "anthropic"):
            out = call_anthropic_chart_review(payload)
            out["provider"] = "claude"
            return out
        if name in ("grok", "xai"):
            out = call_xai_chart_review(payload)
            out["provider"] = "grok"
            return out
        if name == "openai":
            enabled_cfg = dict(cfg)
            enabled_cfg.setdefault("OPENAI_REVIEW_ENABLED", CONFIG.get("OPENAI_REVIEW_ENABLED", True))
            if not openai_review_enabled(enabled_cfg):
                raise PermissionError("OpenAI provider disabled")
            return call_openai_chart_review(payload)
        raise ValueError(f"unknown provider in dual call: {name!r}")

    out = run_dual_provider(
        primary_fn=lambda: _call(primary),
        secondary_fn=lambda: _call(secondary),
        primary_name=primary,
        secondary_name=secondary,
        tiebreaker=tiebreaker,
        timeout_sec=timeout,
    )
    out["provider"] = "dual"
    out["dualProviderConfigured"] = True
    return out


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
