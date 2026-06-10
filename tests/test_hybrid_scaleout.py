"""Hybrid scale-out: banker + runner legs and timed-exit config merge."""

from __future__ import annotations

import math

import timed_exit_monitor as tem


def _hybrid_volumes(total_vol: float, fraction: float, vol_step: float, vol_min: float):
    """Mirror mt5_executor hybrid scale-out volume split (banker TP1, runner no TP)."""
    frac = min(max(float(fraction), 0.0), 1.0)
    vol1 = round(math.floor((total_vol * frac) / vol_step) * vol_step, 2)
    if vol1 < vol_min:
        vol1 = vol_min
    vol2 = round(total_vol - vol1, 2)
    if vol2 < vol_min:
        return None
    return [(vol1, "banker_tp"), (vol2, "runner_no_tp")]


def test_hybrid_scaleout_splits_into_banker_and_runner_legs():
    legs = _hybrid_volumes(total_vol=0.10, fraction=0.5, vol_step=0.01, vol_min=0.01)
    assert legs is not None
    assert legs[0] == (0.05, "banker_tp")
    assert legs[1] == (0.05, "runner_no_tp")
    assert sum(v for v, _ in legs) == 0.10


def test_hybrid_scaleout_skips_when_volume_too_small_for_two_legs():
    legs = _hybrid_volumes(total_vol=0.01, fraction=0.5, vol_step=0.01, vol_min=0.01)
    assert legs is None


def test_get_timed_cfg_hybrid_scaleout_preserves_banker_tp_on_activation():
    cfg = {
        "TIMED_EXIT": {
            "tp_mode": "trailing_atr",
            "trail_clear_broker_tp_on_activation": True,
            "hybrid_scaleout": {"enabled": True, "fraction": 0.5},
        }
    }
    merged = tem._get_timed_cfg(lambda: cfg)
    assert merged["hybrid_scaleout"]["enabled"] is True
    assert merged["hybrid_scaleout"]["fraction"] == 0.5
    assert merged["trail_clear_broker_tp_on_activation"] is False


def test_get_timed_cfg_hybrid_disabled_keeps_tp_clear_default():
    cfg = {
        "TIMED_EXIT": {
            "trail_clear_broker_tp_on_activation": True,
            "hybrid_scaleout": {"enabled": False},
        }
    }
    merged = tem._get_timed_cfg(lambda: cfg)
    assert merged["hybrid_scaleout"]["enabled"] is False
    assert merged["trail_clear_broker_tp_on_activation"] is True


def test_profit_protect_and_giveback_disabled_until_calibrated():
    """Plan: do not re-enable live profit-protect/give-back without backtest calibration."""
    assert tem._DEFAULT_CFG["pre_activation_profit_protect_enabled"] is False

    merged = tem._get_timed_cfg(lambda: {"TIMED_EXIT": {}})
    assert merged["pre_activation_profit_protect_enabled"] is False
    assert merged["trail_giveback_r"]["intraday"] == 0.0
