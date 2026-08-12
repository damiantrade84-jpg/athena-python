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


def _validate_chart_image(
    *,
    role: str,
    screenshot: Any,
    screenshot_meta: Any,
    cfg: dict[str, Any],
) -> ValidationError | None:
    field = "screenshot_base64" if role == "structure" else "entry_screenshot_base64"
    meta_field = "screenshot_meta" if role == "structure" else "entry_screenshot_meta"
    meta = screenshot_meta if isinstance(screenshot_meta, dict) else {}

    if cfg.get("REQUIRE_SCREENSHOT") and not screenshot:
        return ValidationError(f"{field} is required", 400)

    if screenshot:
        if not str(screenshot).startswith("data:image/png;base64,"):
            return ValidationError(f"{field} must be a PNG data URL", 415)
        decoded = _decode_png_bytes(str(screenshot))
        if decoded is None:
            return ValidationError(f"{field} is not valid base64 PNG data", 415)
        max_bytes = int(cfg.get("MAX_IMAGE_BYTES") or 0)
        if max_bytes and len(decoded) > max_bytes:
            return ValidationError(
                f"{role} screenshot exceeds MAX_IMAGE_BYTES ({max_bytes})", 413
            )

    if meta.get("native_chart") is not True:
        return ValidationError(f"{meta_field}.native_chart must be true", 400)
    if str(meta.get("review_role") or "").strip().lower() != role:
        return ValidationError(f"{meta_field}.review_role must be {role}", 400)
    if screenshot and not str(meta.get("chart_timeframe") or "").strip():
        return ValidationError(f"{meta_field}.chart_timeframe is required", 400)
    return None


def validate_request(data: dict[str, Any], cfg: dict[str, Any]) -> ValidationError | None:
    symbol = str(data.get("symbol") or "").strip()
    timeframe = str(data.get("timeframe") or "").strip()
    screenshot = data.get("screenshot_base64")
    screenshot_meta = data.get("screenshot_meta") or {}
    entry_screenshot = data.get("entry_screenshot_base64")
    entry_screenshot_meta = data.get("entry_screenshot_meta") or {}

    if not symbol or not timeframe:
        return ValidationError("symbol and timeframe are required", 400)

    structure_error = _validate_chart_image(
        role="structure",
        screenshot=screenshot,
        screenshot_meta=screenshot_meta,
        cfg=cfg,
    )
    if structure_error:
        return structure_error
    entry_error = _validate_chart_image(
        role="entry",
        screenshot=entry_screenshot,
        screenshot_meta=entry_screenshot_meta,
        cfg=cfg,
    )
    if entry_error:
        return entry_error

    structure_tf = str(screenshot_meta.get("chart_timeframe") or "").strip().upper()
    if structure_tf and structure_tf != timeframe.upper():
        return ValidationError(
            "timeframe must match screenshot_meta.chart_timeframe for the structure image",
            400,
        )

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
