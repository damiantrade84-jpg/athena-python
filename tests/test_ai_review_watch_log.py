"""Phase 4: WATCH state transitions and audit logging."""

from __future__ import annotations

from ai_review.watch_log import (
    ARMED,
    EXPIRED,
    HELD,
    INVALIDATED,
    PROMOTED,
    RE_ARMED,
    build_watch_log_record,
    log_watch_transition,
    resolve_watch_transition,
)


def _watch_state(**overrides):
    state = {
        "state": "WATCH",
        "direction": "LONG",
        "watch_zone": {"low": 63.57, "high": 64.52, "center": 64.046},
        "invalidation": 62.2306,
        "sl_anchored": 62.7437,
        "min_atr_floor": 1.5,
        "shadow_only": True,
        "reason": "direction valid but entry poor",
        "rr_at_watch": {
            "rr_live_tp1": 3.34,
            "rr_live_tp2": 4.43,
            "rr_live_blended": 3.885,
        },
    }
    state.update(overrides)
    return state


def test_no_watch_either_side_is_no_transition():
    assert resolve_watch_transition(previous=None, current=None) is None


def test_arming_a_new_watch():
    assert resolve_watch_transition(previous=None, current=_watch_state()) == ARMED


def test_leaving_watch_without_invalidation_is_promotion():
    assert resolve_watch_transition(previous=_watch_state(), current=None) == PROMOTED


def test_price_through_invalidation_is_invalidated_not_promoted():
    transition = resolve_watch_transition(
        previous=_watch_state(),
        current=None,
        price=62.0,  # below EMA50 62.2306
    )
    assert transition == INVALIDATED


def test_short_invalidates_above_the_level():
    state = _watch_state(direction="SHORT", invalidation=66.0)
    assert resolve_watch_transition(previous=state, current=state, price=66.5) == INVALIDATED
    assert resolve_watch_transition(previous=state, current=state, price=65.0) != INVALIDATED


def test_expiry_takes_precedence_over_holding():
    transition = resolve_watch_transition(
        previous=_watch_state(), current=_watch_state(), price=65.9, expired=True
    )
    assert transition == EXPIRED


def test_re_arm_requires_closed_bar_inside_the_zone():
    state = _watch_state()
    # Inside the zone but the bar has not closed.
    assert resolve_watch_transition(
        previous=state, current=state, price=64.0, bar_closed=False
    ) == HELD
    # Closed bar inside the zone re-arms.
    assert resolve_watch_transition(
        previous=state, current=state, price=64.0, bar_closed=True
    ) == RE_ARMED
    # Closed bar outside the zone does not.
    assert resolve_watch_transition(
        previous=state, current=state, price=65.9, bar_closed=True
    ) == HELD


def test_log_record_carries_price_atr_rr_and_reason():
    record = build_watch_log_record(
        transition=ARMED,
        watch_state=_watch_state(),
        price=65.92,
        atr=0.947599,
        symbol="XAG/USD",
    )
    assert record["transition"] == ARMED
    assert record["price"] == 65.92
    assert record["atr"] == 0.947599
    assert record["rrAtTransition"]["tp1"] == 3.34
    assert record["rrAtTransition"]["blended"] == 3.885
    assert record["reason"] == "direction valid but entry poor"
    assert record["loggedAt"].endswith("+00:00")


def test_log_record_never_authorises_execution():
    record = build_watch_log_record(
        transition=RE_ARMED, watch_state=_watch_state(), price=64.0
    )
    assert record["executionPermitted"] is False
    assert record["aiGated"] is False


def test_shadow_only_watch_is_marked():
    """A WATCH from a DEMO_UNVALIDATED artifact stays SHADOW."""
    record = build_watch_log_record(
        transition=ARMED, watch_state=_watch_state(shadow_only=True)
    )
    assert record["shadowOnly"] is True


def test_unknown_transition_is_rejected():
    assert log_watch_transition(transition="teleported", watch_state=_watch_state()) is None


def test_all_documented_transitions_are_loggable():
    for transition in (ARMED, RE_ARMED, EXPIRED, INVALIDATED, PROMOTED):
        record = log_watch_transition(
            transition=transition, watch_state=_watch_state(), price=64.0, atr=0.95
        )
        assert record is not None
        assert record["transition"] == transition
