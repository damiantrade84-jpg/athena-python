"""Tests for athena_ai.price_action_facts.

Every emitted fact must carry a raw value and a confidence label. Bare
categorical strings are a regression (FIX 3 in the spec).
"""

from __future__ import annotations

from typing import Any

from athena_ai.price_action_facts import (
    derive_acceptance_state,
    derive_breakout_state,
    derive_liquidity_event,
    derive_price_action_facts,
    derive_profile_location,
    derive_setup_candidates,
    derive_volume_behavior,
    derive_wick_event,
)


def _bar(o: float, h: float, l: float, c: float, v: float = 1000.0, t: int = 0) -> dict:
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "time": t}


def _is_self_describing(fact: dict[str, Any]) -> bool:
    """Self-describing = has a confidence AND at least one non-state numeric/raw key."""
    if not isinstance(fact, dict):
        return False
    if "confidence" not in fact:
        return False
    # must carry SOMETHING beyond the categorical label + confidence
    raw_keys = [k for k in fact.keys() if k not in ("confidence", "state", "type", "event", "location", "reason")]
    return len(raw_keys) > 0 or fact.get("state") == "unknown"


# ----- wick_event -------------------------------------------------------------


def test_wick_event_upper_rejection_with_atr() -> None:
    # last candle: small body near low, long upper wick
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    bars.append(_bar(100.0, 105.0, 99.8, 100.2))
    fact = derive_wick_event(bars, atr=2.0)
    assert fact["type"] == "upper_rejection"
    assert fact["upper_wick_atr"] is not None and fact["upper_wick_atr"] >= 1.0
    assert fact["confidence"] in ("medium", "high")
    assert _is_self_describing(fact)


def test_wick_event_unknown_without_candles() -> None:
    fact = derive_wick_event([], atr=1.0)
    assert fact["state"] == "unknown"
    assert fact["confidence"] == "low"


# ----- breakout_state ---------------------------------------------------------


def test_failed_breakout_upper() -> None:
    # close back inside after piercing level=100
    bars = [
        _bar(99.0, 99.5, 98.5, 99.2),
        _bar(99.2, 102.0, 99.0, 99.5),  # wick above 100, close back below
    ]
    fact = derive_breakout_state(bars, level=100.0, direction="LONG")
    assert fact["state"] == "failed_breakout"
    assert fact["side"] == "upper"
    assert fact["level"] == 100.0
    assert fact["close_back_inside"] is True
    assert fact["confidence"] == "high"


def test_breakout_holding_upper() -> None:
    bars = [
        _bar(99.0, 99.5, 98.5, 99.2),
        _bar(99.2, 102.0, 99.2, 101.5),  # closed above
    ]
    fact = derive_breakout_state(bars, level=100.0, direction="LONG")
    assert fact["state"] == "breakout_holding"
    assert fact["side"] == "upper"


def test_breakout_unknown_without_level() -> None:
    bars = [_bar(100, 101, 99, 100) for _ in range(3)]
    fact = derive_breakout_state(bars, level=None)
    assert fact["state"] == "unknown"


# ----- acceptance -------------------------------------------------------------


def test_acceptance_above() -> None:
    bars = [_bar(100, 102, 99.5, 101.0), _bar(101, 103, 100.5, 102.0), _bar(102, 104, 101.5, 103.0)]
    fact = derive_acceptance_state(bars, level=100.0, min_bars=3)
    assert fact["state"] == "accepted_above"
    assert fact["bars"] == 3


def test_acceptance_no_match() -> None:
    bars = [_bar(100, 102, 99.5, 99.0), _bar(99, 103, 98.5, 101.0), _bar(101, 104, 99.5, 99.5)]
    fact = derive_acceptance_state(bars, level=100.0, min_bars=3)
    assert fact["state"] == "no_acceptance"


# ----- liquidity --------------------------------------------------------------


def test_liquidity_sweep_upper_with_named_pool() -> None:
    bars = [_bar(100, 105.0, 99.0, 100.5)]  # wick above 104, close back below
    fact = derive_liquidity_event(bars, pool_levels=[104.0], atr=1.0)
    assert fact["event"] == "upper_sweep"
    assert fact["pool_level"] == 104.0
    assert fact["confidence"] == "high"


def test_liquidity_unknown_without_named_pool() -> None:
    bars = [_bar(100, 105.0, 99.0, 100.5)]
    fact = derive_liquidity_event(bars, pool_levels=None, atr=1.0)
    assert fact["state"] == "unknown"
    assert "no_named_pool" in fact.get("reason", "")


# ----- profile location -------------------------------------------------------


def test_profile_location_near_poc() -> None:
    fact = derive_profile_location(last_close=100.05, poc=100.0, vah=101.0, val=99.0, atr=1.0)
    assert fact["location"] == "near_poc"
    assert fact["distance_to_poc_atr"] is not None and fact["distance_to_poc_atr"] <= 0.25


def test_profile_location_above_vah() -> None:
    fact = derive_profile_location(last_close=102.0, poc=100.0, vah=101.0, val=99.0, atr=1.0)
    assert fact["location"] == "above_vah"


def test_profile_location_unknown_without_poc() -> None:
    fact = derive_profile_location(last_close=100.0, poc=None, vah=101.0, val=99.0, atr=1.0)
    assert fact["state"] == "unknown"


# ----- volume -----------------------------------------------------------------


def test_volume_expansion() -> None:
    bars = [_bar(100, 101, 99, 100, v=100.0) for _ in range(5)]
    bars.append(_bar(100, 102, 99, 101, v=400.0))
    fact = derive_volume_behavior(bars)
    assert fact["state"] == "expansion"
    assert fact["ratio"] >= 1.75


def test_volume_unknown_with_few_bars() -> None:
    fact = derive_volume_behavior([_bar(100, 101, 99, 100, v=100)])
    assert fact["state"] == "unknown"


# ----- setup_candidates -------------------------------------------------------


def test_setup_candidates_failed_breakout_hint() -> None:
    fact = derive_setup_candidates(
        wick_event={"type": "neutral", "confidence": "low"},
        breakout_state={"state": "failed_breakout", "confidence": "high"},
        liquidity_event={"event": "no_sweep", "confidence": "medium"},
        profile_location={"location": "inside_va", "confidence": "medium"},
    )
    assert "failed_breakout_hint" in fact["hints"]
    assert "breakout_continuation_hint" not in fact["hints"]


# ----- top-level orchestrator -------------------------------------------------


def test_orchestrator_always_returns_all_keys_with_confidence() -> None:
    facts = derive_price_action_facts(
        engine_a_ctx={"atr": {"atr_chart_tf": 1.0}, "structure_context": {}, "ema_levels": {}},
        ohlcv_window=None,
        direction="LONG",
        named_levels=None,
        liquidity_pools=None,
    )
    required = {
        "wick_event",
        "breakout_state",
        "acceptance_state",
        "liquidity_event",
        "structure_event",
        "profile_location",
        "volume_behavior",
        "setup_candidates",
    }
    assert required.issubset(set(facts.keys()))
    for key in required:
        fact = facts[key]
        assert isinstance(fact, dict)
        assert "confidence" in fact, f"{key} missing confidence (FIX 3)"


def test_no_bare_categorical_labels_for_known_inputs() -> None:
    """FIX 3 — when inputs ARE present, facts must still carry raw values, not bare labels."""
    bars = [_bar(99, 99.5, 98, 99.2)] * 5 + [_bar(99, 105, 98.5, 99.3, v=400.0)]
    facts = derive_price_action_facts(
        engine_a_ctx={
            "atr": {"atr_chart_tf": 1.5},
            "structure_context": {"profile": {"poc": 99.0, "vah": 100.0, "val": 98.0}},
            "ema_levels": {"ema50": 99.5},
        },
        ohlcv_window=bars,
        direction="LONG",
        liquidity_pools=[104.0],
    )
    wick = facts["wick_event"]
    assert wick.get("upper_wick_atr") is not None or wick.get("state") == "unknown"
    assert "confidence" in wick

    liq = facts["liquidity_event"]
    assert liq.get("event") in ("upper_sweep", "lower_sweep", "no_sweep")
    # raw measurements present, not just a label
    raw_keys = set(liq.keys()) - {"event", "confidence"}
    assert raw_keys, "liquidity_event must carry raw values, not just a label"
