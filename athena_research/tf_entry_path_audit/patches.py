"""Research-only runtime patches for TF matrix and indicator profiles."""

from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

from athena_research.tf_entry_path_audit.parameter_intent import IndicatorProfile


def diagnostic_mode_enabled() -> bool:
    return os.environ.get("ATHENA_TF_ENTRY_PATH_AUDIT", "").strip() in ("1", "true", "yes")


@contextmanager
def ensure_diagnostic_mode() -> Iterator[None]:
    prev = os.environ.get("ATHENA_TF_ENTRY_PATH_AUDIT")
    os.environ["ATHENA_DIAGNOSTIC_MODE"] = "1"
    os.environ["ATHENA_TF_ENTRY_PATH_AUDIT"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("ATHENA_TF_ENTRY_PATH_AUDIT", None)
        else:
            os.environ["ATHENA_TF_ENTRY_PATH_AUDIT"] = prev


@contextmanager
def engine_b_tf_patch(
    *,
    signal_tf: str,
    entry_tf: str,
    zone_tf: str | None = None,
    struct_tf: str | None = None,
    atr_tf: str | None = None,
) -> Iterator[None]:
    """Override Engine B signal/entry timeframes on backtest runtime."""
    from tools.h4_overlay_runtime import bootstrap_backtest_runtime

    bootstrap_backtest_runtime()
    import backtest_runner

    rt = backtest_runner._rt()
    original_style = rt.naked_scan_style_profile
    ztf = zone_tf or signal_tf
    stf = struct_tf or signal_tf
    atf = atr_tf or signal_tf

    def _style(style, score_group=None, asset_type=None):
        resolved, profile = original_style(style, score_group=score_group, asset_type=asset_type)
        profile = dict(profile)
        profile["entry_tf"] = entry_tf
        profile["zone_tf"] = ztf
        profile["struct_tf"] = stf
        profile["atr_tf"] = atf
        return resolved, profile

    rt.naked_scan_style_profile = _style
    try:
        yield
    finally:
        rt.naked_scan_style_profile = original_style


@contextmanager
def indicator_profile_patch(profile: IndicatorProfile) -> Iterator[None]:
    """Apply indicator tuning profile for research runs."""
    if profile == IndicatorProfile.BAR_NATIVE:
        yield
        return

    import config
    from tools.h4_overlay_runtime import apply_h4_indicator_overlay, build_h4_tuned_periods_from_live_config

    live_cfg = dict(config.CONFIG or {})
    if profile == IndicatorProfile.CALENDAR_EQUIVALENT:
        from tools.h4_overlay_runtime import load_h4_overlay

        with apply_h4_indicator_overlay(load_h4_overlay()):
            yield
        return

    if profile == IndicatorProfile.VOLATILITY_NATIVE:
        tuned = build_h4_tuned_periods_from_live_config(live_cfg)
        overlay_doc = copy.deepcopy(tuned)
        overlay_doc["_profile"] = "volatility_native"
        with apply_h4_indicator_overlay(overlay_doc):
            yield
        return

    yield


@contextmanager
def score_group_aware_indicators() -> Iterator[None]:
    """Patch backtest_runner to pass score_group into indicator calc."""
    import backtest_runner
    from tools.h4_overlay_runtime import bootstrap_backtest_runtime

    bootstrap_backtest_runtime()
    original_calc = backtest_runner.calc_indicators_with_normalized

    def _calc(candles, asset_type, score_group=None):
        sg = score_group or getattr(_calc, "_active_score_group", None)
        return original_calc(candles, asset_type, score_group=sg)

    with patch.object(backtest_runner, "calc_indicators_with_normalized", _calc):
        yield _calc
