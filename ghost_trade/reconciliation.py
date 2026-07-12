"""Shadow-only position resolution helpers."""

from __future__ import annotations

from typing import Sequence

from .models import Candle, Direction, GhostPosition


def resolve_shadow_position(
    position: GhostPosition, candles: Sequence[Candle]
) -> dict | None:
    for candle in candles:
        if direction_is_long := position.direction is Direction.LONG:
            stop_hit = candle.low <= position.stop
            target_hit = candle.high >= position.target
        else:
            stop_hit = candle.high >= position.stop
            target_hit = candle.low <= position.target
        ambiguous = stop_hit and target_hit
        if stop_hit:
            return {
                "reason": "SL",
                "price": position.stop,
                "gross_r": -1.0,
                "closed_at": candle.close_time,
                "ambiguous": ambiguous,
            }
        if target_hit:
            gross = (
                (position.target - position.entry) / position.initial_risk
                if direction_is_long
                else (position.entry - position.target) / position.initial_risk
            )
            return {
                "reason": "TP",
                "price": position.target,
                "gross_r": gross,
                "closed_at": candle.close_time,
                "ambiguous": False,
            }
    return None
