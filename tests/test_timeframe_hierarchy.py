"""Explicit coverage for the timeframe_policy.v4 resolution/overlay matrix.

Complements tests/test_timeframe_policy.py — only cases NOT already asserted
there live here (exotic groups, single-stock D1 bias, group/asset-default
inheritance, overlay provenance preservation, speed-adaptation regime guard).
"""

from __future__ import annotations

from timeframe_policy import (
    ExecutionMode,
    LiquidityClass,
    M5Policy,
    M5Role,
    PolicySource,
    SpeedClass,
    SpeedState,
    Timeframe,
    resolve_timeframe_policy,
)


def _assert_universal_roles(policy) -> None:
    """Engine A/B universal ladder: D1 / H4 / H4 / H1 / M15."""
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.H4
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    assert policy.trigger_tf == Timeframe.M15
    assert policy.execution_tf == Timeframe.M15
    assert policy.execution_mode == ExecutionMode.LIVE_QUOTE


def _assert_tuned_h4_roles(policy) -> None:
    """Balanced liquid ladder: D1 / H4 / H4 / H1 / M15 (structure ≠ setup)."""
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.H4
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    assert policy.trigger_tf == Timeframe.M15
    assert policy.execution_tf == Timeframe.M15
    assert policy.execution_mode == ExecutionMode.LIVE_QUOTE


def _assert_thin_m30_roles(policy) -> None:
    """Enabled override ladder for thin/spread-expensive groups: M30 trigger."""
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.H4
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    assert policy.trigger_tf == Timeframe.M30
    assert policy.execution_tf == Timeframe.M15
    assert policy.execution_mode == ExecutionMode.LIVE_QUOTE


def _assert_equity_roles(policy) -> None:
    """Session equity/index intraday: H1 structure / M30 setup / M15 trigger."""
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.H4
    assert policy.structure_tf == Timeframe.H1
    assert policy.setup_tf == Timeframe.M30
    assert policy.trigger_tf == Timeframe.M15
    assert policy.execution_tf == Timeframe.M15
    assert policy.execution_mode == ExecutionMode.LIVE_QUOTE


def _assert_swing_d1_roles(policy) -> None:
    """Enabled override swing ladder: D1 structure / H4 setup / H1 trigger."""
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.D1
    assert policy.structure_tf == Timeframe.D1
    assert policy.setup_tf == Timeframe.H4
    assert policy.trigger_tf == Timeframe.H1
    assert policy.execution_tf == Timeframe.M15
    assert policy.execution_mode == ExecutionMode.LIVE_QUOTE


def test_usd_zar_exotic_liquid_resolution() -> None:
    policy = resolve_timeframe_policy(
        "USD/ZAR", "forex", "forex_exotics_liquid", "intraday"
    )
    assert policy.profile == "FOREX_EXOTICS_LIQUID"
    _assert_thin_m30_roles(policy)
    assert policy.m5_role == M5Role.DISABLED
    assert policy.m5_policy == M5Policy.DISABLED


def test_usd_brl_exotic_restricted_uses_the_thin_m30_ladder() -> None:
    policy = resolve_timeframe_policy(
        "USD/BRL", "forex", "forex_exotics_restricted", "intraday"
    )
    assert policy.profile == "FOREX_EXOTICS_RESTRICTED"
    _assert_thin_m30_roles(policy)
    assert policy.m5_role == M5Role.DISABLED
    assert policy.m5_policy == M5Policy.DISABLED


def test_us_stock_single_uses_the_equity_intraday_ladder() -> None:
    policy = resolve_timeframe_policy("AAPL", "stock", "us_stock_single", "intraday")
    assert policy.profile == "US_STOCK_SINGLE"
    _assert_equity_roles(policy)
    assert policy.m5_policy == M5Policy.CONDITIONAL


def test_unlisted_symbol_inherits_group_template() -> None:
    # EUR/CAD has no symbol override: the group template applies.
    policy = resolve_timeframe_policy(
        "EUR/CAD", "forex", "forex_crosses_broad", "intraday"
    )
    assert policy.policy_source == PolicySource.SCORE_GROUP_OVERRIDE
    assert policy.diagnostics.symbol_override_applied is False
    assert policy.profile == "FOREX_CROSSES_BROAD"
    _assert_tuned_h4_roles(policy)
    assert policy.m5_policy == M5Policy.DISABLED


def test_unlisted_symbol_without_group_inherits_asset_default() -> None:
    policy = resolve_timeframe_policy("FOO/USDT", "crypto", None, "intraday")
    assert policy.policy_source == PolicySource.ASSET_STYLE_DEFAULT
    assert policy.diagnostics.symbol_override_applied is False
    assert policy.profile == "CRYPTO_OTHER_THIN"
    _assert_universal_roles(policy)
    assert policy.m5_policy == M5Policy.DISABLED


def test_engine_a_intraday_overlay_preserves_instrument_profile() -> None:
    # A conditional-M5 fast-major keeps its profile under engine_a
    # intraday: the instrument profile is preserved while the promoted group
    # timeframe ladder is applied.
    policy = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    assert policy.profile == "FOREX_MAJORS_FAST"
    _assert_tuned_h4_roles(policy)
    assert policy.m5_role == M5Role.REFINEMENT
    assert policy.m5_policy == M5Policy.CONDITIONAL


def test_engine_b_swing_overlay_full_chain() -> None:
    policy = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "swing", engine_id="engine_b"
    )
    assert policy.profile.startswith("ENGINE_B_SWING_")
    _assert_swing_d1_roles(policy)
    assert policy.m5_role == M5Role.DISABLED
    assert policy.m5_policy == M5Policy.DISABLED


def test_engine_b_intraday_overlay_preserves_instrument_profile() -> None:
    policy = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "intraday", engine_id="engine_b"
    )
    assert policy.profile == "ENGINE_B_INTRADAY_FOREX_MAJORS_FAST"
    _assert_tuned_h4_roles(policy)
    assert policy.m5_role == M5Role.REFINEMENT
    assert policy.m5_policy == M5Policy.CONDITIONAL


def test_overlays_do_not_modify_unrelated_provenance() -> None:
    # The swing overlay renames the profile and applies the D1 swing ladder;
    # provenance stays fixed.
    intraday = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    swing = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "swing", engine_id="engine_a"
    )
    assert swing.policy_source == intraday.policy_source
    assert swing.diagnostics.score_group == intraday.diagnostics.score_group
    assert (
        swing.diagnostics.symbol_override_applied
        == intraday.diagnostics.symbol_override_applied
    )
    assert (
        swing.diagnostics.symbol_override_patched_roles
        == intraday.diagnostics.symbol_override_patched_roles
    )
    _assert_tuned_h4_roles(intraday)
    _assert_swing_d1_roles(swing)
    assert swing.profile.startswith("ENGINE_A_SWING_")


def test_speed_adaptation_never_touches_regime_or_execution_mode() -> None:
    slow = SpeedState(
        live_speed_class=SpeedClass.SLOW,
        speed_percentile=20.0,
        history_ready=True,
        liquidity_class=LiquidityClass.NORMAL,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )
    fast = SpeedState(
        live_speed_class=SpeedClass.FAST,
        speed_percentile=80.0,
        history_ready=True,
        liquidity_class=LiquidityClass.NORMAL,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )
    # FAST: full liquid ladder, no trigger demotion.
    fast_pol = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", fast
    )
    _assert_tuned_h4_roles(fast_pol)
    assert fast_pol.diagnostics.adaptation_applied is False
    assert fast_pol.regime_tf == Timeframe.D1
    assert fast_pol.execution_mode == ExecutionMode.LIVE_QUOTE
    # SLOW: trigger-only demotion M15→M30; regime/bias/structure/setup untouched.
    slow_pol = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", slow
    )
    assert slow_pol.regime_tf == Timeframe.D1
    assert slow_pol.bias_tf == Timeframe.H4
    assert slow_pol.structure_tf == Timeframe.H4
    assert slow_pol.setup_tf == Timeframe.H1
    assert slow_pol.trigger_tf == Timeframe.M30
    assert slow_pol.execution_mode == ExecutionMode.LIVE_QUOTE
    assert slow_pol.diagnostics.adaptation_applied is True
