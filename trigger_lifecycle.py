"""Trigger lifecycle state machine (timeframe-architecture migration, Phase 4).

Self-contained, side-effect-free module. Triggers move through
``IDLE -> ARMED -> TRIGGERED -> EXECUTABLE -> FILLED`` with ``EXPIRED`` /
``INVALIDATED`` terminal branches. Expiry is mandatory: every armed trigger
carries an ``expires_at`` derived from a TTL and/or a max trigger-bar count,
so stale M15/M5 triggers can never remain executable indefinitely.

The module never reads the wall clock: every timestamp is supplied by the
caller, keeping live and backtest behaviour deterministic and identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class TransitionError(RuntimeError):
    """Raised when an illegal trigger-state transition is attempted."""


class TriggerState(str, Enum):
    """Lifecycle states for a tracked trigger."""

    IDLE = "IDLE"
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    EXECUTABLE = "EXECUTABLE"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    FILLED = "FILLED"


#: Legal transitions. EXPIRED / INVALIDATED / FILLED are terminal.
_TRANSITIONS: Dict[TriggerState, frozenset] = {
    TriggerState.IDLE: frozenset({TriggerState.ARMED}),
    TriggerState.ARMED: frozenset(
        {TriggerState.TRIGGERED, TriggerState.EXPIRED, TriggerState.INVALIDATED}
    ),
    TriggerState.TRIGGERED: frozenset(
        {TriggerState.EXECUTABLE, TriggerState.EXPIRED, TriggerState.INVALIDATED}
    ),
    TriggerState.EXECUTABLE: frozenset(
        {TriggerState.FILLED, TriggerState.EXPIRED, TriggerState.INVALIDATED}
    ),
    TriggerState.EXPIRED: frozenset(),
    TriggerState.INVALIDATED: frozenset(),
    TriggerState.FILLED: frozenset(),
}

#: Non-terminal states eligible for expiry sweeps.
_SWEEPABLE = frozenset(
    {TriggerState.ARMED, TriggerState.TRIGGERED, TriggerState.EXECUTABLE}
)


def _ensure_transition(current: TriggerState, target: TriggerState) -> None:
    """Fail-closed transition guard; raises TransitionError if illegal."""
    if target not in _TRANSITIONS[current]:
        raise TransitionError(
            f"Illegal trigger transition {current.value} -> {target.value}"
        )


def _to_iso(ts: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime as ISO-8601 UTC (or None)."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def _from_iso(raw: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp to an aware UTC datetime (or None)."""
    if raw is None:
        return None
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


@dataclass
class TriggerRecord:
    """Persistent lifecycle record for one tracked trigger."""

    signal_id: str
    symbol: str
    engine: str
    style: str
    direction: str
    profile: str
    regime_tf: str
    bias_tf: str
    structure_tf: str
    setup_tf: str
    trigger_tf: str
    setup_zone: Optional[Tuple[float, float]] = None
    trigger_level: Optional[float] = None
    armed_at: Optional[datetime] = None
    triggered_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    invalidated_at: Optional[datetime] = None
    fill_timestamp: Optional[datetime] = None
    fill_price: Optional[float] = None
    fill_source: Optional[str] = None
    spread_at_fill: Optional[float] = None
    distance_from_setup: Optional[float] = None
    distance_from_trigger: Optional[float] = None
    actual_rr_at_fill: Optional[float] = None
    invalidation_reason: Optional[str] = None
    state: TriggerState = TriggerState.IDLE

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe dict; timestamps as ISO-8601 UTC strings."""
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "engine": self.engine,
            "style": self.style,
            "direction": self.direction,
            "profile": self.profile,
            "regime_tf": self.regime_tf,
            "bias_tf": self.bias_tf,
            "structure_tf": self.structure_tf,
            "setup_tf": self.setup_tf,
            "trigger_tf": self.trigger_tf,
            "setup_zone": list(self.setup_zone) if self.setup_zone is not None else None,
            "trigger_level": self.trigger_level,
            "armed_at": _to_iso(self.armed_at),
            "triggered_at": _to_iso(self.triggered_at),
            "expires_at": _to_iso(self.expires_at),
            "invalidated_at": _to_iso(self.invalidated_at),
            "fill_timestamp": _to_iso(self.fill_timestamp),
            "fill_price": self.fill_price,
            "fill_source": self.fill_source,
            "spread_at_fill": self.spread_at_fill,
            "distance_from_setup": self.distance_from_setup,
            "distance_from_trigger": self.distance_from_trigger,
            "actual_rr_at_fill": self.actual_rr_at_fill,
            "invalidation_reason": self.invalidation_reason,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TriggerRecord":
        """Rebuild a record from :meth:`to_dict` output."""
        zone = data.get("setup_zone")
        return cls(
            signal_id=data["signal_id"],
            symbol=data["symbol"],
            engine=data["engine"],
            style=data["style"],
            direction=data["direction"],
            profile=data["profile"],
            regime_tf=data["regime_tf"],
            bias_tf=data["bias_tf"],
            structure_tf=data["structure_tf"],
            setup_tf=data["setup_tf"],
            trigger_tf=data["trigger_tf"],
            setup_zone=tuple(zone) if zone is not None else None,
            trigger_level=data.get("trigger_level"),
            armed_at=_from_iso(data.get("armed_at")),
            triggered_at=_from_iso(data.get("triggered_at")),
            expires_at=_from_iso(data.get("expires_at")),
            invalidated_at=_from_iso(data.get("invalidated_at")),
            fill_timestamp=_from_iso(data.get("fill_timestamp")),
            fill_price=data.get("fill_price"),
            fill_source=data.get("fill_source"),
            spread_at_fill=data.get("spread_at_fill"),
            distance_from_setup=data.get("distance_from_setup"),
            distance_from_trigger=data.get("distance_from_trigger"),
            actual_rr_at_fill=data.get("actual_rr_at_fill"),
            invalidation_reason=data.get("invalidation_reason"),
            state=TriggerState(data["state"]),
        )


class TriggerTracker:
    """In-memory, deterministic trigger-lifecycle tracker keyed by signal_id."""

    def __init__(self) -> None:
        self._records: Dict[str, TriggerRecord] = {}

    def get(self, signal_id: str) -> Optional[TriggerRecord]:
        """Return the record for ``signal_id`` or None."""
        return self._records.get(signal_id)

    def arm(
        self,
        *,
        signal_id: str,
        symbol: str,
        engine: str,
        style: str,
        direction: str,
        profile: str,
        regime_tf: str,
        bias_tf: str,
        structure_tf: str,
        setup_tf: str,
        trigger_tf: str,
        armed_at: datetime,
        setup_zone: Optional[Tuple[float, float]] = None,
        trigger_level: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        max_trigger_bars: Optional[int] = None,
        bar_seconds: Optional[float] = None,
    ) -> TriggerRecord:
        """Arm a new trigger (IDLE -> ARMED).

        Expiry is mandatory: at least one of ``ttl_seconds`` or
        ``max_trigger_bars`` (with ``bar_seconds``) must be supplied;
        ``expires_at`` is the earliest bound provided. There is no path
        that leaves a trigger without an expiry.
        """
        if signal_id in self._records:
            raise ValueError(f"signal_id already tracked: {signal_id}")
        bounds: List[datetime] = []
        if ttl_seconds is not None:
            bounds.append(armed_at + timedelta(seconds=ttl_seconds))
        if max_trigger_bars is not None:
            if bar_seconds is None:
                raise ValueError("bar_seconds required with max_trigger_bars")
            bounds.append(armed_at + timedelta(seconds=max_trigger_bars * bar_seconds))
        if not bounds:
            raise ValueError(
                "expiry required: supply ttl_seconds and/or max_trigger_bars"
            )
        record = TriggerRecord(
            signal_id=signal_id,
            symbol=symbol,
            engine=engine,
            style=style,
            direction=direction,
            profile=profile,
            regime_tf=regime_tf,
            bias_tf=bias_tf,
            structure_tf=structure_tf,
            setup_tf=setup_tf,
            trigger_tf=trigger_tf,
            setup_zone=setup_zone,
            trigger_level=trigger_level,
            armed_at=armed_at,
            expires_at=min(bounds),
            state=TriggerState.IDLE,
        )
        _ensure_transition(record.state, TriggerState.ARMED)
        record.state = TriggerState.ARMED
        self._records[signal_id] = record
        return record

    def mark_triggered(self, signal_id: str, triggered_at: datetime) -> TriggerRecord:
        """ARMED -> TRIGGERED. Rejects already-expired triggers (fail-closed)."""
        record = self._require(signal_id)
        self._ensure_not_expired(record, triggered_at)
        _ensure_transition(record.state, TriggerState.TRIGGERED)
        record.state = TriggerState.TRIGGERED
        record.triggered_at = triggered_at
        return record

    def mark_executable(self, signal_id: str, now: datetime) -> TriggerRecord:
        """TRIGGERED -> EXECUTABLE. Rejects already-expired triggers."""
        record = self._require(signal_id)
        self._ensure_not_expired(record, now)
        _ensure_transition(record.state, TriggerState.EXECUTABLE)
        record.state = TriggerState.EXECUTABLE
        return record

    def expire(self, signal_id: str) -> TriggerRecord:
        """ARMED/TRIGGERED/EXECUTABLE -> EXPIRED (terminal)."""
        record = self._require(signal_id)
        _ensure_transition(record.state, TriggerState.EXPIRED)
        record.state = TriggerState.EXPIRED
        return record

    def invalidate(
        self, signal_id: str, invalidated_at: datetime, reason: str
    ) -> TriggerRecord:
        """ARMED/TRIGGERED/EXECUTABLE -> INVALIDATED (terminal)."""
        record = self._require(signal_id)
        _ensure_transition(record.state, TriggerState.INVALIDATED)
        record.state = TriggerState.INVALIDATED
        record.invalidated_at = invalidated_at
        record.invalidation_reason = reason
        return record

    def fill(
        self,
        signal_id: str,
        *,
        fill_timestamp: datetime,
        fill_price: float,
        fill_source: str,
        spread_at_fill: Optional[float] = None,
        distance_from_setup: Optional[float] = None,
        distance_from_trigger: Optional[float] = None,
        actual_rr_at_fill: Optional[float] = None,
    ) -> TriggerRecord:
        """EXECUTABLE -> FILLED (terminal), recording fill diagnostics."""
        record = self._require(signal_id)
        _ensure_transition(record.state, TriggerState.FILLED)
        record.state = TriggerState.FILLED
        record.fill_timestamp = fill_timestamp
        record.fill_price = fill_price
        record.fill_source = fill_source
        record.spread_at_fill = spread_at_fill
        record.distance_from_setup = distance_from_setup
        record.distance_from_trigger = distance_from_trigger
        record.actual_rr_at_fill = actual_rr_at_fill
        return record

    def sweep_expired(self, now: datetime) -> List[str]:
        """Expire overdue ARMED/TRIGGERED/EXECUTABLE records.

        Returns the signal_ids expired by this sweep, in insertion order.
        Terminal and not-yet-due records are untouched.
        """
        expired: List[str] = []
        for record in self._records.values():
            if (
                record.state in _SWEEPABLE
                and record.expires_at is not None
                and now >= record.expires_at
            ):
                self.expire(record.signal_id)
                expired.append(record.signal_id)
        return expired

    def _require(self, signal_id: str) -> TriggerRecord:
        record = self._records.get(signal_id)
        if record is None:
            raise KeyError(f"unknown signal_id: {signal_id}")
        return record

    @staticmethod
    def _ensure_not_expired(record: TriggerRecord, now: datetime) -> None:
        if record.expires_at is not None and now >= record.expires_at:
            raise TransitionError(
                f"trigger {record.signal_id} expired at "
                f"{_to_iso(record.expires_at)}; cannot advance state"
            )
