"""Engine A setup trigger lookback window (Engine B parity).

A valid trigger pattern on any of the last N closed bars must fire, not only
the newest one. ENGINE_A_V3_SETUP_LOOKBACK_BARS=1 / ENABLED=false restore the
legacy last-bar-only behaviour exactly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine_a_v3.routing import route_specialist
from engine_a_v3.setups import (
    _breakout_retest_candidate,
    _mean_reversion_candidate,
    _pullback_candidate,
    _setup_trigger_offsets,
)


def _trend_bars(*, count: int = 80, base: float = 2000.0, step: float = 0.08) -> list[dict]:
    start = datetime(2025, 6, 2, 8, 0, tzinfo=timezone.utc)
    rows = []
    for idx in range(count):
        close = base + step * idx
        open_ = close - 0.03
        rows.append(
            {
                "time": (start + timedelta(hours=1) * idx).isoformat(),
                "open": open_,
                "high": close + 0.04,
                "low": open_ - 0.02,
                "close": close,
                "vol": 1000 + idx,
            }
        )
    return rows


def _shift_pattern_back(
    primary: list[dict],
    context: list[dict],
    *,
    bars_back: int,
    ema_period: int = 21,
) -> None:
    """Move the touch/confirm pullback pair `bars_back` bars from the end.

    The pair is rebuilt at [-2-bars_back, -1-bars_back], priced against the
    EMA of the series the age-`bars_back` evaluation will actually see
    (primary[:len-bars_back]). Bars after the pair are tight dojis straddling
    the EMA: touched stays true but confirmation fails, so no fresh pattern
    forms at a newer offset and ATR is not inflated.
    """
    from engine_a_v3.setups import _ema

    anchor = primary[: len(primary) - bars_back]
    ema_now = _ema([float(row["close"]) for row in anchor], ema_period)[-1]
    touch_idx = len(primary) - 2 - bars_back
    confirm_idx = touch_idx + 1
    # Touch bar: modest dip below the EMA with a rejection wick >= 1x body.
    primary[touch_idx].update(
        {
            "open": ema_now + 0.02,
            "high": ema_now + 0.04,
            "low": ema_now - 0.12,
            "close": ema_now - 0.04,
        }
    )
    # Confirm bar: bullish close back above the EMA, close to it.
    primary[confirm_idx].update(
        {
            "open": ema_now - 0.01,
            "high": ema_now + 0.07,
            "low": ema_now - 0.03,
            "close": ema_now + 0.05,
        }
    )
    for idx in range(confirm_idx + 1, len(primary)):
        primary[idx].update(
            {
                "open": ema_now + 0.01,
                "high": ema_now + 0.04,
                "low": ema_now - 0.04,
                "close": ema_now - 0.02,
            }
        )


def test_trigger_offsets_default_window_and_bounds(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_BARS", 3)
    assert _setup_trigger_offsets(100) == (0, 1, 2)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_BARS", 1)
    assert _setup_trigger_offsets(100) == (0,)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", False)
    assert _setup_trigger_offsets(100) == (0,)
    # Short history clamps the window instead of scanning out of range.
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_BARS", 5)
    assert _setup_trigger_offsets(3) == (0, 1)
    assert _setup_trigger_offsets(2) == (0,)


def test_pullback_fires_on_newest_bar_with_age_zero(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_BARS", 3)
    context = _trend_bars()
    primary = [dict(row) for row in context]
    _shift_pattern_back(primary, context, bars_back=0)
    candidate = _pullback_candidate(
        "fx_trend_pullback", primary, context, family="forex"
    )
    assert candidate.decision == "TRADE"
    assert candidate.trigger_bar_age == 0


def test_pullback_recovers_pattern_two_bars_back(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_BARS", 3)
    context = _trend_bars()
    primary = [dict(row) for row in context]
    _shift_pattern_back(primary, context, bars_back=2)
    candidate = _pullback_candidate(
        "fx_trend_pullback", primary, context, family="forex"
    )
    assert candidate.decision == "TRADE"
    assert candidate.trigger_bar_age == 2


def test_pullback_lookback_disabled_restores_legacy(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", False)
    context = _trend_bars()
    primary = [dict(row) for row in context]
    _shift_pattern_back(primary, context, bars_back=2)
    candidate = _pullback_candidate(
        "fx_trend_pullback", primary, context, family="forex"
    )
    assert candidate.decision in {"WATCH", "NO_SIGNAL"}
    assert candidate.trigger_bar_age != 2


def test_mean_reversion_reversal_recovers_one_bar_back(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_BARS", 3)

    # Choppy upward drift: low efficiency (alternating moves) with price
    # stretched well above the EMA mean -> SHORT fade setup.
    start = datetime(2025, 6, 2, 8, 0, tzinfo=timezone.utc)
    rows = []
    close = 100.0
    for idx in range(80):
        delta = 0.36 if idx % 2 == 0 else -0.20
        open_ = close
        close = round(close + delta, 4)
        rows.append(
            {
                "time": (start + timedelta(hours=1) * idx).isoformat(),
                "open": open_,
                "high": max(open_, close) + 0.02,
                "low": min(open_, close) - 0.02,
                "close": close,
                "vol": 1000 + idx,
            }
        )
    # Reversal candle at [-2]: bearish, below previous close.
    prev_close = rows[-3]["close"]
    rows[-2].update(
        {
            "open": prev_close + 0.05,
            "high": prev_close + 0.08,
            "low": prev_close - 0.10,
            "close": prev_close - 0.06,
        }
    )
    # Newest bar: doji closing marginally UP -> breaks the reversal condition.
    rev_close = rows[-2]["close"]
    rows[-1].update(
        {
            "open": rev_close,
            "high": rev_close + 0.03,
            "low": rev_close - 0.03,
            "close": rev_close + 0.01,
        }
    )
    candidate = _mean_reversion_candidate("fx_range_mean_reversion", rows)
    assert candidate.decision == "TRADE"
    assert candidate.trigger_bar_age == 1
    assert candidate.direction == "SHORT"


def test_breakout_retest_recovers_retest_two_bars_back(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_BARS", 3)
    context = _trend_bars(count=80, step=0.05)
    primary = [dict(row) for row in context]
    level = max(float(row["high"]) for row in primary[-22:-4])
    # Breakout bar at [-4] closes beyond the prior window high.
    bo_open = float(primary[-5]["close"])
    primary[-4].update(
        {
            "open": bo_open,
            "high": level + 0.60,
            "low": bo_open - 0.02,
            "close": level + 0.50,
        }
    )
    # Retest bar at [-3]: dips to the level, closes back above it, bullish.
    primary[-3].update(
        {
            "open": level + 0.05,
            "high": level + 0.35,
            "low": level - 0.01,
            "close": level + 0.30,
        }
    )
    # Bars [-2] / [-1]: continue up WITHOUT re-touching the level, so the
    # newest offsets fail the retest predicate and age 2 wins.
    follow = level + 0.30
    for idx in (-2, -1):
        open_ = follow
        close = follow + 0.12
        primary[idx].update(
            {
                "open": open_,
                "high": close + 0.03,
                "low": open_ + 0.02,
                "close": close,
            }
        )
        follow = close
    candidate = _breakout_retest_candidate(
        "fx_breakout_retest",
        primary,
        context,
        require_contraction=False,
    )
    assert candidate.decision == "TRADE"
    assert candidate.trigger_bar_age == 2


def test_detect_setup_stamps_bar_age_through_evaluator_path(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_LOOKBACK_BARS", 3)
    context = _trend_bars()
    primary = [dict(row) for row in context]
    # H1 entry frames resolve per-TF period overrides (ema_trend=9 for this
    # group) — anchor the fixture to the EMA the specialist will actually use.
    _shift_pattern_back(primary, context, bars_back=2, ema_period=9)
    candles = {"H4": context, "H1": primary, "D1": context[::6] or context}
    route = route_specialist(
        {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
    )
    from engine_a_v3.setups import detect_setup

    candidate = detect_setup(
        route,
        "intraday",
        candles,
        indicator_periods={"ema_trend": 9, "ema_momentum": 21, "atr": 14},
    )
    assert candidate.decision == "TRADE"
    assert candidate.setup_id.endswith("trend_pullback")
    assert candidate.trigger_bar_age == 2
