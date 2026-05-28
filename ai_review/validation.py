"""Request validation for AI chart review."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidationError:
    message: str
    status: int


def _bool_config(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return default
        return text in ("1", "true", "yes", "on")
    return bool(value)


def openai_review_enabled(cfg: dict[str, Any]) -> bool:
    if "OPENAI_REVIEW_ENABLED" in cfg:
        return _bool_config(cfg.get("OPENAI_REVIEW_ENABLED"), True)
    return _bool_config(cfg.get("ALLOW_OPENAI_PROVIDER"), False)


def _decode_png_bytes(data_url: str) -> bytes | None:
    if not data_url.startswith("data:image/png;base64,"):
        return None
    raw = data_url.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except Exception:
        return None


def validate_request(data: dict[str, Any], cfg: dict[str, Any]) -> ValidationError | None:
    symbol = str(data.get("symbol") or "").strip()
    timeframe = str(data.get("timeframe") or "").strip()
    screenshot = data.get("screenshot_base64")
    screenshot_meta = data.get("screenshot_meta") or {}

    if not symbol or not timeframe:
        return ValidationError("symbol and timeframe are required", 400)

    if cfg.get("REQUIRE_SCREENSHOT") and not screenshot:
        return ValidationError("screenshot_base64 is required", 400)

    if screenshot:
        if not str(screenshot).startswith("data:image/png;base64,"):
            return ValidationError("screenshot_base64 must be a PNG data URL", 415)
        decoded = _decode_png_bytes(str(screenshot))
        if decoded is None:
            return ValidationError("screenshot_base64 is not valid base64 PNG data", 415)
        max_bytes = int(cfg.get("MAX_IMAGE_BYTES") or 0)
        if max_bytes and len(decoded) > max_bytes:
            return ValidationError(
                f"screenshot exceeds MAX_IMAGE_BYTES ({max_bytes})", 413
            )

    if screenshot_meta.get("native_chart") is not True:
        return ValidationError("screenshot_meta.native_chart must be true", 400)

    provider = str(data.get("provider") or "default").strip().lower()
    if provider == "claude":
        provider = "anthropic"
    if provider == "grok":
        provider = "xai"
    if provider == "openai" and not openai_review_enabled(cfg):
        return ValidationError("OpenAI provider is disabled", 403)
    if provider == "dual" and not cfg.get("ALLOW_DUAL_PROVIDER"):
        return ValidationError("Dual provider is disabled", 403)

    return None


def decode_screenshot_bytes(data_url: str) -> bytes:
    decoded = _decode_png_bytes(data_url)
    if decoded is None:
        raise ValueError("invalid PNG data URL")
    return decoded
