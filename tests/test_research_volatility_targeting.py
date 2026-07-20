import math
from pathlib import Path

import pytest

from config import CONFIG


def test_research_vol_targeting_disabled_by_default():
    assert CONFIG.get("RESEARCH_VOL_TARGETING_ENABLED") is False


def test_inverse_vol_multiplier_is_capped():
    from athena_research.volatility_targeting import inverse_volatility_weight

    assert inverse_volatility_weight(0.02, target_volatility=0.12, min_multiplier=0.25, max_multiplier=1.5) == 1.5
    assert inverse_volatility_weight(1.0, target_volatility=0.12, min_multiplier=0.25, max_multiplier=1.5) == 0.25


@pytest.mark.parametrize("bad_vol", [0.0, -0.1, float("nan"), None])
def test_zero_or_nan_volatility_is_safe_neutral(bad_vol):
    from athena_research.volatility_targeting import inverse_volatility_weight

    assert inverse_volatility_weight(bad_vol, target_volatility=0.12) == 1.0


def test_atr_percent_fallback_works():
    from athena_research.volatility_targeting import atr_percent_volatility

    out = atr_percent_volatility(atr=2.0, close=100.0)

    assert out == pytest.approx(0.02)


def test_realized_volatility_and_size_outputs_are_deterministic():
    from athena_research.volatility_targeting import (
        apply_research_vol_target_size,
        realized_volatility_from_returns,
    )

    returns = [0.01, -0.005, 0.002, 0.004, -0.003]
    vol_1 = realized_volatility_from_returns(returns, annualization=252)
    vol_2 = realized_volatility_from_returns(returns, annualization=252)
    assert vol_1 == vol_2

    cfg = {
        "RESEARCH_VOL_TARGETING_ENABLED": True,
        "RESEARCH_VOL_TARGET_ANNUALIZED": 0.12,
        "RESEARCH_VOL_TARGET_MAX_MULTIPLIER": 1.50,
        "RESEARCH_VOL_TARGET_MIN_MULTIPLIER": 0.25,
        "RESEARCH_VOL_TARGET_LOOKBACK": 5,
    }
    applied_1 = apply_research_vol_target_size(
        original_size=0.01,
        returns=returns,
        atr=2.0,
        close=100.0,
        cfg=cfg,
    )
    applied_2 = apply_research_vol_target_size(
        original_size=0.01,
        returns=returns,
        atr=2.0,
        close=100.0,
        cfg=cfg,
    )

    assert applied_1 == applied_2
    assert applied_1["vol_targeting_applied"] is True
    assert applied_1["original_size"] == 0.01
    assert 0.0025 <= applied_1["vol_target_adjusted_size"] <= 0.015


def test_apply_research_vol_target_size_disabled_returns_original():
    from athena_research.volatility_targeting import apply_research_vol_target_size

    out = apply_research_vol_target_size(
        original_size=0.01,
        returns=[0.01, -0.005],
        atr=2.0,
        close=100.0,
        cfg={"RESEARCH_VOL_TARGETING_ENABLED": False},
    )

    assert out["vol_targeting_applied"] is False
    assert out["original_size"] == 0.01
    assert out["vol_target_adjusted_size"] == 0.01
    assert out["multiplier"] == 1.0


def test_live_execution_sources_do_not_reference_research_vol_targeting():
    root = Path(__file__).resolve().parents[1]
    live_sources = [
        (root / "risk_engine.py").read_text(encoding="utf-8"),
        (root / "execution.py").read_text(encoding="utf-8"),
    ]

    assert all("volatility_targeting" not in source for source in live_sources)
    assert all("RESEARCH_VOL_TARGET" not in source for source in live_sources)


# test_backtest_research_vol_target_metadata_does_not_change_equity_input removed:
# it exercised the retired legacy backtester's _research_vol_target_bt_metadata
# helper (now in archive/backtest_legacy/); the pure sizing math above still has
# full coverage via research_metrics.
