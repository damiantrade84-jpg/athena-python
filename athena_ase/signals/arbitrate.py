"""Signal arbitration and candidate emission (ASE v2.1 §3.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from athena_ase.horizon import Horizon, HORIZONS
from athena_ase.instruments import Instrument
from athena_ase.settings import arbitration_mode, arbitration_strength_floor


@dataclass(frozen=True)
class FiredSignal:
    name: str
    direction: int
    raw_strength: float
    context: dict[str, float] = field(default_factory=dict)


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
    aggregate_strength: float = 0.0
    conflict_score: float = 0.0


def _max_mode_direction(
    longs: list[FiredSignal],
    shorts: list[FiredSignal],
    *,
    floor: float,
) -> tuple[int, bool, float, float]:
    long_max = max((s.raw_strength for s in longs), default=0.0)
    short_max = max((s.raw_strength for s in shorts), default=0.0)
    direction = 0
    conflict_flag = False
    conflict_score = 0.0
    aggregate_strength = 0.0

    if longs and shorts:
        if long_max >= floor and short_max >= floor:
            conflict_score = min(long_max, short_max) / max(long_max, short_max)
            if long_max >= 2.0 * short_max:
                direction = 1
                conflict_flag = True
            elif short_max >= 2.0 * long_max:
                direction = -1
                conflict_flag = True
            else:
                return 0, False, 0.0, 0.0
        elif long_max >= floor:
            direction = 1
            conflict_flag = True
            conflict_score = short_max / long_max if long_max > 0 else 0.0
        elif short_max >= floor:
            direction = -1
            conflict_flag = True
            conflict_score = long_max / short_max if short_max > 0 else 0.0
        else:
            return 0, False, 0.0, 0.0
        aggregate_strength = long_max if direction > 0 else short_max if direction < 0 else 0.0
    elif longs:
        direction = 1
        aggregate_strength = long_max
    elif shorts:
        direction = -1
        aggregate_strength = short_max

    return direction, conflict_flag, aggregate_strength, conflict_score


def _weighted_mode_direction(
    active: list[FiredSignal],
    *,
    floor: float,
) -> tuple[int, bool, float, float]:
    long_sum = sum(s.raw_strength for s in active if s.direction > 0 and s.raw_strength >= floor)
    short_sum = sum(s.raw_strength for s in active if s.direction < 0 and s.raw_strength >= floor)
    long_any = any(s.direction > 0 for s in active)
    short_any = any(s.direction < 0 for s in active)
    net = long_sum - short_sum
    if abs(net) < floor:
        return 0, False, 0.0, 0.0
    direction = 1 if net > 0 else -1
    conflict_flag = long_any and short_any
    denom = max(long_sum, short_sum, 1e-12)
    conflict_score = min(long_sum, short_sum) / denom if conflict_flag else 0.0
    return direction, conflict_flag, abs(net), conflict_score


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

    floor = arbitration_strength_floor()
    longs = [s for s in active if s.direction > 0]
    shorts = [s for s in active if s.direction < 0]

    if arbitration_mode() == "weighted":
        direction, conflict_flag, aggregate_strength, conflict_score = _weighted_mode_direction(
            active, floor=floor
        )
    else:
        direction, conflict_flag, aggregate_strength, conflict_score = _max_mode_direction(
            longs, shorts, floor=floor
        )

    if direction == 0:
        return None

    agreeing = [s for s in active if s.direction == direction]
    sig_dicts = []
    for signal in active:
        row: dict[str, Any] = {
            "name": signal.name,
            "direction": signal.direction,
            "rawStrength": signal.raw_strength,
        }
        row.update(signal.context)
        sig_dicts.append(row)
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
        aggregate_strength=aggregate_strength,
        conflict_score=conflict_score,
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
