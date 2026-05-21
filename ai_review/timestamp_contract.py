"""Timestamp mismatch evaluation for AI chart review."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def evaluate_timestamp_mismatch(
    engine_a_ctx: dict[str, Any],
    screenshot_meta: dict[str, Any],
    cfg: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    scan_dt = _parse_iso(engine_a_ctx.get("scan_timestamp"))
    captured_dt = _parse_iso(screenshot_meta.get("captured_at"))
    max_delta = float(cfg.get("MISMATCH_WARN_MAX_SECONDS") or 120)

    if scan_dt and captured_dt:
        delta = abs((captured_dt - scan_dt).total_seconds())
        if delta > max_delta:
            warnings.append(
                f"chart captured {int(delta)}s from scan_timestamp (max {int(max_delta)}s)"
            )
    elif not captured_dt:
        warnings.append("screenshot_meta.captured_at missing")

    latest = _parse_iso(engine_a_ctx.get("latest_candle_ts"))
    if latest and captured_dt and captured_dt < latest:
        warnings.append("chart captured before latest_candle_ts")

    return warnings
