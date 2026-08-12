"""Payload dataclasses for AI chart review."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChartReviewPayload:
    symbol: str
    timeframe: str
    screenshot_base64: str
    screenshot_meta: dict[str, Any]
    entry_screenshot_base64: str
    entry_screenshot_meta: dict[str, Any]
    engine_a_context: dict[str, Any]
    prompt: str
    mismatch_warnings: list[str] = field(default_factory=list)
    # Additive read-only strategy-playbook layer. None when the feature flag
    # is off OR the builder errored — the rest of the payload is unchanged.
    strategy_layer: dict[str, Any] | None = None


def review_image_inputs(payload: Any) -> list[dict[str, str]]:
    """Return the fixed structure/entry image order used by every provider.

    Missing roles remain empty so the provider rejects them. A structure image
    must never be silently reused as entry evidence.
    """
    structure_url = getattr(payload, "screenshot_base64", "")
    if not isinstance(structure_url, str):
        structure_url = ""
    entry_url = getattr(payload, "entry_screenshot_base64", "")
    if not isinstance(entry_url, str):
        entry_url = ""

    structure_meta = getattr(payload, "screenshot_meta", {})
    if not isinstance(structure_meta, dict):
        structure_meta = {}
    entry_meta = getattr(payload, "entry_screenshot_meta", {})
    if not isinstance(entry_meta, dict):
        entry_meta = {}

    structure_tf = str(
        structure_meta.get("chart_timeframe")
        or getattr(payload, "timeframe", "")
        or "UNKNOWN"
    ).upper()
    entry_tf = str(entry_meta.get("chart_timeframe") or structure_tf).upper()
    return [
        {"role": "STRUCTURE", "timeframe": structure_tf, "data_url": structure_url},
        {"role": "ENTRY", "timeframe": entry_tf, "data_url": entry_url},
    ]


def build_payload(
    request_data: dict[str, Any],
    engine_a_context: dict[str, Any],
    *,
    prompt: str,
    mismatch_warnings: list[str] | None = None,
    strategy_layer: dict[str, Any] | None = None,
) -> ChartReviewPayload:
    return ChartReviewPayload(
        symbol=str(request_data.get("symbol") or ""),
        timeframe=str(request_data.get("timeframe") or ""),
        screenshot_base64=str(request_data.get("screenshot_base64") or ""),
        screenshot_meta=dict(request_data.get("screenshot_meta") or {}),
        entry_screenshot_base64=str(request_data.get("entry_screenshot_base64") or ""),
        entry_screenshot_meta=dict(request_data.get("entry_screenshot_meta") or {}),
        engine_a_context=engine_a_context,
        prompt=prompt,
        mismatch_warnings=list(mismatch_warnings or []),
        strategy_layer=strategy_layer,
    )
