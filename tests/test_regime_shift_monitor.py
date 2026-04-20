"""Smoke tests for regime_shift_monitor — pure-function classifier only."""
from regime_shift_monitor import (
    _detect_choch_simple,
    _fib_zone_status,
    classify_position,
)


def _candle(o, h, l, c, v=100):
    return {"open": o, "high": h, "low": l, "close": c, "vol": v}


def test_choch_long_detects_break_below_swing_low():
    # Swing low at bar 1 (low=1.00), then an uptrend, then a final bar closing below 1.00.
    candles = [
        _candle(1.10, 1.15, 1.08, 1.12),   # 0
        _candle(1.12, 1.20, 1.00, 1.18),   # 1 — swing low (1.00 < 1.08 and 1.00 < 1.10)
        _candle(1.18, 1.22, 1.10, 1.20),   # 2
        _candle(1.20, 1.25, 1.15, 1.22),   # 3
        _candle(1.22, 1.28, 1.18, 1.25),   # 4
        _candle(1.25, 1.30, 1.20, 1.28),   # 5
        _candle(1.28, 1.31, 1.22, 1.29),   # 6
        _candle(1.29, 1.32, 1.24, 1.30),   # 7
        _candle(1.30, 1.31, 1.25, 1.26),   # 8
        _candle(1.26, 1.27, 1.15, 0.95),   # 9 — last close 0.95 < swing low 1.00
    ]
    assert _detect_choch_simple(candles, "LONG", 20) is True


def test_choch_short_detects_break_above_swing_high():
    # Swing high at bar 1 (high=1.40), then a downtrend, then a final bar closing above 1.40.
    candles = [
        _candle(1.30, 1.32, 1.28, 1.29),   # 0
        _candle(1.29, 1.40, 1.22, 1.28),   # 1 — swing high (1.40 > 1.32 and 1.40 > 1.30)
        _candle(1.28, 1.30, 1.20, 1.25),   # 2
        _candle(1.25, 1.26, 1.18, 1.22),   # 3
        _candle(1.22, 1.24, 1.15, 1.20),   # 4
        _candle(1.20, 1.22, 1.14, 1.18),   # 5
        _candle(1.18, 1.20, 1.10, 1.15),   # 6
        _candle(1.15, 1.18, 1.08, 1.12),   # 7
        _candle(1.12, 1.16, 1.05, 1.10),   # 8
        _candle(1.10, 1.50, 1.08, 1.45),   # 9 — last close 1.45 > swing high 1.40
    ]
    assert _detect_choch_simple(candles, "SHORT", 20) is True


def test_fib_zone_inside():
    impulse = (1.0, 1.1)  # LONG: leg_low=1.0, leg_high=1.1
    # price at 1.05 => 50% retrace -> inside (0.382..0.786)
    assert _fib_zone_status(impulse, 1.05, "LONG", None) == "inside"


def test_fib_zone_past_ceiling():
    impulse = (1.0, 1.1)
    # price at 1.01 => 90% retrace -> past_ceiling
    assert _fib_zone_status(impulse, 1.01, "LONG", None) == "past_ceiling"


def test_classify_returns_neutral_on_insufficient_bars():
    def bad_fetch(tf, lim):
        return []

    result = classify_position("TEST/PAIR", "LONG", "engine_a", "intraday", bad_fetch)
    assert result["classification"] == "NEUTRAL"
    assert result["weight"] == 0
    assert "insufficient" in result["advisory"].lower()
