from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ghost_trade.exhaustion import contextual_exhaustion
from ghost_trade.levels import latest_impulse_pullback, structural_geometry, structural_trigger
from ghost_trade.market_data import CandleBundle, confirmed_only
from ghost_trade.models import Candle, Direction, Swing, SwingKind, VolatilityRegime
from ghost_trade.momentum import momentum_diagnostics
from ghost_trade.scoring import (
    clamp_live_overlay,
    confirmed_score,
    direction_confidence,
    entry_quality,
    pullback_score,
    score_confirmed,
    score_live_overlay,
)
from ghost_trade.structure import confirmed_fractals, structure_from_swings
from ghost_trade.volatility import classify_volatility, volatility_diagnostics


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candle(
    index: int,
    *,
    open_: float = 1.0,
    high: float = 2.0,
    low: float = 0.0,
    close: float = 1.0,
    volume: float | None = 100.0,
    hours: int = 1,
) -> Candle:
    opened = BASE + timedelta(hours=index * hours)
    return Candle(
        open_time=opened,
        close_time=opened + timedelta(hours=hours),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def swing(index: int, price: float, kind: SwingKind) -> Swing:
    return Swing(
        kind=kind,
        center_index=index,
        confirmation_index=index + 2,
        price=price,
        center_time=BASE + timedelta(days=index),
        confirmation_time=BASE + timedelta(days=index + 2),
    )


def test_fractal_center_cannot_see_its_t_plus_two_confirmation():
    bars = [
        candle(0, high=1),
        candle(1, high=2),
        candle(2, high=5),
        candle(3, high=2),
        candle(4, high=1),
    ]

    before = confirmed_fractals(bars, available_through=3)
    after = confirmed_fractals(bars, available_through=4)

    assert before.highs == ()
    assert len(after.highs) == 1
    assert after.highs[0].center_index == 2
    assert after.highs[0].confirmation_index == 4


@pytest.mark.parametrize(
    "high_prices,low_prices,expected",
    [
        ((10, 11, 12), (5, 6, 7), 1.0),
        ((12, 11, 10), (7, 6, 5), -1.0),
        ((10, 12, 11), (5, 6, 5.5), 0.0),
    ],
)
def test_d1_structure_requires_ordered_highs_and_lows(high_prices, low_prices, expected):
    highs = tuple(swing(i * 5, price, SwingKind.HIGH) for i, price in enumerate(high_prices))
    lows = tuple(swing(i * 5 + 2, price, SwingKind.LOW) for i, price in enumerate(low_prices))

    result = structure_from_swings(highs, lows, atr_value=2.0, current_index=20)

    assert result.direction == expected
    assert 0.0 <= result.strength <= 1.0
    assert result.last_swing_highs == high_prices
    assert result.last_swing_lows == low_prices


def test_missing_volume_renormalizes_momentum_to_roc_only():
    bars = [
        candle(i, open_=100 + i * 0.1, high=101 + i * 0.1, low=99 + i * 0.1, close=100 + i * 0.1, volume=None)
        for i in range(80)
    ]

    result = momentum_diagnostics(bars, volume_mode="unavailable")

    assert result.roc_available is True
    assert result.pressure_available is False
    assert result.volume_quality == 0.0
    assert result.direction == pytest.approx(result.normalized_roc)


def test_structural_geometry_uses_correct_sides_and_nearest_target():
    h1 = (
        swing(1, 94, SwingKind.LOW),
        swing(5, 95, SwingKind.LOW),
        swing(7, 105, SwingKind.HIGH),
    )
    h4 = (
        swing(1, 110, SwingKind.HIGH),
        swing(5, 106, SwingKind.HIGH),
        swing(7, 90, SwingKind.LOW),
    )

    long_result = structural_geometry(Direction.LONG, 100, h1, h4, h1_atr=5)
    short_result = structural_geometry(Direction.SHORT, 100, h1, h4, h1_atr=5)

    assert long_result.reasons == ()
    assert long_result.geometry is not None
    assert long_result.geometry.unbuffered_stop == 95
    assert long_result.geometry.stop == 94.5
    assert long_result.geometry.target == 106
    assert long_result.geometry.stop < 100 < long_result.geometry.target
    assert short_result.reasons == ()
    assert short_result.geometry is not None
    assert short_result.geometry.stop == 105.5
    assert short_result.geometry.target == 90
    assert short_result.geometry.target < 100 < short_result.geometry.stop


def test_missing_structural_stop_or_target_returns_explicit_reasons():
    result = structural_geometry(
        Direction.LONG,
        100,
        (swing(1, 101, SwingKind.LOW),),
        (swing(1, 99, SwingKind.HIGH),),
        h1_atr=2,
    )

    assert result.geometry is None
    assert result.reasons == ("no_structural_stop", "no_structural_target")


def test_entry_quality_formula_and_pullback_plateau_are_bounded():
    result = entry_quality(
        raw_rr=2.0,
        target_distance=4.0,
        h1_atr=2.0,
        pullback_depth=0.50,
        trigger_quality=1.0,
    )

    assert result.rr_score == pytest.approx(0.5)
    assert result.room_score == pytest.approx(1.0)
    assert result.pullback_score == pytest.approx(1.0)
    assert result.trigger_score == pytest.approx(1.0)
    assert result.score == pytest.approx(0.80)
    assert pullback_score(0.0) == 0.0
    assert pullback_score(0.25) == 1.0
    assert pullback_score(0.65) == 1.0
    assert pullback_score(1.0) == 0.0


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"atr_percentile": 20, "bb_width_percentile": 20}, VolatilityRegime.COMPRESSED),
        ({"atr_percentile": 75, "bb_width_percentile": 75}, VolatilityRegime.EXPANDING),
        ({"atr_percentile": 95, "bb_width_percentile": 50}, VolatilityRegime.EXTREME),
        (
            {
                "atr_percentile": 55,
                "bb_width_percentile": 35,
                "prior_bb_width_percentile": 20,
                "atr_rising": True,
                "bb_width_rising": True,
            },
            VolatilityRegime.SQUEEZE_RELEASE,
        ),
        ({"atr_percentile": 55, "bb_width_percentile": 55}, VolatilityRegime.NORMAL),
    ],
)
def test_volatility_classification(kwargs, expected):
    assert classify_volatility(**kwargs) is expected


def test_contextual_exhaustion_combines_signals_but_caps_at_point_three():
    bars = [candle(i, open_=9, high=10, low=8.5, close=9, volume=100) for i in range(22)]
    bars[-3] = candle(19, open_=9.0, high=10.0, low=8.8, close=9.5, volume=30)
    bars[-2] = candle(20, open_=9.5, high=10.3, low=9.2, close=9.8, volume=20)
    bars[-1] = candle(21, open_=10.0, high=11.2, low=9.0, close=9.2, volume=10)

    result = contextual_exhaustion(
        Direction.LONG,
        bars,
        target=11.5,
        h1_atr=1.0,
        volume_available=True,
    )

    assert result.adverse_wick is True
    assert result.declining_participation is True
    assert result.penalty == pytest.approx(0.30)


def test_direction_entry_and_final_score_remain_separate():
    confidence = direction_confidence(1.0, 0.8, 0.4)
    quality = entry_quality(2.0, 4.0, 2.0, 0.5, 1.0)
    score = confirmed_score(confidence, quality.score, 0.15, 0.10)

    assert confidence == pytest.approx(0.62)
    assert quality.score == pytest.approx(0.80)
    assert score == pytest.approx(0.246)


def test_confirmed_candles_exclude_the_forming_bar_as_of_timestamp():
    bars = [candle(0), candle(1), candle(2)]
    as_of = bars[1].close_time

    result = confirmed_only(bars, as_of=as_of)

    assert result == tuple(bars[:2])


def test_live_overlay_is_capped_and_does_not_rewrite_confirmed_score():
    positive = clamp_live_overlay(confirmed_score_value=0.64, requested_adjustment=0.25)
    negative = clamp_live_overlay(confirmed_score_value=0.04, requested_adjustment=-0.25)

    assert positive.confirmed_score == 0.64
    assert positive.adjustment == 0.10
    assert positive.display_score == pytest.approx(0.74)
    assert negative.confirmed_score == 0.04
    assert negative.adjustment == -0.10
    assert negative.display_score == 0.0


def test_h4_volatility_percentiles_use_completed_history_only():
    bars = [
        candle(
            i,
            open_=100,
            high=100 + 0.1 * (i + 1),
            low=100 - 0.1 * (i + 1),
            close=100,
            hours=4,
        )
        for i in range(80)
    ]

    result = volatility_diagnostics(bars)

    assert result.available is True
    assert result.atr_percentile > 90
    assert 0 <= result.bb_width_percentile <= 100
    assert result.regime is VolatilityRegime.EXTREME


def test_latest_impulse_pullback_and_structural_trigger_are_directional():
    swings = (
        swing(1, 90, SwingKind.LOW),
        swing(5, 110, SwingKind.HIGH),
        swing(7, 100, SwingKind.LOW),
    )
    bars = [candle(i, open_=100, high=104, low=99, close=103) for i in range(5)]
    bars[-1] = candle(4, open_=101, high=106, low=99, close=105)

    depth = latest_impulse_pullback(Direction.LONG, 100, swings)
    trigger = structural_trigger(Direction.LONG, bars, swings)

    assert depth == pytest.approx(0.5)
    assert trigger == 1.0


def test_top_down_scorer_fails_closed_with_explicit_missing_history_reasons():
    bundle = CandleBundle(
        d1=tuple(candle(i, hours=24) for i in range(10)),
        h4=tuple(candle(i, hours=4) for i in range(10)),
        h1=tuple(candle(i) for i in range(10)),
        as_of=BASE + timedelta(days=20),
    )

    result = score_confirmed(bundle, volume_mode="unavailable")

    assert result.direction is Direction.NEUTRAL
    assert result.selected is False
    assert "insufficient_d1_history" in result.reasons
    assert "insufficient_h4_history" in result.reasons
    assert "insufficient_h1_history" in result.reasons
    assert 0.0 <= result.confirmed_score <= 1.0


def test_live_overlay_uses_forming_m15_without_mutating_confirmed_score():
    confirmed_m15 = tuple(
        candle(i, open_=100, high=101, low=99, close=100, hours=1)
        for i in range(20)
    )
    forming = Candle(
        open_time=BASE + timedelta(hours=20),
        close_time=BASE + timedelta(hours=20, minutes=15),
        open=100,
        high=103,
        low=99.8,
        close=102.5,
        volume=200,
    )
    bundle = CandleBundle(
        d1=(), h4=(), h1=confirmed_m15, m15_confirmed=confirmed_m15,
        m15_forming=forming, as_of=forming.open_time,
    )
    confirmed = SimpleNamespace(
        direction=Direction.LONG,
        confirmed_score=0.64,
        geometry=SimpleNamespace(geometry=SimpleNamespace(
            entry=100, stop=95, target=110, risk_distance=5,
        )),
    )

    overlay = score_live_overlay(bundle, confirmed)

    assert overlay.confirmed_score == 0.64
    assert 0 < overlay.adjustment <= 0.10
    assert overlay.display_score > overlay.confirmed_score
    assert overlay.diagnostics["currentCandleState"] == "FORMING"
