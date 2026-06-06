"""Tests for crypto/Bybit TP absolute floor and max-distance bounds."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk_engine import validate_tp_exchange_bounds

_CFG = {
    "BYBIT_MIN_TP_PCT_OF_MARK": 0.10,
    "MAX_TP_PCT": {"crypto": 0.50, "default": 0.50},
}


def test_dot_short_tp_below_bybit_floor_rejected():
    ok, err = validate_tp_exchange_bounds(0.929, 0.037, "crypto", _CFG)
    assert ok is False
    assert err is not None
    assert "TP_BELOW_EXCHANGE_FLOOR" in err


def test_short_tp_within_bounds_accepted():
    ok, err = validate_tp_exchange_bounds(0.929, 0.85, "crypto", _CFG)
    assert ok is True
    assert err is None


def test_tp_beyond_max_distance_rejected():
    ok, err = validate_tp_exchange_bounds(0.929, 0.40, "crypto", _CFG)
    assert ok is False
    assert err is not None
    assert "TP_TOO_FAR" in err
