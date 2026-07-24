"""Tests for trigger_lifecycle (Phase 4 trigger state machine)."""

from datetime import datetime, timedelta, timezone

import pytest

from trigger_lifecycle import (
    TransitionError,
    TriggerRecord,
    TriggerState,
    TriggerTracker,
)

T0 = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)

ARM_KWARGS = dict(
    signal_id="sig-1",
    symbol="XAUUSD",
    engine="B",
    style="intraday",
    direction="long",
    profile="metals_spot",
    regime_tf="D1",
    bias_tf="H4",
    structure_tf="H1",
    setup_tf="M30",
    trigger_tf="M15",
    setup_zone=(2400.0, 2402.5),
    trigger_level=2403.0,
    armed_at=T0,
)


def _armed_tracker(**overrides):
    tracker = TriggerTracker()
    kwargs = {**ARM_KWARGS, **overrides}
    tracker.arm(**kwargs)
    return tracker


def _walk_to_executable(tracker, signal_id="sig-1"):
    tracker.mark_triggered(signal_id, T0 + timedelta(minutes=15))
    tracker.mark_executable(signal_id, T0 + timedelta(minutes=30))


def test_happy_path_full_lifecycle_all_fields():
    tracker = _armed_tracker(ttl_seconds=3600, max_trigger_bars=8, bar_seconds=900)
    rec = tracker.get("sig-1")
    assert rec.state == TriggerState.ARMED
    assert rec.armed_at == T0
    # earliest of TTL (3600s) and bars (8 * 900 = 7200s) -> TTL wins
    assert rec.expires_at == T0 + timedelta(seconds=3600)

    tracker.mark_triggered("sig-1", T0 + timedelta(minutes=15))
    assert tracker.get("sig-1").state == TriggerState.TRIGGERED
    assert tracker.get("sig-1").triggered_at == T0 + timedelta(minutes=15)

    tracker.mark_executable("sig-1", T0 + timedelta(minutes=30))
    assert tracker.get("sig-1").state == TriggerState.EXECUTABLE

    fill_ts = T0 + timedelta(minutes=31)
    tracker.fill(
        "sig-1",
        fill_timestamp=fill_ts,
        fill_price=2403.5,
        fill_source="live_quote",
        spread_at_fill=0.35,
        distance_from_setup=1.0,
        distance_from_trigger=0.5,
        actual_rr_at_fill=1.8,
    )
    rec = tracker.get("sig-1")
    assert rec.state == TriggerState.FILLED
    assert rec.signal_id == "sig-1"
    assert rec.symbol == "XAUUSD"
    assert rec.engine == "B"
    assert rec.style == "intraday"
    assert rec.direction == "long"
    assert rec.profile == "metals_spot"
    assert rec.regime_tf == "D1"
    assert rec.bias_tf == "H4"
    assert rec.structure_tf == "H1"
    assert rec.setup_tf == "M30"
    assert rec.trigger_tf == "M15"
    assert rec.setup_zone == (2400.0, 2402.5)
    assert rec.trigger_level == 2403.0
    assert rec.fill_timestamp == fill_ts
    assert rec.fill_price == 2403.5
    assert rec.fill_source == "live_quote"
    assert rec.spread_at_fill == 0.35
    assert rec.distance_from_setup == 1.0
    assert rec.distance_from_trigger == 0.5
    assert rec.actual_rr_at_fill == 1.8


def test_expiry_via_ttl():
    tracker = _armed_tracker(ttl_seconds=1800)
    assert tracker.get("sig-1").expires_at == T0 + timedelta(seconds=1800)


def test_expiry_via_bar_count():
    tracker = _armed_tracker(max_trigger_bars=4, bar_seconds=900)
    assert tracker.get("sig-1").expires_at == T0 + timedelta(seconds=3600)


def test_expiry_is_earliest_of_ttl_and_bars():
    tracker = _armed_tracker(ttl_seconds=7200, max_trigger_bars=2, bar_seconds=900)
    assert tracker.get("sig-1").expires_at == T0 + timedelta(seconds=1800)


def test_arm_requires_expiry_bound():
    with pytest.raises(ValueError):
        _armed_tracker()


def test_arm_bar_count_requires_bar_seconds():
    with pytest.raises(ValueError):
        _armed_tracker(max_trigger_bars=4)


def test_duplicate_signal_id_rejected():
    tracker = _armed_tracker(ttl_seconds=60)
    with pytest.raises(ValueError):
        tracker.arm(**{**ARM_KWARGS, "ttl_seconds": 60})


@pytest.mark.parametrize("stage", ["armed", "triggered", "executable"])
def test_invalidate_from_each_non_terminal_state(stage):
    tracker = _armed_tracker(ttl_seconds=3600)
    if stage in ("triggered", "executable"):
        tracker.mark_triggered("sig-1", T0 + timedelta(minutes=1))
    if stage == "executable":
        tracker.mark_executable("sig-1", T0 + timedelta(minutes=2))
    tracker.invalidate("sig-1", T0 + timedelta(minutes=3), reason="bos_against")
    rec = tracker.get("sig-1")
    assert rec.state == TriggerState.INVALIDATED
    assert rec.invalidated_at == T0 + timedelta(minutes=3)
    assert rec.invalidation_reason == "bos_against"


def test_illegal_transitions_raise():
    tracker = _armed_tracker(ttl_seconds=3600)
    # cannot fill or become executable straight from ARMED
    with pytest.raises(TransitionError):
        tracker.fill("sig-1", fill_timestamp=T0, fill_price=1.0, fill_source="x")
    with pytest.raises(TransitionError):
        tracker.mark_executable("sig-1", T0)
    # cannot re-trigger a TRIGGERED record
    tracker.mark_triggered("sig-1", T0 + timedelta(minutes=1))
    with pytest.raises(TransitionError):
        tracker.mark_triggered("sig-1", T0 + timedelta(minutes=2))
    with pytest.raises(TransitionError):
        tracker.fill("sig-1", fill_timestamp=T0, fill_price=1.0, fill_source="x")


def test_terminal_states_reject_further_transitions():
    for terminal in ("filled", "expired", "invalidated"):
        tracker = _armed_tracker(ttl_seconds=3600)
        _walk_to_executable(tracker)
        if terminal == "filled":
            tracker.fill(
                "sig-1", fill_timestamp=T0 + timedelta(minutes=31),
                fill_price=1.0, fill_source="live_quote",
            )
        elif terminal == "expired":
            tracker.expire("sig-1")
        else:
            tracker.invalidate("sig-1", T0 + timedelta(minutes=31), reason="r")
        with pytest.raises(TransitionError):
            tracker.expire("sig-1")
        with pytest.raises(TransitionError):
            tracker.invalidate("sig-1", T0 + timedelta(minutes=32), reason="r")
        with pytest.raises(TransitionError):
            tracker.fill(
                "sig-1", fill_timestamp=T0 + timedelta(minutes=32),
                fill_price=1.0, fill_source="live_quote",
            )


def test_expired_trigger_cannot_advance_fail_closed():
    tracker = _armed_tracker(ttl_seconds=1800)
    with pytest.raises(TransitionError):
        tracker.mark_triggered("sig-1", T0 + timedelta(seconds=1800))
    tracker.mark_triggered("sig-1", T0 + timedelta(seconds=900))
    with pytest.raises(TransitionError):
        tracker.mark_executable("sig-1", T0 + timedelta(seconds=1801))


def test_sweep_expired_only_touches_overdue_non_terminal():
    tracker = TriggerTracker()
    base = {**ARM_KWARGS, "ttl_seconds": 1800}
    # overdue ARMED
    tracker.arm(**base)
    # overdue TRIGGERED
    tracker.arm(**{**base, "signal_id": "sig-2"})
    tracker.mark_triggered("sig-2", T0 + timedelta(minutes=1))
    # overdue EXECUTABLE
    tracker.arm(**{**base, "signal_id": "sig-3"})
    tracker.mark_triggered("sig-3", T0 + timedelta(minutes=1))
    tracker.mark_executable("sig-3", T0 + timedelta(minutes=2))
    # not yet due (long TTL)
    tracker.arm(**{**base, "signal_id": "sig-4", "ttl_seconds": 7200})
    # already terminal
    tracker.arm(**{**base, "signal_id": "sig-5"})
    tracker.invalidate("sig-5", T0 + timedelta(minutes=1), reason="r")

    now = T0 + timedelta(seconds=1800)
    expired = tracker.sweep_expired(now)
    assert expired == ["sig-1", "sig-2", "sig-3"]
    assert tracker.get("sig-1").state == TriggerState.EXPIRED
    assert tracker.get("sig-2").state == TriggerState.EXPIRED
    assert tracker.get("sig-3").state == TriggerState.EXPIRED
    assert tracker.get("sig-4").state == TriggerState.ARMED
    assert tracker.get("sig-5").state == TriggerState.INVALIDATED
    # second sweep is a no-op
    assert tracker.sweep_expired(now) == []


def test_to_dict_from_dict_round_trip():
    tracker = _armed_tracker(ttl_seconds=3600)
    _walk_to_executable(tracker)
    tracker.fill(
        "sig-1",
        fill_timestamp=T0 + timedelta(minutes=31),
        fill_price=2403.5,
        fill_source="live_quote",
        spread_at_fill=0.35,
        distance_from_setup=1.0,
        distance_from_trigger=0.5,
        actual_rr_at_fill=1.8,
    )
    original = tracker.get("sig-1")
    restored = TriggerRecord.from_dict(original.to_dict())
    assert restored == original
    # timestamps survive as aware UTC datetimes
    assert restored.fill_timestamp == original.fill_timestamp
    assert restored.expires_at == original.expires_at
    assert restored.state == TriggerState.FILLED


def test_round_trip_with_none_optionals():
    tracker = _armed_tracker(ttl_seconds=60, setup_zone=None, trigger_level=None)
    original = tracker.get("sig-1")
    restored = TriggerRecord.from_dict(original.to_dict())
    assert restored == original
    assert restored.setup_zone is None
    assert restored.fill_price is None
