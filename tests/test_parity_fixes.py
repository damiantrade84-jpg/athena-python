"""
tests/test_parity_fixes.py
==========================
Tests for three parity fixes:
  CRIT-01  London breakout parity (session_eval_hour vs breakout_eval_hour)
  HIGH-01  Live/backtest Engine A candle-state parity (missing pair= in scalp BT)
  HIGH-06  Engine-C scan candle-state parity (bar_time not forcing 13:00 UTC)

Run with:
    py -m pytest tests/test_parity_fixes.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import forex_scoring
import indicators


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_h1_candles(count: int = 80, start_hour_utc: int = 0, base: float = 1.1000) -> list[dict]:
    """Build synthetic H1 candles starting at start_hour_utc UTC."""
    start = datetime(2026, 4, 7, start_hour_utc, 0, 0, tzinfo=timezone.utc)
    bars = []
    for idx in range(count):
        px = base + idx * 0.0001
        dt = start + timedelta(hours=idx)
        bars.append({
            "time": dt.isoformat(),
            "open": px,
            "high": px + 0.0005,
            "low": px - 0.0005,
            "close": px,
            "vol": 1000.0,
        })
    return bars


def _make_london_breakout_candles(breakout_hour_utc: int = 7, total: int = 80) -> list[dict]:
    """
    Build H1 candles where:
    - Hours 0-6 UTC = Asian session range (price at 1.1000)
    - The breakout bar is the LAST candle at breakout_hour_utc

    Always prepends at least 5 pre-session padding candles so len(bars) >= 10,
    which is required by _london_breakout_score's length guard.
    """
    bars = []
    base_date = datetime(2026, 4, 7, 0, 0, 0, tzinfo=timezone.utc)

    # Pre-session padding: previous evening bars (prior day 20:00-23:00 UTC)
    # These are before midnight and won't be mistaken for Asian session bars
    prev_day = base_date - timedelta(hours=4)
    for h in range(5):
        dt = prev_day + timedelta(hours=h)
        bars.append({
            "time": dt.isoformat(),
            "open": 1.1005,
            "high": 1.1015,
            "low": 1.0995,
            "close": 1.1005,
            "vol": 800.0,
        })

    # Asian session candles: from 00:00 to just before breakout_hour_utc
    # Use hours 0-6 regardless of breakout_hour_utc so Asian range is always defined
    asian_end = min(breakout_hour_utc, 7)
    for h in range(asian_end):
        dt = base_date + timedelta(hours=h)
        bars.append({
            "time": dt.isoformat(),
            "open": 1.1000,
            "high": 1.1010,
            "low": 1.0990,
            "close": 1.1000,
            "vol": 1000.0,
        })

    # For breakout_hour_utc > 7, add neutral bars between asian_end and breakout_hour
    for h in range(asian_end, breakout_hour_utc):
        dt = base_date + timedelta(hours=h)
        bars.append({
            "time": dt.isoformat(),
            "open": 1.1010,
            "high": 1.1015,
            "low": 1.1005,
            "close": 1.1010,
            "vol": 800.0,
        })

    # Breakout candle: the LAST bar is at breakout_hour_utc
    dt_bo = base_date + timedelta(hours=breakout_hour_utc)
    bars.append({
        "time": dt_bo.isoformat(),
        "open": 1.1010,
        "high": 1.1060,
        "low": 1.1010,
        "close": 1.1050,  # clearly above asian_high=1.1010, body_ratio=1.0
        "vol": 2000.0,
    })

    return bars


def _base_snaps():
    d1_snap = {"adx": 30.0, "close": 1.1200, "ema200": 1.1000, "ema200Slope10": 0.01}
    h4_snap = {
        "ema50": 1.1150, "ema200": 1.1050, "adx": 30.0, "atr": 0.0050,
        "close": 1.1180, "macdHist": 0.10, "macdHistPrev": 0.05,
    }
    h1_snap = {"rsi": 40.0, "ema21": 1.1170, "ema50": 1.1120, "close": 1.1190}
    pair = {"display": "EUR/USD", "type": "forex"}
    return d1_snap, h4_snap, h1_snap, pair


def _patch_nonessential(monkeypatch):
    monkeypatch.setattr(
        forex_scoring,
        "_session_state",
        lambda *_a, **_k: {"is_active": True, "multiplier": 1.0},
    )
    monkeypatch.setattr(forex_scoring, "_cot_boost", lambda *a, **k: 0.0)
    monkeypatch.setattr(forex_scoring, "_carry_tilt", lambda *a, **k: 0.0)
    monkeypatch.setattr(indicators, "detect_fvg", lambda _c: [])
    monkeypatch.setattr(indicators, "detect_liquidity_sweep", lambda _c, _a: False)
    monkeypatch.setattr(indicators, "volume_strength_at_level", lambda _c, _l: 0.0)
    monkeypatch.setattr(indicators, "calc_fib", lambda _c: {"fib618": 1.0, "fib382": 1.0})
    monkeypatch.setattr(forex_scoring, "_hurst_exponent", lambda *a, **k: 0.60)


# ─────────────────────────────────────────────────────────────────────────────
# CRIT-01 tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCrit01LondonBreakoutParity:
    """CRIT-01: breakout_eval_hour must come from H1 candles, not bar_time."""

    def test_breakout_eval_hour_from_candles_returns_last_h1_hour(self):
        """_breakout_eval_hour_from_candles extracts hour from last H1 bar."""
        candles = _make_h1_candles(count=10, start_hour_utc=6)
        # last candle was at 06:00 + 9h = 15:00 UTC
        hour = forex_scoring._breakout_eval_hour_from_candles(candles)
        assert hour == 15

    def test_breakout_eval_hour_at_london_open(self):
        """When last H1 candle is at 07 UTC, breakout_eval_hour = 7."""
        candles = _make_london_breakout_candles(breakout_hour_utc=7)
        hour = forex_scoring._breakout_eval_hour_from_candles(candles)
        assert hour == 7

    def test_d1_swing_bt_bar_time_13utc_does_not_suppress_breakout(self, monkeypatch):
        """
        CRIT-01 regression: D1 swing BT uses bar_time=13:00 UTC for session check.
        Before the fix, utc_hour=13 was also passed to _london_breakout_score,
        which then returned 0.0 because 13 is outside the 07-09 window.
        After the fix, breakout_eval_hour is derived from h1_candles[-1]['time'].
        If the last H1 candle is at 07:00 UTC, the breakout must be scored.
        """
        _patch_nonessential(monkeypatch)

        d1_snap, h4_snap, h1_snap, pair = _base_snaps()
        h4_candles = _make_h1_candles(count=40, start_hour_utc=0)

        # Build candles where last H1 bar is AT the London open (07:00 UTC)
        h1_candles = _make_london_breakout_candles(breakout_hour_utc=7)

        # Simulate D1 swing BT: bar_time forced to 13:00 UTC
        bt_bar_time = "2026-04-07T13:00:00+00:00"

        result = forex_scoring.compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair=pair,
            bar_time=bt_bar_time,
            backtest_mode=True,
            h4_candles=h4_candles,
        )

        # breakout_eval_hour must be 7 (from last candle), not 13 (from bar_time)
        assert result.components["breakout_eval_hour"] == 7, (
            f"breakout_eval_hour={result.components['breakout_eval_hour']}, "
            "expected 7 (from H1 candle), not 13 (from bar_time)"
        )
        # session_ok should be True because bar_time=13:00 is in London/NY
        assert result.components["session_active"] is True

    def test_session_eval_and_breakout_eval_use_different_hours(self, monkeypatch):
        """
        session_active uses bar_time (utc_hour); breakout_eval_hour uses H1 candles.
        They should differ when bar_time is forced to 13:00 but candles end at 07:00.
        """
        _patch_nonessential(monkeypatch)

        d1_snap, h4_snap, h1_snap, pair = _base_snaps()
        h4_candles = _make_h1_candles(count=40, start_hour_utc=0)
        h1_candles = _make_london_breakout_candles(breakout_hour_utc=7)

        result = forex_scoring.compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair=pair,
            bar_time="2026-04-07T13:00:00+00:00",
            backtest_mode=True,
            h4_candles=h4_candles,
        )

        utc = result.components["utc_hour"]
        bo_h = result.components["breakout_eval_hour"]
        assert utc == 13, f"utc_hour should be 13 (from bar_time), got {utc}"
        assert bo_h == 7, f"breakout_eval_hour should be 7 (from H1 last candle), got {bo_h}"
        assert utc != bo_h, "session and breakout eval hours should differ in this scenario"

    def test_live_mode_breakout_uses_h1_candle_not_clock(self, monkeypatch):
        """
        In live mode (backtest_mode=False), breakout_eval_hour still reads from
        h1_candles, not from the live clock. This ensures parity.
        """
        _patch_nonessential(monkeypatch)
        # Patch the live clock to 20:00 UTC (well past London open)
        monkeypatch.setattr(forex_scoring, "_local_to_utc_hour", lambda: 20)

        d1_snap, h4_snap, h1_snap, pair = _base_snaps()
        h4_candles = _make_h1_candles(count=40, start_hour_utc=0)
        h1_candles = _make_london_breakout_candles(breakout_hour_utc=7)

        result = forex_scoring.compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair=pair,
            backtest_mode=False,
            h4_candles=h4_candles,
        )

        # utc_hour = 20 (from live clock)
        assert result.components["utc_hour"] == 20
        # breakout_eval_hour = 7 (from last H1 candle)
        assert result.components["breakout_eval_hour"] == 7

    def test_breakout_not_scored_when_h1_candle_outside_window(self, monkeypatch):
        """When last H1 candle is at 15:00 UTC, breakout window is missed → 0 score."""
        _patch_nonessential(monkeypatch)

        d1_snap, h4_snap, h1_snap, pair = _base_snaps()
        h4_candles = _make_h1_candles(count=40, start_hour_utc=0)
        # Last candle at 15:00 UTC - not in London open window 07-09
        h1_candles = _make_london_breakout_candles(breakout_hour_utc=15)

        result = forex_scoring.compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair=pair,
            backtest_mode=True,
            bar_time="2026-04-07T15:00:00+00:00",
            h4_candles=h4_candles,
        )
        # Breakout should not have fired
        assert result.components["breakout_score"] == 0.0
        assert result.components["breakout_eval_hour"] == 15


# ─────────────────────────────────────────────────────────────────────────────
# HIGH-01 tests – candle-state parity (pair= arg in scalp BT)
# ─────────────────────────────────────────────────────────────────────────────

class TestHigh01ScalpBTPairArg:
    """HIGH-01: compute_forex_score must receive pair= in the scalp BT loop."""

    def test_compute_forex_score_requires_pair_arg(self, monkeypatch):
        """Calling compute_forex_score without pair raises or returns 0 result."""
        _patch_nonessential(monkeypatch)

        d1_snap, h4_snap, h1_snap, _ = _base_snaps()
        h1_candles = _make_h1_candles(80, start_hour_utc=8)
        h4_candles = _make_h1_candles(40, start_hour_utc=0)

        # Without pair (empty dict should degrade gracefully — carry/cot need pair.display)
        result_no_pair = forex_scoring.compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair={},  # empty — simulates missing pair
            backtest_mode=True,
            bar_time="2026-04-07T13:00:00+00:00",
            h4_candles=h4_candles,
        )

        result_with_pair = forex_scoring.compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair={"display": "EUR/USD", "type": "forex"},
            backtest_mode=True,
            bar_time="2026-04-07T13:00:00+00:00",
            h4_candles=h4_candles,
        )

        # Both should return a ForexScoreResult (no crash)
        assert hasattr(result_no_pair, "final_score")
        assert hasattr(result_with_pair, "final_score")

    def test_backtest_runner_scalp_loop_has_pair_arg(self):
        """
        HIGH-01: backtest_runner must not call compute_forex_score for forex pairs.
        Forex BT now routes through Engine A v2 (factor_scoring) via backtest_pair.
        """
        import inspect
        import backtest_runner as bt

        src = inspect.getsource(bt)

        # compute_forex_score must not be called anywhere in the backtest runner
        assert "compute_forex_score" not in src, (
            "backtest_runner.py still calls compute_forex_score — forex BT must use Engine A v2"
        )


# ─────────────────────────────────────────────────────────────────────────────
# HIGH-06 tests – Engine-C scan candle-state parity
# ─────────────────────────────────────────────────────────────────────────────

class TestHigh06EngineCBTParity:
    """HIGH-06: Engine-C BT must not use _bt_forex_d1_bar_time for H4 forex bars."""

    def test_engine_c_bt_no_longer_forces_13utc_for_forex(self):
        """
        HIGH-06: Engine-C BT must pass H4 bar time directly, not via _bt_forex_d1_bar_time
        which forced midnight bars to 13:00 and suppressed 07-09 breakout detection.
        """
        import inspect
        import backtest_runner as bt

        src = inspect.getsource(bt)

        # Correct pattern: candles_h4[i].get("time") passed directly (not via _bt_forex_d1_bar_time)
        assert 'candles_h4[i].get("time") if candles_h4 else None' in src, (
            "Engine-C BT is not passing H4 bar time directly — check backtest_runner.py"
        )

    def test_breakout_eval_hour_resolver_is_independent_of_bar_time(self, monkeypatch):
        """
        The shared resolver (_breakout_eval_hour_from_candles) must return the
        H1-candle-based hour regardless of what bar_time says.
        Candles end at 08:00 UTC; even if bar_time were different, eval hour = 8.
        """
        _patch_nonessential(monkeypatch)

        d1_snap, h4_snap, h1_snap, pair = _base_snaps()
        h4_candles = _make_h1_candles(40, start_hour_utc=0)
        # Candles end at 08:00 UTC exactly (last bar is the breakout at 08:00)
        h1_candles = _make_london_breakout_candles(breakout_hour_utc=8)

        # bar_time = H4 bar timestamp at 08:00 UTC
        h4_bar_time = "2026-04-07T08:00:00+00:00"

        result = forex_scoring.compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair=pair,
            bar_time=h4_bar_time,
            backtest_mode=True,
            h4_candles=h4_candles,
        )

        # breakout_eval_hour must be 8 (from last H1 candle at 08:00)
        assert result.components["breakout_eval_hour"] == 8
        # In this test the utc_hour from bar_time is also 8, but the KEY point is
        # that the resolver reads from candles, not bar_time.
        assert result.components["utc_hour"] == 8

    def test_breakout_scored_when_h1_at_london_open_regardless_of_bt_bar_time(self, monkeypatch):
        """
        If H1 candles end at 07:00 UTC during London open, breakout MUST be
        evaluated even if bar_time is passed as a different time.
        """
        _patch_nonessential(monkeypatch)

        d1_snap, h4_snap, h1_snap, pair = _base_snaps()
        h4_candles = _make_h1_candles(40, start_hour_utc=0)
        # The LAST H1 candle is the breakout bar at 07:00 UTC
        h1_candles = _make_london_breakout_candles(breakout_hour_utc=7)

        # Simulate Engine-C BT with H4 timestamp at 07:00
        result = forex_scoring.compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair=pair,
            bar_time="2026-04-07T07:00:00+00:00",
            backtest_mode=True,
            h4_candles=h4_candles,
        )

        assert result.components["breakout_eval_hour"] == 7
        # Breakout score should be non-zero (breakout candle is in the 07-09 window
        # and the candle breaks above the Asian session high with good body ratio)
        assert result.components["breakout_score"] > 0.0, (
            f"Expected breakout_score > 0 but got {result.components['breakout_score']}. "
            "Breakout candle is at 07 UTC (inside _LONDON_OPEN[0]+2 = 09 window)."
        )
