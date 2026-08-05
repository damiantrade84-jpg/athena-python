"""Regression guard for the Engine B backtest style-profile memo.

``athena_backtest/engines/engine_b.py`` memoizes the per-bar
``resolve_engine_b_style_profile`` call on the premise that policy v4 never
lets ``speed_state`` rewrite the resolved role timeframes or the style profile
— speed is recorded for diagnostics and M5-eligibility only
(``timeframe_policy.py``: "speed no longer demotes setup/trigger either").
If a future policy change makes speed affect the resolved profile, this test
fails and the memo must be revisited before the backtest diverges from the
per-bar resolution live scoring performs.
"""

from __future__ import annotations

from types import SimpleNamespace

from engine_b_snapshot import resolve_engine_b_style_profile
from timeframe_policy import LiquidityClass, SpeedClass, SpeedThresholds


def _speed_state(speed: SpeedClass, liquidity: LiquidityClass) -> SimpleNamespace:
    # Attribute set resolve_timeframe_policy reads: live_speed_class,
    # history_ready, liquidity_class, thresholds, missing_inputs.
    return SimpleNamespace(
        live_speed_class=speed,
        history_ready=True,
        liquidity_class=liquidity,
        thresholds=SpeedThresholds(),
        missing_inputs=(),
    )


def test_engine_b_style_profile_is_speed_state_independent_in_v4():
    baseline = resolve_engine_b_style_profile(
        "intraday", "forex_majors", "forex", symbol="EUR/USD"
    )
    fast = resolve_engine_b_style_profile(
        "intraday",
        "forex_majors",
        "forex",
        symbol="EUR/USD",
        speed_state=_speed_state(SpeedClass.FAST, LiquidityClass.DEEP),
    )
    slow_thin = resolve_engine_b_style_profile(
        "intraday",
        "forex_majors",
        "forex",
        symbol="EUR/USD",
        speed_state=_speed_state(SpeedClass.SLOW, LiquidityClass.THIN),
    )

    assert baseline[0] == fast[0] == slow_thin[0]
    assert baseline[1] == fast[1] == slow_thin[1]
