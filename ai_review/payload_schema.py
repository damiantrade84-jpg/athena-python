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
    engine_a_context: dict[str, Any]
    prompt: str
    mismatch_warnings: list[str] = field(default_factory=list)
    # Additive read-only strategy-playbook layer. None when the feature flag
    # is off OR the builder errored — the rest of the payload is unchanged.
    strategy_layer: dict[str, Any] | None = None


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
        engine_a_context=engine_a_context,
        prompt=prompt,
        mismatch_warnings=list(mismatch_warnings or []),
        strategy_layer=strategy_layer,
    )
