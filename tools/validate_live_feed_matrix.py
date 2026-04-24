#!/usr/bin/env python3
"""Validate live feed diagnostic matrix before paper trading."""
import argparse
import json
import sys
from pathlib import Path


REQUIRED_SYMBOLS = ["EURUSD", "GBPUSD", "BTCUSDT", "ETHUSDT"]
REQUIRED_TIMEFRAMES = ["H1", "H4", "D1"]


def validate_row(row: dict, symbol: str, timeframe: str) -> list[str]:
    """Validate a single diagnostic row. Returns list of error messages."""
    errors = []
    
    # Check provider status
    provider_status = row.get("providerStatus")
    if provider_status != "ok":
        errors.append(f"providerStatus is '{provider_status}' (expected 'ok')")
    
    # Check consistency status
    consistency = row.get("consistency", {})
    if not isinstance(consistency, dict):
        errors.append("consistency is not a dict")
        return errors
    
    consistency_status = consistency.get("status")
    
    # Failing statuses
    if consistency_status in ("ERROR_STALE_MULTI_BUCKET", "ERROR_PATH_MISMATCH", 
                                "ERROR_OFFSET_MISMATCH", "provider_error"):
        errors.append(f"consistency status is '{consistency_status}' (blocking)")
    elif consistency_status == "WARNING_ONE_BUCKET_LAG":
        errors.append(f"consistency status is '{consistency_status}' (blocking)")
    elif consistency_status not in ("OK", "CONFIRMED_ONLY_OK"):
        errors.append(f"consistency status is '{consistency_status}' (unknown)")
    
    # Check engine policies
    engine_policies = consistency.get("enginePolicies", {})
    if not isinstance(engine_policies, dict):
        errors.append("enginePolicies is not a dict")
        return errors
    
    for path_name in ["engine_a", "engine_b", "scanner", "compare"]:
        path_policy = engine_policies.get(path_name)
        if not isinstance(path_policy, dict):
            errors.append(f"{path_name} policy is not a dict")
            continue
        
        policy_status = path_policy.get("policyStatus")
        if policy_status != "POLICY_OK":
            errors.append(f"{path_name} policyStatus is '{policy_status}' (expected 'POLICY_OK')")
    
    # Determine gate decision
    if consistency_status in ("OK", "CONFIRMED_ONLY_OK"):
        gate_decision = "ALLOW"
    elif consistency_status.startswith("ERROR_") or consistency_status == "provider_error":
        gate_decision = "BLOCK"
    elif consistency_status == "WARNING_ONE_BUCKET_LAG":
        gate_decision = "BLOCK"
    else:
        gate_decision = "UNKNOWN"
    
    if gate_decision != "ALLOW":
        errors.append(f"gate decision is '{gate_decision}' (expected 'ALLOW')")
    
    return errors


def validate_matrix(data: dict) -> tuple[bool, list[str]]:
    """Validate the full diagnostic matrix. Returns (is_valid, errors)."""
    errors = []
    
    diagnostics = data.get("diagnostics", [])
    
    # Build matrix of symbol -> timeframe -> row
    matrix = {}
    for row in diagnostics:
        symbol = row.get("symbol", "").replace("=X", "")  # Remove MT5 suffix
        timeframe = row.get("timeframe")
        
        if symbol not in matrix:
            matrix[symbol] = {}
        
        matrix[symbol][timeframe] = row
    
    # Check all required symbols and timeframes
    missing_rows = []
    for symbol in REQUIRED_SYMBOLS:
        if symbol not in matrix:
            errors.append(f"Missing symbol: {symbol}")
            continue
        
        for timeframe in REQUIRED_TIMEFRAMES:
            if timeframe not in matrix[symbol]:
                missing_rows.append(f"{symbol}/{timeframe}")
    
    if missing_rows:
        errors.append(f"Missing rows: {', '.join(missing_rows)}")
    
    # Validate each present row
    for symbol in REQUIRED_SYMBOLS:
        if symbol not in matrix:
            continue
        
        for timeframe in REQUIRED_TIMEFRAMES:
            if timeframe not in matrix[symbol]:
                continue
            
            row = matrix[symbol][timeframe]
            row_errors = validate_row(row, symbol, timeframe)
            if row_errors:
                errors.append(f"{symbol}/{timeframe}: {', '.join(row_errors)}")
    
    return len(errors) == 0, errors


def main():
    parser = argparse.ArgumentParser(description="Validate live feed diagnostic matrix")
    parser.add_argument("--input", required=True, help="Path to diagnostic JSON file")
    args = parser.parse_args()
    
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    is_valid, errors = validate_matrix(data)
    
    if is_valid:
        print("✓ Live feed matrix validation PASSED")
        print(f"  All required symbols: {', '.join(REQUIRED_SYMBOLS)}")
        print(f"  All required timeframes: {', '.join(REQUIRED_TIMEFRAMES)}")
        print(f"  Total rows validated: {len(data.get('diagnostics', []))}")
        sys.exit(0)
    else:
        print("✗ Live feed matrix validation FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
