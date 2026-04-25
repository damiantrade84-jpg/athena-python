"""
Crypto Target Model Validation Report.

Consumes logs/crypto_engine_b_gate_calibration.json and summarizes only
internally consistent, report-only evidence. This script intentionally does not
recommend trigger tuning, RR reductions, or enabling the crypto target model.
"""

import json
from pathlib import Path


def generate_comparison_report():
    report_path = Path(__file__).parent / "logs" / "crypto_engine_b_gate_calibration.json"
    with open(report_path, encoding="utf-8") as f:
        current_report = json.load(f)

    stats = current_report.get("aggregate_stats", {})
    integrity = stats.get("target_model_integrity", {})
    pair_details = current_report.get("pair_details", [])

    validation = {
        "comparison_timestamp": current_report.get("audit_timestamp"),
        "source_report": str(report_path),
        "scope": "validation_only_no_tuning",
        "gate_consistency": {
            "total_directions": stats.get("total_directions", 0),
            "trigger_failures": stats.get("gate_failure_counts", {}).get("trigger", 0),
            "passed_all_gates": stats.get("passed_all_gates_count", 0),
            "passed_all_non_trigger_gates": stats.get("passed_all_non_trigger_gates_count", 0),
            "final_engine_b_passed": stats.get("final_engine_b_passed_count", 0),
            "production_pass_flag": stats.get("production_pass_flag_count", 0),
            "consistency_errors": stats.get("consistency_errors", []),
        },
        "target_model_integrity": integrity,
        "passing_directions": [
            {
                "display": d.get("display"),
                "direction": d.get("direction"),
                "confidence_score": d.get("confidence_score"),
                "threshold": d.get("threshold"),
                "debug_gate_failed_names": d.get("debug_gate_failed_names"),
                "target_integrity": d.get("target_integrity"),
                "trigger_audit": d.get("trigger_audit"),
            }
            for d in pair_details
            if d.get("final_engine_b_passed")
        ],
        "single_trigger_failure_directions": [
            {
                "display": d.get("display"),
                "direction": d.get("direction"),
                "passed_all_non_trigger_gates": d.get("passed_all_non_trigger_gates"),
                "debug_gate_failed_names": d.get("debug_gate_failed_names"),
                "target_integrity": d.get("target_integrity"),
                "trigger_audit": d.get("trigger_audit"),
            }
            for d in pair_details
            if d.get("debug_gate_failed_names") == ["trigger"]
        ],
        "explicit_non_recommendations": [
            "No trigger logic change is recommended by this validation report.",
            "No RR reduction is recommended by this validation report.",
            "No default enablement of ENGINE_B_CRYPTO_TARGET_MODEL_ENABLED is recommended by this validation report.",
        ],
    }

    out_path = Path(__file__).parent / "logs" / "crypto_target_model_comparison.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(validation, f, indent=2)

    print("\n" + "=" * 80)
    print("CRYPTO TARGET MODEL VALIDATION REPORT")
    print("=" * 80)
    gate = validation["gate_consistency"]
    print(f"Total directions: {gate['total_directions']}")
    print(f"Trigger failures: {gate['trigger_failures']}")
    print(f"Passed all gates: {gate['passed_all_gates']}")
    print(f"Passed all non-trigger gates: {gate['passed_all_non_trigger_gates']}")
    print(f"Final Engine B passed: {gate['final_engine_b_passed']}")
    print(f"Production pass flag: {gate['production_pass_flag']}")
    if gate["consistency_errors"]:
        print("Consistency errors:")
        for err in gate["consistency_errors"]:
            print(f"  {err}")
    else:
        print("Consistency check: OK")

    print("\nTarget model integrity:")
    print(f"  true H4 liquidity TP2: {integrity.get('true_h4_liquidity_tp2_count')}")
    print(f"  fallback projection: {integrity.get('fallback_2r_projection_count')}")
    print(f"  TP2 invalid rejection: {integrity.get('rejected_because_tp2_invalid_count')}")
    print(f"  passed using true H4 liquidity: {integrity.get('passed_using_true_h4_liquidity_count')}")
    print(f"  passed using fallback: {integrity.get('passed_using_fallback_count')}")
    print(f"\nValidation report saved to: {out_path}")
    print("=" * 80)
    return validation


if __name__ == "__main__":
    generate_comparison_report()
