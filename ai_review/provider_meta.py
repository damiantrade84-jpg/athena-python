"""Provider outcome metadata for AI chart review."""

from __future__ import annotations

from typing import Any

VALID_PROVIDER_STATUSES = frozenset(
    {
        "success",
        "failed_auth",
        "insufficient_credit",
        "timeout",
        "fallback_used",
        "unknown",
    }
)

DETERMINISTIC_NORMALIZER_MODEL = "deterministic_normalizer"


class ProviderChartReviewError(Exception):
    """Provider failure with a stable provider_status for API error responses."""

    def __init__(
        self,
        message: str,
        *,
        provider_status: str = "unknown",
        provider: str = "anthropic",
        http_status: int = 503,
    ) -> None:
        super().__init__(message)
        self.provider_status = provider_status
        self.provider = provider
        self.http_status = http_status


def _classify_anthropic_exception(exc: Exception) -> tuple[str, int]:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    if "authentication" in name or "auth" in msg or "api key" in msg or "unauthorized" in msg:
        return "failed_auth", 503
    if "permission" in name or "forbidden" in msg:
        return "failed_auth", 403
    if "ratelimit" in name or "rate limit" in msg or "rate_limit" in msg:
        return "insufficient_credit", 503
    if "credit" in msg or "billing" in msg or "balance" in msg or "insufficient" in msg:
        return "insufficient_credit", 503
    if "timeout" in name or "timed out" in msg:
        return "timeout", 503
    return "unknown", 503


def classify_anthropic_exception(exc: Exception) -> ProviderChartReviewError:
    status, http_status = _classify_anthropic_exception(exc)
    return ProviderChartReviewError(
        str(exc),
        provider_status=status,
        provider="anthropic",
        http_status=http_status,
    )


def provider_error_response(exc: Exception, *, provider: str = "anthropic") -> tuple[dict[str, Any], int]:
    if isinstance(exc, ProviderChartReviewError):
        return (
            {
                "error": str(exc),
                "provider": exc.provider,
                "provider_status": exc.provider_status,
                "fallback_used": False,
            },
            exc.http_status,
        )
    if isinstance(exc, PermissionError):
        return (
            {
                "error": str(exc),
                "provider": provider,
                "provider_status": "failed_auth",
                "fallback_used": False,
            },
            403,
        )
    if isinstance(exc, RuntimeError) and "ANTHROPIC_API_KEY" in str(exc):
        return (
            {
                "error": str(exc),
                "provider": provider,
                "provider_status": "failed_auth",
                "fallback_used": False,
            },
            503,
        )
    if isinstance(exc, RuntimeError):
        return (
            {
                "error": str(exc),
                "provider": provider,
                "provider_status": "unknown",
                "fallback_used": False,
            },
            503,
        )
    return (
        {
            "error": str(exc),
            "provider": provider,
            "provider_status": "unknown",
            "fallback_used": False,
        },
        500,
    )


def build_provider_meta(
    *,
    provider: str,
    model: str | None,
    provider_status: str = "success",
    fallback_used: bool = False,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    status = provider_status if provider_status in VALID_PROVIDER_STATUSES else "unknown"
    return {
        "provider": provider,
        "model": model or "",
        "provider_status": status,
        "fallback_used": bool(fallback_used),
        "latency_ms": latency_ms,
    }


def apply_parse_fallback(provider_meta: dict[str, Any], ai_review: dict[str, Any]) -> dict[str, Any]:
    """When normalizer could not parse model JSON, do not label output as Claude success."""
    if ai_review.get("parse_success", True):
        return provider_meta
    out = dict(provider_meta)
    out["provider_status"] = "fallback_used"
    out["fallback_used"] = True
    out["model"] = DETERMINISTIC_NORMALIZER_MODEL
    return out


def provider_meta_from_persisted(
    *,
    provider: str,
    model: str,
    ai_review: dict[str, Any],
) -> dict[str, Any]:
    meta = build_provider_meta(
        provider=provider,
        model=model,
        provider_status="success",
        fallback_used=False,
    )
    return apply_parse_fallback(meta, ai_review)
