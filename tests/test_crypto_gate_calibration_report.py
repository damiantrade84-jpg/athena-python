import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crypto_engine_b_gate_calibration import (
    FALLBACK_NOT_ALLOWED_GATE,
    _apply_fallback_pass_policy,
    generate_aggregate_stats,
)


def _detail(display, direction, failed_gates, final_passed=False, target=None):
    target = target or {}
    return {
        "display": display,
        "direction": direction,
        "confidence_score": 3.0,
        "threshold": 3.0,
        "structural_verdict": "CLEAR",
        "passed_all_gates": final_passed and not failed_gates,
        "passed_all_non_trigger_gates": not any(g != "trigger" for g in failed_gates),
        "final_engine_b_passed": final_passed,
        "trigger_failed": "trigger" in failed_gates,
        "rr_failed": any(str(g).startswith("rr=") or g == "rr_gate" for g in failed_gates),
        "production_pass_flag": final_passed,
        "debug_gate_failed_names": failed_gates,
        "target_integrity": {
            "final_rr": target.get("final_rr"),
            "used_true_h4_liquidity_target": target.get("used_true", False),
            "used_fallback_projection": target.get("used_fallback", False),
            "tp2_reject_reason": target.get("tp2_reject_reason"),
            "passed_because_of_true_h4_target": final_passed and target.get("used_true", False),
            "passed_because_of_fallback": final_passed and target.get("used_fallback", False),
        },
        "trigger_audit": {},
    }


def test_trigger_failures_cannot_pass_all_gates():
    details = [
        _detail("BTC/USDT", "LONG", ["trigger"]),
        _detail("BTC/USDT", "SHORT", ["trigger"]),
    ]

    stats = generate_aggregate_stats(details)

    assert stats["gate_failure_counts"]["trigger"] == 2
    assert stats["passed_all_gates_count"] == 0
    assert stats["final_engine_b_passed_count"] == 0
    assert stats["single_gate_failures"]["only_trigger"] == 2
    assert stats["consistency_errors"] == []


def test_target_integrity_separates_true_h4_from_fallback_passes():
    details = [
        _detail("ETH/USDT", "LONG", [], True, {"final_rr": 2.1, "used_true": True}),
        _detail("SOL/USDT", "LONG", [], True, {"final_rr": 4.0, "used_fallback": True}),
        _detail(
            "DOGE/USDT",
            "SHORT",
            ["rr=0.7"],
            False,
            {"final_rr": 0.7, "used_fallback": True, "tp2_reject_reason": "tp2_too_close_0.37atr"},
        ),
    ]

    stats = generate_aggregate_stats(details)
    integrity = stats["target_model_integrity"]

    assert integrity["true_h4_liquidity_tp2_count"] == 1
    assert integrity["fallback_2r_projection_count"] == 2
    assert integrity["rejected_because_tp2_invalid_count"] == 1
    assert integrity["passed_using_true_h4_liquidity_count"] == 1
    assert integrity["passed_using_fallback_count"] == 1
    assert integrity["rr_distribution_true_h4_liquidity_only"]["median"] == 2.1
    assert integrity["rr_distribution_fallback_only"]["count"] == 2


def test_fallback_not_allowed_policy_removes_fallback_pass():
    detail = _detail(
        "SOL/USDT",
        "SHORT",
        [],
        True,
        {"final_rr": 4.0, "used_fallback": True},
    )

    filtered = _apply_fallback_pass_policy(detail, allow_fallback_target_for_pass=False)
    stats = generate_aggregate_stats([filtered])

    assert filtered["final_engine_b_passed"] is False
    assert filtered["production_pass_flag"] is False
    assert FALLBACK_NOT_ALLOWED_GATE in filtered["debug_gate_failed_names"]
    assert stats["final_engine_b_passed_count"] == 0
    assert stats["target_model_integrity"]["passed_using_fallback_count"] == 0
