"""Ghost-only duplicate, cooldown, and portfolio-risk gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .config import GhostConfig
from .models import GhostPosition, GhostSignal


@dataclass(frozen=True, slots=True)
class GateResult:
    approved: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    current_total_risk: float
    projected_total_risk: float
    group_exposure: Mapping[str, float]


class GhostRiskService:
    def __init__(
        self,
        config: GhostConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate(
        self,
        signal: GhostSignal,
        open_positions: Sequence[GhostPosition],
        *,
        recent_closed: Sequence[Mapping[str, Any]] = (),
        other_engine_positions: Sequence[Mapping[str, Any]] = (),
    ) -> GateResult:
        active = [position for position in open_positions if position.status == "OPEN"]
        reasons: list[str] = []
        warnings: list[str] = []
        if any(position.signal_id == signal.signal_id for position in active):
            reasons.append("signal_already_open")
        if any(
            position.canonical_symbol == signal.instrument.canonical_symbol
            and position.direction is signal.direction
            for position in active
        ):
            reasons.append("duplicate_symbol_direction")
        if len(active) >= self.config.max_open_positions:
            reasons.append("max_open_positions_reached")

        current_risk = sum(
            float(position.payload.get("risk_fraction", self.config.risk_per_trade))
            for position in active
        )
        projected = current_risk + self.config.risk_per_trade
        if projected > self.config.max_total_risk + 1e-12:
            reasons.append("max_total_risk_exceeded")

        now = self._clock()
        for row in recent_closed:
            if str(row.get("canonical_symbol") or "") != signal.instrument.canonical_symbol:
                continue
            closed_at = row.get("closed_at")
            if isinstance(closed_at, str):
                closed_at = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            if isinstance(closed_at, datetime):
                if closed_at.tzinfo is None:
                    closed_at = closed_at.replace(tzinfo=timezone.utc)
                if (now - closed_at).total_seconds() < self.config.signal_cooldown_seconds:
                    reasons.append("signal_cooldown_active")
                    break

        if any(
            str(row.get("symbol") or row.get("canonical_symbol") or "")
            == signal.instrument.canonical_symbol
            for row in other_engine_positions
        ):
            warnings.append("other_engine_symbol_conflict")

        group_exposure: dict[str, float] = {}
        for position in active:
            key = position.asset_group.value
            group_exposure[key] = group_exposure.get(key, 0.0) + float(
                position.payload.get("risk_fraction", self.config.risk_per_trade)
            )
        signal_group = signal.instrument.asset_group.value
        group_exposure[signal_group] = group_exposure.get(signal_group, 0.0) + self.config.risk_per_trade
        return GateResult(
            approved=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
            warnings=tuple(dict.fromkeys(warnings)),
            current_total_risk=current_risk,
            projected_total_risk=projected,
            group_exposure=group_exposure,
        )
