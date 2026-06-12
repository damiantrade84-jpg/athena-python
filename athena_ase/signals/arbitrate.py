"""Signal arbitration and candidate emission (ASE v2.1 §3.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from athena_ase.horizon import Horizon, HORIZONS
from athena_ase.instruments import Instrument


@dataclass(frozen=True)
class FiredSignal:
    name: str
    direction: int
    raw_strength: float


@dataclass
class Candidate:
    instrument: str
    family: str
    horizon: Horizon
    decision_time_ms: int
    bar_index: int
    direction: int
    signals: list[dict[str, Any]] = field(default_factory=list)
    agreement_count: int = 0
    conflict_flag: bool = False
    sigma_bar: float = 0.0
    entry_log: float = 0.0


def arbitrate(
    instrument: Instrument,
    horizon: Horizon,
    decision_time_ms: int,
    bar_index: int,
    entry_log: float,
    sigma_bar: float,
    fired: list[FiredSignal],
) -> Candidate | None:
    active = [s for s in fired if s.direction != 0 and s.raw_strength > 0]
    if not active:
        return None

    longs = [s for s in active if s.direction > 0]
    shorts = [s for s in active if s.direction < 0]
    long_max = max((s.raw_strength for s in longs), default=0.0)
    short_max = max((s.raw_strength for s in shorts), default=0.0)

    direction = 0
    conflict_flag = False
    if longs and shorts:
        if long_max >= 0.3 and short_max >= 0.3:
            if long_max >= 2.0 * short_max:
                direction = 1
                conflict_flag = True
            elif short_max >= 2.0 * long_max:
                direction = -1
                conflict_flag = True
            else:
                return None
        elif longs and not shorts:
            direction = 1
        elif shorts and not longs:
            direction = -1
    elif longs:
        direction = 1
    elif shorts:
        direction = -1

    if direction == 0:
        return None

    agreeing = [s for s in active if s.direction == direction]
    sig_dicts = [
        {"name": s.name, "direction": s.direction, "rawStrength": s.raw_strength}
        for s in active
    ]
    return Candidate(
        instrument=instrument.symbol,
        family=instrument.family,
        horizon=horizon,
        decision_time_ms=decision_time_ms,
        bar_index=bar_index,
        direction=direction,
        signals=sig_dicts,
        agreement_count=len(agreeing),
        conflict_flag=conflict_flag,
        sigma_bar=sigma_bar,
        entry_log=entry_log,
    )


class EventSpacingFilter:
    """Suppress same-direction candidates for H/2 bars after emission."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, Horizon], tuple[int, int]] = {}

    def allow(self, candidate: Candidate) -> bool:
        key = (candidate.instrument, candidate.horizon)
        h = HORIZONS[candidate.horizon].max_hold_bars
        spacing = max(h // 2, 1)
        prev = self._last.get(key)
        if prev is None:
            return True
        prev_idx, prev_dir = prev
        if candidate.direction != prev_dir:
            return True
        if candidate.bar_index - prev_idx < spacing:
            return False
        return True

    def record(self, candidate: Candidate) -> None:
        self._last[(candidate.instrument, candidate.horizon)] = (
            candidate.bar_index,
            candidate.direction,
        )
