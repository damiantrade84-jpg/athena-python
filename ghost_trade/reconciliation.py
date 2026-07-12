"""Shadow and verified-demo position reconciliation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .models import Candle, Direction, GhostPosition, Venue
from .persistence import GhostRepository


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


class DemoPositionReconciler:
    def __init__(
        self,
        repository: GhostRepository,
        *,
        broker_positions_provider: Callable[[Venue], Any],
        clock: Callable[[], datetime] | None = None,
    ):
        self._repository = repository
        self._provider = broker_positions_provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _broker_id(row: Mapping[str, Any]) -> str:
        value = row.get("id") or row.get("ticket") or row.get("position_id") or row.get("positionId")
        return str(value) if value is not None else ""

    def run(self) -> dict[str, Any]:
        positions = [
            position for position in self._repository.list_positions()
            if position.mode == "DEMO" and position.status in {"OPEN", "RECONCILIATION_MISMATCH"}
        ]
        checked = closed = mismatches = 0
        errors: list[str] = []
        by_venue: dict[Venue, list[GhostPosition]] = {}
        for position in positions:
            by_venue.setdefault(position.venue, []).append(position)
        for venue, venue_positions in by_venue.items():
            try:
                response = self._provider(venue)
            except Exception as exc:
                errors.append(f"{venue.value.lower()}_positions_failed:{exc}")
                continue
            if isinstance(response, Mapping) and response.get("error"):
                errors.append(
                    f"{venue.value.lower()}_positions_failed:{response.get('detail') or response.get('error')}"
                )
                continue
            rows = response.get("positions", []) if isinstance(response, Mapping) else response
            if not isinstance(rows, Sequence):
                errors.append(f"{venue.value.lower()}_positions_failed:invalid_response")
                continue
            indexed = {
                self._broker_id(row): row
                for row in rows
                if isinstance(row, Mapping) and self._broker_id(row)
            }
            for position in venue_positions:
                checked += 1
                broker_id = str(position.broker_position_id or "")
                row = indexed.get(broker_id)
                if row is None:
                    mismatches += 1
                    self._repository.set_position_status(
                        position.position_id, "RECONCILIATION_MISMATCH"
                    )
                    continue
                status = str(row.get("status") or "open").lower()
                raw_size = row.get("size", row.get("volume", row.get("contracts")))
                try:
                    size_zero = raw_size is not None and float(raw_size) == 0
                except (TypeError, ValueError):
                    size_zero = False
                if status not in {"closed", "filled", "cancelled"} and not size_zero:
                    if position.status == "RECONCILIATION_MISMATCH":
                        self._repository.set_position_status(position.position_id, "OPEN")
                    continue
                try:
                    exit_price = float(row.get("exit_price", row.get("exitPrice")))
                except (TypeError, ValueError):
                    errors.append(f"position_close_missing_price:{position.position_id}")
                    continue
                gross_r = (
                    (exit_price - position.entry) / position.initial_risk
                    if position.direction is Direction.LONG
                    else (position.entry - exit_price) / position.initial_risk
                )
                closed_at = row.get("closed_at") or row.get("closedAt")
                if isinstance(closed_at, str):
                    closed_time = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                elif isinstance(closed_at, datetime):
                    closed_time = closed_at
                else:
                    closed_time = self._clock()
                self._repository.close_position(
                    position,
                    closed_at=closed_time,
                    exit_price=exit_price,
                    gross_r=gross_r,
                    exit_reason=str(row.get("exit_reason") or row.get("exitReason") or "BROKER_CLOSED"),
                    payload={"broker": dict(row)},
                )
                closed += 1
        return {
            "checked": checked,
            "closed": closed,
            "mismatches": mismatches,
            "errors": errors,
        }
