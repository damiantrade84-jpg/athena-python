from execution import _stamp_engine_b_execution_plan
from risk_engine import _engine_b_resolver_plan_for_risk


def test_quick_execute_stamps_crypto_scale_out_provenance_for_risk() -> None:
    signal = {
        "engine": "engine_b",
        "type": "crypto",
        "direction": "LONG",
        "price": 100.0,
        "tp1": 105.0,
        "tp2": 110.0,
    }
    resolver = {
        "execution_plan": "scale_out",
        "rr_passed": True,
        "rr_required": 1.5,
        "execution_sl": 95.0,
        "execution_tp": 110.0,
        "execution_tp1": 105.0,
        "execution_tp2": 110.0,
    }
    levels = {
        "execution_plan": "scale_out",
        "execution_plan_reason": "crypto_scale_out_supported",
    }

    _stamp_engine_b_execution_plan(signal, resolver, levels)

    assert signal["engine_b_execution_tp1"] == 105.0
    assert signal["engine_b_execution_tp2"] == 110.0
    assert signal["engine_b_execution_tp"] == 110.0
    assert _engine_b_resolver_plan_for_risk(signal, 100.0, "crypto") == (
        "scale_out",
        1.5,
        110.0,
    )
