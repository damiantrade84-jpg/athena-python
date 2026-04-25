"""
Crypto Engine B Gate Calibration Audit
Analyzes why Engine B crypto signals are being rejected by hard gates.
Extracts detailed gate data and runs sensitivity checks.
"""

import sys
import json
import logging
import requests
from datetime import datetime
from pathlib import Path
from statistics import median, quantiles

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import CONFIG
import importlib.util

# Load athena.py as a module directly
spec = importlib.util.spec_from_file_location("athena_module", "athena.py")
athena_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(athena_module)

# Get CRYPTO_PAIRS from the loaded module
CRYPTO_PAIRS = athena_module.CRYPTO_PAIRS

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger("crypto_gate_calibration")

# Get enabled crypto pairs
ENABLED_CRYPTO = [p for p in CRYPTO_PAIRS if p.get("enabled", True)]

API_BASE = "http://localhost:5000"

def run_gate_calibration_audit():
    """Run Engine B gate calibration audit for crypto pairs."""
    log.info(f"Starting Crypto Engine B Gate Calibration Audit for {len(ENABLED_CRYPTO)} enabled pairs")
    
    report = {
        "audit_timestamp": datetime.utcnow().isoformat(),
        "total_pairs": len(ENABLED_CRYPTO),
        "pair_details": [],
        "aggregate_stats": {},
        "sensitivity_checks": {}
    }
    
    # Call /api/scan-naked with debug mode to get detailed data
    log.info("Calling /api/scan-naked with debug mode...")
    try:
        response = requests.post(
            f"{API_BASE}/api/scan-naked",
            json={
                "assetClass": "crypto",
                "style": "auto",
                "debug": True
            },
            timeout=120
        )
        
        if response.status_code != 200:
            log.error(f"API returned {response.status_code}")
            return report
        
        data = response.json()
        debug_rows = data.get("debugRows", [])
        log.info(f"Received {len(debug_rows)} debug rows from API")
        
    except requests.exceptions.RequestException as e:
        log.error(f"API request failed: {e}")
        return report
    
    # Process each debug row to extract detailed gate data
    for debug_row in debug_rows:
        pair_display = debug_row.get("pair")
        symbol = debug_row.get("symbol")
        
        if not pair_display:
            continue
        
        # Extract data for both LONG and SHORT
        for direction in ["LONG", "SHORT"]:
            if direction == "LONG":
                confidence_score = debug_row.get("long_confidence_score", 0)
                passed = debug_row.get("long_passed", False)
                structural_verdict = debug_row.get("long_structural_verdict")
            else:
                confidence_score = debug_row.get("short_confidence_score", 0)
                passed = debug_row.get("short_passed", False)
                structural_verdict = debug_row.get("short_structural_verdict")
            
            failed_gates = debug_row.get("failed_gate_names", [])
            final_reject_reason = debug_row.get("final_reject_reason", "unknown")
            
            # Get candle and ATR data
            atr_value = debug_row.get("atr_value", 0)
            zone_count = debug_row.get("zone_count", 0)
            entry_count = debug_row.get("entry_count", 0)
            d1_count = debug_row.get("d1_count", 0)
            
            # Extract detailed gate information from the failed_gates list
            # The failed_gates contains: loc, trigger, struct, rr=X
            rr_value = None
            for gate in failed_gates:
                if gate.startswith("rr="):
                    try:
                        rr_value = float(gate.split("=")[1])
                    except:
                        pass
            
            pair_detail = {
                "symbol": symbol,
                "display": pair_display,
                "direction": direction,
                "confidence_score": confidence_score,
                "threshold": 2.4,  # Default threshold for crypto
                "structural_verdict": structural_verdict,
                "passed": passed,
                "failed_gates": failed_gates,
                "final_reject_reason": final_reject_reason,
                "rr_value": rr_value,
                "atr_value": atr_value,
                "candle_counts": {
                    "d1": d1_count,
                    "zone": zone_count,
                    "entry": entry_count
                },
                "style_profile": debug_row.get("style"),
                "timeframes": {
                    "zone_tf": debug_row.get("zone_tf"),
                    "entry_tf": debug_row.get("entry_tf"),
                    "atr_tf": debug_row.get("atr_tf")
                },
                # Crypto target model debug fields
                "crypto_target_old_target": debug_row.get("crypto_target_old_target"),
                "crypto_target_old_rr": debug_row.get("crypto_target_old_rr"),
                "crypto_target_tp1": debug_row.get("crypto_target_tp1"),
                "crypto_target_tp2": debug_row.get("crypto_target_tp2"),
                "crypto_target_new_rr": debug_row.get("crypto_target_new_rr"),
                "crypto_target_selection_mode": debug_row.get("crypto_target_selection_mode"),
                "crypto_target_reject_reason": debug_row.get("crypto_target_reject_reason"),
                "crypto_target_path_clear_to_tp2": debug_row.get("crypto_target_path_clear_to_tp2"),
                "crypto_target_atr_multiple": debug_row.get("crypto_target_atr_multiple")
            }
            
            report["pair_details"].append(pair_detail)
    
    # Generate aggregate statistics
    report["aggregate_stats"] = generate_aggregate_stats(report["pair_details"])
    
    # Run sensitivity checks
    report["sensitivity_checks"] = run_sensitivity_checks(report["pair_details"])
    
    # Save report
    report_path = Path(__file__).parent / "logs" / "crypto_engine_b_gate_calibration.json"
    report_path.parent.mkdir(exist_ok=True)
    
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    
    log.info(f"Calibration report saved to {report_path}")
    
    # Print summary
    print_calibration_summary(report)
    
    return report

def generate_aggregate_stats(pair_details):
    """Generate aggregate statistics from pair details."""
    stats = {
        "total_directions": len(pair_details),
        "gate_failure_counts": {
            "loc": 0,
            "trigger": 0,
            "struct": 0,
            "rr": 0
        },
        "single_gate_failures": {
            "only_loc": 0,
            "only_trigger": 0,
            "only_struct": 0,
            "only_rr": 0
        },
        "multi_gate_failures": {
            "loc_trigger": 0,
            "loc_struct": 0,
            "loc_rr": 0,
            "trigger_struct": 0,
            "trigger_rr": 0,
            "struct_rr": 0
        },
        "score_above_threshold_but_failed": 0,
        "structural_clear_but_failed": 0,
        "rr_distribution": {},
        "score_distribution": {}
    }
    
    rr_values = []
    score_values = []
    
    for detail in pair_details:
        failed_gates = detail["failed_gates"]
        score = detail["confidence_score"]
        structural_verdict = detail["structural_verdict"]
        rr_value = detail["rr_value"]
        
        # Count gate failures
        if "loc" in failed_gates:
            stats["gate_failure_counts"]["loc"] += 1
        if "trigger" in failed_gates:
            stats["gate_failure_counts"]["trigger"] += 1
        if "struct" in failed_gates:
            stats["gate_failure_counts"]["struct"] += 1
        if any(g.startswith("rr=") for g in failed_gates):
            stats["gate_failure_counts"]["rr"] += 1
        
        # Count single gate failures
        if len(failed_gates) == 1:
            if failed_gates[0] == "loc":
                stats["single_gate_failures"]["only_loc"] += 1
            elif failed_gates[0] == "trigger":
                stats["single_gate_failures"]["only_trigger"] += 1
            elif failed_gates[0] == "struct":
                stats["single_gate_failures"]["only_struct"] += 1
            elif failed_gates[0].startswith("rr="):
                stats["single_gate_failures"]["only_rr"] += 1
        
        # Count multi-gate failures
        if "loc" in failed_gates and "trigger" in failed_gates:
            stats["multi_gate_failures"]["loc_trigger"] += 1
        if "loc" in failed_gates and "struct" in failed_gates:
            stats["multi_gate_failures"]["loc_struct"] += 1
        if "loc" in failed_gates and any(g.startswith("rr=") for g in failed_gates):
            stats["multi_gate_failures"]["loc_rr"] += 1
        if "trigger" in failed_gates and "struct" in failed_gates:
            stats["multi_gate_failures"]["trigger_struct"] += 1
        if "trigger" in failed_gates and any(g.startswith("rr=") for g in failed_gates):
            stats["multi_gate_failures"]["trigger_rr"] += 1
        if "struct" in failed_gates and any(g.startswith("rr=") for g in failed_gates):
            stats["multi_gate_failures"]["struct_rr"] += 1
        
        # Score above threshold but failed
        if score >= 2.4 and not detail["passed"]:
            stats["score_above_threshold_but_failed"] += 1
        
        # Structural CLEAR but failed
        if structural_verdict == "CLEAR" and not detail["passed"]:
            stats["structural_clear_but_failed"] += 1
        
        # Collect RR and score values for distribution
        if rr_value is not None:
            rr_values.append(rr_value)
        score_values.append(score)
    
    # Calculate RR distribution
    if rr_values:
        rr_values_sorted = sorted(rr_values)
        stats["rr_distribution"] = {
            "min": min(rr_values),
            "p25": rr_values_sorted[int(len(rr_values) * 0.25)],
            "median": median(rr_values),
            "p75": rr_values_sorted[int(len(rr_values) * 0.75)],
            "max": max(rr_values),
            "count": len(rr_values)
        }
    
    # Calculate score distribution
    if score_values:
        score_values_sorted = sorted(score_values)
        stats["score_distribution"] = {
            "min": min(score_values),
            "p25": score_values_sorted[int(len(score_values) * 0.25)],
            "median": median(score_values),
            "p75": score_values_sorted[int(len(score_values) * 0.75)],
            "max": max(score_values),
            "count": len(score_values)
        }
    
    return stats

def run_sensitivity_checks(pair_details):
    """Run sensitivity checks to see what would pass with different thresholds."""
    checks = {
        "rr_1_5": {"description": "Crypto RR minimum 1.5", "would_pass": 0},
        "rr_1_2": {"description": "Crypto RR minimum 1.2", "would_pass": 0},
        "trigger_m15": {"description": "Trigger gate allows M15 confirmation", "would_pass": 0},
        "trigger_m5_m15": {"description": "Trigger gate allows M5/M15 confirmation", "would_pass": 0},
        "location_atr_buffer": {"description": "Location tolerance allows ATR buffer", "would_pass": 0},
        "trend_continuation_relaxed": {"description": "Trend-continuation setups don't require perfect pullback", "would_pass": 0},
        "target_h4_liquidity": {"description": "Target selection uses next major H4 liquidity", "would_pass": 0}
    }
    
    for detail in pair_details:
        failed_gates = detail["failed_gates"]
        rr_value = detail["rr_value"]
        score = detail["confidence_score"]
        structural_verdict = detail["structural_verdict"]
        
        # Check A: RR minimum 1.5
        if rr_value is not None and rr_value >= 1.5:
            # Would pass if only RR was the issue
            if len(failed_gates) == 1 and failed_gates[0].startswith("rr="):
                checks["rr_1_5"]["would_pass"] += 1
        
        # Check B: RR minimum 1.2
        if rr_value is not None and rr_value >= 1.2:
            if len(failed_gates) == 1 and failed_gates[0].startswith("rr="):
                checks["rr_1_2"]["would_pass"] += 1
        
        # Check C: Trigger gate allows M15 confirmation
        # This is a hypothetical - we can't actually test without M15 data
        # But we can count how many fail only on trigger
        if len(failed_gates) == 1 and failed_gates[0] == "trigger":
            checks["trigger_m15"]["would_pass"] += 1
        
        # Check D: Trigger gate allows M5/M15 confirmation
        if len(failed_gates) == 1 and failed_gates[0] == "trigger":
            checks["trigger_m5_m15"]["would_pass"] += 1
        
        # Check E: Location tolerance allows ATR buffer
        if len(failed_gates) == 1 and failed_gates[0] == "loc":
            checks["location_atr_buffer"]["would_pass"] += 1
        
        # Check F: Trend-continuation relaxed
        if len(failed_gates) == 1 and failed_gates[0] == "loc":
            checks["trend_continuation_relaxed"]["would_pass"] += 1
        
        # Check G: Target selection uses H4 liquidity
        if rr_value is not None and rr_value < 1.0:
            # If RR is low due to target being too close, using H4 liquidity might help
            checks["target_h4_liquidity"]["would_pass"] += 1
    
    return checks

def print_calibration_summary(report):
    """Print a summary of the calibration report."""
    print("\n" + "="*80)
    print("CRYPTO ENGINE B GATE CALIBRATION AUDIT SUMMARY")
    print("="*80)
    
    stats = report["aggregate_stats"]
    
    print(f"\nTotal directions analyzed: {stats['total_directions']}")
    
    print("\n--- Gate Failure Counts ---")
    for gate, count in stats["gate_failure_counts"].items():
        print(f"  {gate:10s}: {count}")
    
    print("\n--- Single Gate Failures ---")
    for gate, count in stats["single_gate_failures"].items():
        print(f"  {gate:15s}: {count}")
    
    print("\n--- Multi-Gate Failures ---")
    for combo, count in stats["multi_gate_failures"].items():
        if count > 0:
            print(f"  {combo:20s}: {count}")
    
    print(f"\n--- Score Above Threshold (2.4) But Failed: {stats['score_above_threshold_but_failed']}")
    print(f"--- Structural CLEAR But Failed: {stats['structural_clear_but_failed']}")
    
    if stats["rr_distribution"]:
        print("\n--- RR Distribution ---")
        rr_dist = stats["rr_distribution"]
        print(f"  Min: {rr_dist['min']:.2f}")
        print(f"  P25: {rr_dist['p25']:.2f}")
        print(f"  Median: {rr_dist['median']:.2f}")
        print(f"  P75: {rr_dist['p75']:.2f}")
        print(f"  Max: {rr_dist['max']:.2f}")
    
    if stats["score_distribution"]:
        print("\n--- Score Distribution ---")
        score_dist = stats["score_distribution"]
        print(f"  Min: {score_dist['min']:.2f}")
        print(f"  P25: {score_dist['p25']:.2f}")
        print(f"  Median: {score_dist['median']:.2f}")
        print(f"  P75: {score_dist['p75']:.2f}")
        print(f"  Max: {score_dist['max']:.2f}")
    
    print("\n--- Sensitivity Checks ---")
    for check_name, check_data in report["sensitivity_checks"].items():
        print(f"  {check_data['description']:50s}: {check_data['would_pass']} would pass")
    
    print("\n" + "="*80)

if __name__ == "__main__":
    run_gate_calibration_audit()
