from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import config
import risk_engine
from auto_trader import AutoTrader
from guardian import pre_trade_invariants
from m5_eligibility import M5EligibilityContext, evaluate_m5_eligibility
from market_structure import NakedEngine, resolve_engine_b_execution_levels
from risk_engine import clamp_execution_sl_to_max_sl_pct, risk_check
from tp_sl_rr_gate_policy import (
    engine_ab_profitability_gates_enforced,
    resolve_profitability_gate_engine,
)


@pytest.fixture(autouse=True)
def _advisory_default(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_AB_PROFITABILITY_GATES_ENFORCED", False)
    monkeypatch.setattr(risk_engine, "_current_drawdown", lambda *_args, **_kwargs: 0.0)


def _engine_a_signal(**overrides):
    signal = {
        "pair": "BTCUSDT",
        "symbol": "BTCUSDT",
        "engine": "engine_a",
        "direction": "LONG",
        "price": 100.0,
        "sl": 90.0,
        "tp1": 102.0,
        "tp2": 103.0,
        "type": "crypto",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confluenceScore": 5.0,
        "candleConsistency": {"H4": {"status": "OK"}},
    }
    signal.update(overrides)
    return signal


def test_selected_engine_a_is_not_misclassified_by_engine_b_overlay():
    signal = _engine_a_signal(
        engine_b={"passed": True},
        naked_data={"passed": True},
    )
    assert resolve_profitability_gate_engine(signal) == "engine_a"
    assert engine_ab_profitability_gates_enforced(config.CONFIG, signal=signal) is False


def test_engine_b_preserves_low_rr_wide_structural_plan_and_reports_facts():
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=80.0,
        structural_tp=110.0,
        atr=5.0,
        style="intraday",
        asset_class="forex",
        min_rr=2.0,
        fallback_rr=2.0,
        pair="EUR/USD",
    )

    assert out["execution_levels_valid"] is True
    assert out["execution_sl"] == pytest.approx(80.0)
    assert out["execution_tp"] == pytest.approx(110.0)
    assert out["rr_actual"] == pytest.approx(0.5)
    assert out["rr_required"] == pytest.approx(2.0)
    assert out["rr_passed"] is False
    assert out["rr_gate_enforced"] is False
    assert out["max_sl_threshold_met"] is False
    assert out["max_sl_gate_enforced"] is False


def test_engine_b_low_rr_passes_but_room_gate_remains_mandatory():
    engine = NakedEngine()
    base = {
        "atr": 5.0,
        "asset_type": "crypto",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "zone_touched": True,
        "trigger_ok": True,
        "recommended_stop_loss": 95.0,
        "recommended_take_profit": 102.0,
        "bos_confirmed": True,
        "bos_volume_confirmed": True,
    }
    profile = {"style": "intraday", "min_rr": 2.0, "min_room_atr": 0.5}

    clear = engine.calculate_confidence(
        {**base, "distance_to_res": 20.0},
        100.0,
        "LONG",
        style_profile=profile,
    )
    blocked = engine.calculate_confidence(
        {**base, "distance_to_res": 0.5},
        100.0,
        "LONG",
        style_profile=profile,
    )

    assert clear["rr_threshold_met"] is False
    assert clear["rr_ok"] is True
    assert clear["passed"] is True
    assert "RR_BELOW_MINIMUM" in {
        item["code"] for item in clear["levelAdvisories"]
    }
    assert blocked["space_gate_ok"] is False
    assert blocked["passed"] is False


def test_risk_and_guardian_allow_advisory_rr_and_wide_sl_without_mutating_stop():
    signal = _engine_a_signal()
    original = dict(signal)

    assert clamp_execution_sl_to_max_sl_pct(signal, config.CONFIG) is False
    assert signal["sl"] == original["sl"]
    invariant_ok, reason = pre_trade_invariants(signal)
    assert invariant_ok is True, reason

    approval = risk_check(signal, 10000.0, 10000.0, [])
    assert approval.approved is True
    codes = {item["code"] for item in signal.get("levelAdvisories", [])}
    assert {"RR_BELOW_MINIMUM", "MAX_SL_EXCEEDED"} <= codes


def test_auto_trader_sl_precheck_is_advisory_for_engine_a():
    signal = _engine_a_signal()
    trader = object.__new__(AutoTrader)
    allowed, reason, detail = trader._sl_distance_precheck(
        signal,
        "crypto",
        config.CONFIG,
    )
    assert allowed is True
    assert reason == ""
    assert detail["advisory"] is True


def test_master_toggle_restores_blocks_while_wrong_side_always_fails(monkeypatch):
    wrong_side = _engine_a_signal(sl=105.0, tp1=110.0)
    ok, reason = pre_trade_invariants(wrong_side)
    assert ok is False
    assert "SL_ABOVE_ENTRY" in reason

    monkeypatch.setitem(config.CONFIG, "ENGINE_AB_PROFITABILITY_GATES_ENFORCED", True)
    enforced = risk_check(_engine_a_signal(), 10000.0, 10000.0, [])
    assert enforced.approved is False
    assert enforced.reason in {"RR_BELOW_MINIMUM", "MAX_SL_EXCEEDED"}


def test_m5_low_rr_is_advisory_but_missing_rr_still_fails_closed():
    kwargs = {
        "m5_policy": "conditional",
        "htf_structure_valid": True,
        "setup_armed": True,
        "session_or_participation_eligible": True,
        "current_spread": 0.1,
        "spread_threshold": 0.2,
        "distance_from_setup_atr": 0.2,
        "max_displacement_atr": 1.0,
        "entry_event": "break_retest",
        "trigger_expired": False,
        "structural_rr_at_current_price": 0.5,
        "min_rr": 1.5,
        "quote_inside_opposing_zone": False,
        "ltf_direction_agrees_with_thesis": True,
        "m5_validation_status": "approved",
        "profitability_gates_enforced": False,
    }
    advisory = evaluate_m5_eligibility(M5EligibilityContext(**kwargs))
    missing = evaluate_m5_eligibility(
        M5EligibilityContext(**{**kwargs, "structural_rr_at_current_price": None})
    )

    assert advisory.eligible is True
    assert missing.eligible is False
    assert "structural_rr_missing" in missing.reasons


def test_scan_config_exposes_one_profitability_gate_switch():
    root = Path(__file__).resolve().parents[1]
    panel = (
        root
        / "static"
        / "react-app"
        / "app"
        / "src"
        / "components"
        / "panels"
        / "ScanConfigPanel.tsx"
    ).read_text(encoding="utf-8")
    api = (root / "athena.py").read_text(encoding="utf-8")

    assert "profitability_gates?: boolean" in panel
    assert "Restore minimum RR and max-SL blocks for Engine A/B" in panel
    assert 'elif feature == "profitability_gates":' in api
    assert 'CONFIG["ENGINE_AB_PROFITABILITY_GATES_ENFORCED"] = new_val' in api
