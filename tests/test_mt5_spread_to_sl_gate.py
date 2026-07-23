"""Spread-to-stop-distance gate (SPREAD_TOO_WIDE_FOR_SL) unit tests."""

from __future__ import annotations

from types import SimpleNamespace

import mt5_executor


def test_ratio_cap_disabled_when_missing_or_nonpositive(monkeypatch):
    monkeypatch.delitem(
        mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", raising=False
    )
    assert mt5_executor._mt5_max_spread_to_sl_ratio() is None

    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", 0)
    assert mt5_executor._mt5_max_spread_to_sl_ratio() is None

    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", None)
    assert mt5_executor._mt5_max_spread_to_sl_ratio() is None

    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", 0.15)
    assert mt5_executor._mt5_max_spread_to_sl_ratio() == 0.15


def test_wide_spread_tight_stop_blocks():
    # ATFX-like AUDCHF: 3-pip spread, 15-pip stop -> ratio 0.20 > 0.15 cap
    tick = SimpleNamespace(ask=0.57110, bid=0.57080)
    exceeded, ratio = mt5_executor._mt5_spread_to_sl_exceeded(
        tick, price=0.57110, sl=0.56960, ratio_cap=0.15
    )
    assert exceeded is True
    assert ratio is not None
    assert abs(ratio - 0.20) < 1e-6


def test_tight_spread_or_wide_stop_passes():
    # Same spread but a 60-pip stop -> ratio 0.05 passes
    tick = SimpleNamespace(ask=0.57110, bid=0.57080)
    exceeded, ratio = mt5_executor._mt5_spread_to_sl_exceeded(
        tick, price=0.57110, sl=0.56510, ratio_cap=0.15
    )
    assert exceeded is False
    assert ratio is not None
    assert abs(ratio - 0.05) < 1e-6

    # Pepperstone-like 0.2-pip spread, 15-pip stop -> ratio ~0.013 passes
    tick = SimpleNamespace(ask=0.57082, bid=0.57080)
    exceeded, ratio = mt5_executor._mt5_spread_to_sl_exceeded(
        tick, price=0.57082, sl=0.56932, ratio_cap=0.15
    )
    assert exceeded is False
    assert ratio is not None
    assert ratio < 0.02


def test_short_direction_uses_absolute_distance():
    # SHORT: fill at bid, SL above entry — |price - sl| basis is direction-agnostic
    tick = SimpleNamespace(ask=0.57110, bid=0.57080)
    exceeded, ratio = mt5_executor._mt5_spread_to_sl_exceeded(
        tick, price=0.57080, sl=0.57230, ratio_cap=0.15
    )
    assert exceeded is True
    assert ratio is not None
    assert abs(ratio - 0.20) < 1e-6


def test_malformed_inputs_fail_open_to_other_gates():
    good_tick = SimpleNamespace(ask=0.57110, bid=0.57080)
    # Missing/zero SL: SL validation and MAX_SL_PCT gates own that case
    assert mt5_executor._mt5_spread_to_sl_exceeded(
        good_tick, price=0.57110, sl=0.0, ratio_cap=0.15
    ) == (False, None)
    # Broken tick: plain spread cap owns that case
    assert mt5_executor._mt5_spread_to_sl_exceeded(
        SimpleNamespace(ask=0, bid=0), price=0.57110, sl=0.56960, ratio_cap=0.15
    ) == (False, None)
    assert mt5_executor._mt5_spread_to_sl_exceeded(
        SimpleNamespace(ask=1.0, bid=1.1), price=1.05, sl=1.0, ratio_cap=0.15
    ) == (False, None)
    # SL equal to price (zero distance)
    assert mt5_executor._mt5_spread_to_sl_exceeded(
        good_tick, price=0.57110, sl=0.57110, ratio_cap=0.15
    ) == (False, None)
