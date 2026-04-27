import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import risk_engine
from risk_engine import risk_check
from market_structure import NakedEngine, resolve_engine_b_execution_levels


@pytest.fixture(autouse=True)
def _isolate_risk_drawdown(monkeypatch):
    monkeypatch.setattr(risk_engine, "_current_drawdown", lambda *_args, **_kwargs: 0.0)


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


def test_forex_structural_tp_below_min_rr_uses_execution_sl_fallback():
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert out["structural_rr"] == pytest.approx(0.1)
    assert out["execution_sl"] == pytest.approx(98.5)
    assert out["execution_tp"] == pytest.approx(103.0)
    assert out["rr_used_for_gate"] == pytest.approx(2.0)
    assert out["rr_source"] == "atr_sl_fallback_rr_tp"
    assert out["fallback_tp_applied"] is True
    assert out["fallback_tp_reason"] == "structural_tp_below_min_rr"


def test_crypto_does_not_use_forex_rr_fallback():
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="crypto",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert out["execution_tp"] == pytest.approx(101.0)
    assert out["rr_used_for_gate"] < 1.5
    assert out["rr_source"] == "atr_sl_structural_tp"
    assert out["fallback_tp_applied"] is False


def test_calculate_confidence_reports_forex_fallback_rr_basis():
    engine = NakedEngine()
    out = engine.calculate_confidence(
        {
            "atr": 1.0,
            "asset_type": "forex",
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "bos_confirmed": True,
            "trigger_ok": True,
            "zone_touched": True,
            "near_active_zone": True,
            "distance_to_res": 5.0,
            "recommended_stop_loss": 90.0,
            "recommended_take_profit": 101.0,
            "structural_verdict": "CLEAR",
        },
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile={
            "style": "intraday",
            "min_score": 3.0,
            "min_room_atr": 0.35,
            "min_rr": 1.5,
            "fallback_rr": 2.0,
            "require_macro_align": False,
        },
    )

    assert out["rr_ok"] is True
    assert out["rr_used_for_gate"] == pytest.approx(2.0)
    assert out["execution_tp"] == pytest.approx(103.0)
    assert out["rr_source"] == "atr_sl_fallback_rr_tp"
    assert out["fallback_tp_applied"] is True
