"""Engine B trigger lookback window + D1 order-block mitigation.

Covers the 2026-08-21 audit fixes:
- _price_action_trigger scans a bounded newest->oldest window instead of only
  the last confirmed bar (top funnel blocker: raw_trigger_missing).
- _detect_order_blocks marks a block mitigated once price closes through its
  far side, so stale D1 blocks stop donating hierarchy bias evidence.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from market_structure import NakedEngine


def _candle(o, h, l, c, t=None, vol=0.0):
    return {"open": o, "high": h, "low": l, "close": c, "vol": vol, "time": t}


def _engulf_series():
    """7 bars; the ONLY aligned LONG trigger is the engulfing at age 2."""
    return [
        _candle(100.0, 100.1, 99.9, 100.0, "2026-08-21T00:00:00+00:00"),
        _candle(100.0, 100.1, 99.9, 100.0, "2026-08-21T00:15:00+00:00"),
        _candle(100.0, 100.1, 99.9, 100.0, "2026-08-21T00:30:00+00:00"),
        # age 3: bearish lead-in (open 100.5 / close 99.8)
        _candle(100.5, 100.6, 99.7, 99.8, "2026-08-21T00:45:00+00:00"),
        # age 2: bullish engulfing (body 0.9 >= 0.2 ATR)
        _candle(99.7, 100.65, 99.6, 100.6, "2026-08-21T01:00:00+00:00"),
        # age 1: doji — no pattern either direction
        _candle(100.4, 100.5, 100.3, 100.4, "2026-08-21T01:15:00+00:00"),
        # age 0: neutral two-sided bar — no pattern either direction
        # (small body/wicks defeat rejection; close stays inside the doji
        # range so no inside-break; flat direction defeats strong-close).
        _candle(100.40, 100.44, 100.26, 100.31, "2026-08-21T01:30:00+00:00"),
    ]


def test_newest_bar_trigger_reports_bar_age_zero():
    engine = NakedEngine()
    series = _engulf_series()
    # Make the NEWEST bar an engulfing too: mirror it upward.
    series[-1] = _candle(100.2, 100.9, 100.1, 100.8, "2026-08-21T01:15:00+00:00")
    ctx = engine._price_action_trigger(series, "LONG", 1.0, True, True)
    assert ctx["trigger_ok"] is True
    assert ctx["bar_age"] == 0
    assert ctx["trigger_bar_time"] == "2026-08-21T01:15:00+00:00"


def test_trigger_armed_two_bars_ago_is_recovered():
    engine = NakedEngine()
    ctx = engine._price_action_trigger(_engulf_series(), "LONG", 1.0, True, True)
    assert ctx["trigger_ok"] is True
    assert ctx["pattern"] == "ENGULFING"
    assert ctx["bar_age"] == 2
    assert ctx["trigger_bar_time"] == "2026-08-21T01:00:00+00:00"


def test_trigger_lookback_disabled_restores_last_bar_only():
    engine = NakedEngine()
    saved = config.CONFIG.get("ENGINE_B_TRIGGER_LOOKBACK_ENABLED")
    config.CONFIG["ENGINE_B_TRIGGER_LOOKBACK_ENABLED"] = False
    try:
        ctx = engine._price_action_trigger(_engulf_series(), "LONG", 1.0, True, True)
    finally:
        if saved is None:
            config.CONFIG.pop("ENGINE_B_TRIGGER_LOOKBACK_ENABLED", None)
        else:
            config.CONFIG["ENGINE_B_TRIGGER_LOOKBACK_ENABLED"] = saved
    assert ctx["trigger_ok"] is False
    assert ctx["bar_age"] is None


def test_trigger_lookback_window_bound_is_honoured():
    engine = NakedEngine()
    saved = config.CONFIG.get("ENGINE_B_TRIGGER_LOOKBACK_BARS")
    config.CONFIG["ENGINE_B_TRIGGER_LOOKBACK_BARS"] = 2  # ages {1} searched
    try:
        ctx = engine._price_action_trigger(_engulf_series(), "LONG", 1.0, True, True)
    finally:
        if saved is None:
            config.CONFIG.pop("ENGINE_B_TRIGGER_LOOKBACK_BARS", None)
        else:
            config.CONFIG["ENGINE_B_TRIGGER_LOOKBACK_BARS"] = saved
    assert ctx["trigger_ok"] is False


def test_opposing_direction_finds_nothing_on_same_series():
    engine = NakedEngine()
    # zone_hit/bos off so the pullback bar cannot qualify as a SHORT
    # strong-close; the series then carries no aligned SHORT pattern at all.
    ctx = engine._price_action_trigger(_engulf_series(), "SHORT", 1.0, False, False)
    assert ctx["trigger_ok"] is False
    assert ctx["bar_age"] is None


# ── D1 order-block mitigation ────────────────────────────────────────────────


def _ob_series(last_close):
    """Bullish BOS at index 8; OB = bearish candle at 7 (100.8/100.2)."""
    candles = [_candle(100.0, 100.2, 99.8, 100.0) for _ in range(7)]
    candles.append(_candle(100.8, 100.9, 100.1, 100.2))   # idx 7: bearish OB
    candles.append(_candle(100.3, 101.6, 100.1, 101.5))   # idx 8: BOS close
    candles.append(_candle(101.5, 101.7, last_close - 0.05, last_close))
    return candles


def _ob_bos_data():
    return {
        "bos_bull": True,
        "bos_bear": False,
        "last_broken_high": 101.0,
        "bos_bull_bar_index": 8,
    }


def test_order_block_mitigated_when_closed_through_far_side():
    engine = NakedEngine()
    obs = engine._detect_order_blocks(
        _ob_series(last_close=100.0), _ob_bos_data(), 1.0, structure_tf="D1"
    )
    assert len(obs) == 1
    assert obs[0]["type"] == "bullish"
    assert obs[0]["mitigated"] is True


def test_order_block_not_mitigated_while_intact():
    engine = NakedEngine()
    obs = engine._detect_order_blocks(
        _ob_series(last_close=100.5), _ob_bos_data(), 1.0, structure_tf="D1"
    )
    assert len(obs) == 1
    assert obs[0]["mitigated"] is False
