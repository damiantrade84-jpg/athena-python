import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk_engine import risk_check
from market_structure import resolve_engine_b_execution_levels

def _make_signal(**overrides):
    base = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 100.0,
        "sl": 95.0,
        "tp1": 110.0,
        "tp2": 120.0,
        "type": "crypto",
        "timestamp": None,
        "confluenceScore": 5.0,
    }
    base.update(overrides)
    return base

def test_long_sl_below_entry():
    # Approved since SL (95) < Entry (100)
    result = risk_check(_make_signal(price=100.0, sl=95.0, tp1=110.0), 10000.0, 10000.0, [])
    assert result.approved is True

def test_short_sl_above_entry():
    # Approved since SL (105) > Entry (100)
    result = risk_check(_make_signal(direction="SHORT", price=100.0, sl=105.0, tp1=90.0), 10000.0, 10000.0, [])
    assert result.approved is True

def test_long_tp_above_entry():
    # Approved since TP (110) > Entry (100)
    result = risk_check(_make_signal(price=100.0, sl=95.0, tp1=110.0), 10000.0, 10000.0, [])
    assert result.approved is True

def test_short_tp_below_entry():
    # Approved since TP (90) < Entry (100)
    result = risk_check(_make_signal(direction="SHORT", price=100.0, sl=105.0, tp1=90.0), 10000.0, 10000.0, [])
    assert result.approved is True

def test_wrong_side_sl_rejected_long():
    # Rejected since SL (105) >= Entry (100) for LONG
    result = risk_check(_make_signal(price=100.0, sl=105.0, tp1=120.0), 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "INVALID_LEVELS"

def test_wrong_side_sl_rejected_short():
    # Rejected since SL (95) <= Entry (100) for SHORT
    result = risk_check(_make_signal(direction="SHORT", price=100.0, sl=95.0, tp1=80.0), 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "INVALID_LEVELS"

def test_wrong_side_tp_rejected_long():
    # Rejected since TP (90) <= Entry (100) for LONG
    result = risk_check(_make_signal(price=100.0, sl=95.0, tp1=90.0), 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "INVALID_LEVELS"

def test_wrong_side_tp_rejected_short():
    # Rejected since TP (110) >= Entry (100) for SHORT
    result = risk_check(_make_signal(direction="SHORT", price=100.0, sl=105.0, tp1=110.0), 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "INVALID_LEVELS"

def test_rr_uses_absolute_reward_absolute_risk():
    # Verify resolve_engine_b_execution_levels absolute RR computation
    out_long = resolve_engine_b_execution_levels(
        direction="LONG", entry=100.0, structural_sl=90.0, structural_tp=120.0, atr=1.0, style="intraday", asset_class="forex"
    )
    # Execution SL: max(atr_sl, structural_sl) -> max(100 - 1.5*1, 90) = max(98.5, 90) = 98.5
    # Execution TP: structural_tp = 120.0
    # execution_rr = |120 - 100| / |100 - 98.5| = 20 / 1.5 = 13.333
    assert out_long["structural_rr"] == pytest.approx(2.0)
    
    out_short = resolve_engine_b_execution_levels(
        direction="SHORT", entry=100.0, structural_sl=110.0, structural_tp=80.0, atr=1.0, style="intraday", asset_class="forex"
    )
    # Execution SL: min(atr_sl, structural_sl) -> min(100 + 1.5*1, 110) = min(101.5, 110) = 101.5
    # Execution TP: 80.0
    # execution_rr = |80 - 100| / |100 - 101.5| = 20 / 1.5 = 13.333
    assert out_short["structural_rr"] == pytest.approx(2.0)

def test_live_and_backtest_same_sl_tp_basis():
    # Verify live Engine B resolution returns the same ATR levels used in backtest logic.
    out = resolve_engine_b_execution_levels(
        direction="LONG", entry=100.0, structural_sl=90.0, structural_tp=120.0, atr=1.0, style="intraday", asset_class="forex"
    )
    # Both systems read CONFIG.STYLE_ATR_MULTS and ATR_CLASS
    # Intraday mult for forex is 1.5
    # Live engine:
    assert out["execution_sl"] == pytest.approx(98.5)
    assert out["execution_tp"] == pytest.approx(120.0)
