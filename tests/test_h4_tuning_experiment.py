"""Tests for H4 tuning experiment runner (research-only)."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from tools.h4_overlay_runtime import (
    apply_h4_indicator_overlay,
    build_h4_tuned_periods_from_live_config,
    load_h4_overlay,
    research_backtest_patches,
)


def test_build_h4_tuned_periods_scales_forex_majors():
    tables = build_h4_tuned_periods_from_live_config(CONFIG)
    ema = tables["ENGINE_A_EMA_PERIODS_BY_CLASS"]["forex_majors"]
    assert ema["trend"] == 13
    assert ema["momentum"] == 15
    assert tables["ENGINE_A_RSI_PERIOD_BY_CLASS"]["forex_majors"] == 9
    assert tables["ENGINE_B_SWEEP_LOOKBACK_BARS"] == 2


def test_apply_overlay_changes_resolvers_without_mutating_config():
    import factor_scoring

    baseline_cfg = copy.deepcopy(dict(CONFIG))
    overlay = load_h4_overlay()
    with apply_h4_indicator_overlay(overlay):
        ema = factor_scoring._resolve_ema_periods("forex_majors", "forex")
        rsi = factor_scoring._resolve_rsi_period("forex_majors", "forex")
        assert ema["trend"] == 13
        assert rsi == 9
        assert CONFIG.get("ENGINE_B_SWEEP_LOOKBACK_BARS") == 2
    after = factor_scoring._resolve_ema_periods("forex_majors", "forex")
    assert after["trend"] != 13 or CONFIG["ENGINE_A_EMA_PERIODS_BY_CLASS"]["forex_majors"]["trend"] == 26
    assert dict(CONFIG) == baseline_cfg


def test_research_backtest_patches_entry_tf(monkeypatch):
    from types import SimpleNamespace

    import backtest_runner
    from tools.h4_overlay_runtime import entry_tf_override

    runtime = SimpleNamespace(
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None: (
            "intraday",
            {"entry_tf": "H1", "min_score": 4.0, "min_rr": 1.2, "atr_tf": "H4"},
        )
    )
    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    import tools.h4_overlay_runtime as ort

    monkeypatch.setattr(ort, "bootstrap_backtest_runtime", lambda: None)
    with entry_tf_override("H4"):
        _, profile = backtest_runner._rt().naked_scan_style_profile("intraday")
    assert profile["entry_tf"] == "H4"


def test_xau_pair_uses_gc_symbol():
    from tools._athena_pair_loader import find_pair_by_display

    pair = find_pair_by_display("XAU/USD")
    assert pair is not None
    assert pair["symbol"] == "GC=F"
