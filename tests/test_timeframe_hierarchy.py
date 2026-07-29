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


def test_usd_zar_exotic_liquid_resolution() -> None:
    policy = resolve_timeframe_policy(
        "USD/ZAR", "forex", "forex_exotics_liquid", "intraday"
    )
    assert policy.profile == "FOREX_EXOTICS_LIQUID"
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.H4
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    assert policy.trigger_tf == Timeframe.M30
    assert policy.execution_tf == Timeframe.M30
    assert policy.m5_role == M5Role.DISABLED
    assert policy.m5_policy == M5Policy.DISABLED
    assert policy.execution_mode == ExecutionMode.LIVE_QUOTE


def test_usd_brl_exotic_restricted_h1_confirmed_trigger_no_lower_tfs() -> None:
    policy = resolve_timeframe_policy(
        "USD/BRL", "forex", "forex_exotics_restricted", "intraday"
    )
    assert policy.profile == "FOREX_EXOTICS_RESTRICTED"
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.H4
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    # H1 confirmed trigger: no M30/M15/M5 anywhere in the role chain.
    assert policy.trigger_tf == Timeframe.H1
    assert policy.execution_tf == Timeframe.H1
    role_tfs = {
        policy.regime_tf,
        policy.bias_tf,
        policy.structure_tf,
        policy.setup_tf,
        policy.trigger_tf,
        policy.execution_tf,
    }
    assert role_tfs.isdisjoint({Timeframe.M30, Timeframe.M15, Timeframe.M5, Timeframe.M1})
    assert policy.m5_role == M5Role.DISABLED
    assert policy.m5_policy == M5Policy.DISABLED


def test_us_stock_single_d1_bias() -> None:
    policy = resolve_timeframe_policy("AAPL", "stock", "us_stock_single", "intraday")
    assert policy.profile == "US_STOCK_SINGLE"
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.D1
    assert policy.structure_tf == Timeframe.H1
    assert policy.setup_tf == Timeframe.M15
    assert policy.trigger_tf == Timeframe.M15
    assert policy.m5_policy == M5Policy.CONDITIONAL
    assert policy.execution_mode == ExecutionMode.LIVE_QUOTE


def test_unlisted_symbol_inherits_group_template() -> None:
    # No symbol override for USD/SEK: the exotic-liquid group template applies.
    policy = resolve_timeframe_policy(
        "USD/SEK", "forex", "forex_exotics_liquid", "intraday"
    )
    assert policy.policy_source == PolicySource.SCORE_GROUP_OVERRIDE
    assert policy.diagnostics.symbol_override_applied is False
    assert policy.profile == "FOREX_EXOTICS_LIQUID"
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    assert policy.trigger_tf == Timeframe.M30
    assert policy.m5_policy == M5Policy.DISABLED


def test_unlisted_symbol_without_group_inherits_asset_default() -> None:
    policy = resolve_timeframe_policy("FOO/USDT", "crypto", None, "intraday")
    assert policy.policy_source == PolicySource.ASSET_STYLE_DEFAULT
    assert policy.diagnostics.symbol_override_applied is False
    assert policy.profile == "CRYPTO_OTHER_THIN"
    assert policy.structure_tf == Timeframe.H4
    assert policy.m5_policy == M5Policy.DISABLED


def test_engine_a_intraday_overlay_preserves_instrument_profile() -> None:
    # A conditional-M5 fast-major keeps its full profile under engine_a
    # intraday: the overlay is a no-op, not a timeframe rewrite.
    policy = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    assert policy.profile == "FOREX_MAJORS_FAST"
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.H4
    assert policy.structure_tf == Timeframe.H1
    assert policy.setup_tf == Timeframe.M15
    assert policy.trigger_tf == Timeframe.M15
    assert policy.execution_tf == Timeframe.M15
    assert policy.m5_role == M5Role.REFINEMENT
    assert policy.m5_policy == M5Policy.CONDITIONAL


def test_engine_b_swing_overlay_full_chain() -> None:
    policy = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "swing", engine_id="engine_b"
    )
    assert policy.profile.startswith("ENGINE_B_SWING_")
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.D1
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H4
    assert policy.trigger_tf == Timeframe.H1
    assert policy.execution_tf == Timeframe.H1
    assert policy.m5_role == M5Role.DISABLED
    assert policy.m5_policy == M5Policy.DISABLED
    assert policy.execution_mode == ExecutionMode.LIVE_QUOTE


def test_engine_b_intraday_overlay_preserves_instrument_profile() -> None:
    policy = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "intraday", engine_id="engine_b"
    )
    assert policy.profile == "ENGINE_B_INTRADAY_FOREX_MAJORS_FAST"
    assert policy.trigger_tf == Timeframe.M15
    assert policy.m5_role == M5Role.REFINEMENT
    assert policy.m5_policy == M5Policy.CONDITIONAL


def test_overlays_do_not_modify_unrelated_provenance() -> None:
    # The swing overlay patches role timeframes only; resolution provenance
    # (policy source, score group, symbol-override diagnostics) is untouched.
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
    # Roles did move to the slow swing chain — provenance alone was preserved.
    assert swing.bias_tf == Timeframe.D1
    assert swing.structure_tf == Timeframe.H4
    assert intraday.bias_tf == Timeframe.H4
    assert intraday.structure_tf == Timeframe.H1


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
    for state in (slow, fast):
        policy = resolve_timeframe_policy(
            "EUR/USD", "forex", "forex_majors", "intraday", state
        )
        assert policy.regime_tf == Timeframe.D1
        assert policy.bias_tf == Timeframe.H4
        assert policy.structure_tf == Timeframe.H1
        assert policy.execution_mode == ExecutionMode.LIVE_QUOTE
        # Advisory execution context is never speed-shifted.
        assert policy.execution_tf == Timeframe.M15
