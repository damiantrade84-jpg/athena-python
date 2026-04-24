#!/usr/bin/env python3
"""Generate markdown summary from live feed diagnostic snapshot."""
import json
import sys
from datetime import datetime
from pathlib import Path


def main():
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1])
    else:
        input_path = Path(__file__).parent.parent / "docs" / "diagnostics" / "latest_live_feed_snapshot.json"
    
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    rows = data.get("diagnostics", [])
    
    output_path = input_path.parent / (input_path.stem + ".md")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Live Feed Diagnostic Snapshot\n\n")
        f.write(f"**Generated:** {data.get('generatedAt', 'Unknown')}\n")
        f.write(f"**Total Rows:** {len(rows)}\n\n")
        
        # Summary table
        f.write("## Summary\n\n")
        f.write("| Symbol | TF | Provider Status | Consistency Status | Gate Decision |\n")
        f.write("|--------|----|----------------|-------------------|---------------|\n")
        
        for row in rows:
            symbol = row.get("symbol", "N/A")
            tf = row.get("timeframe", "N/A")
            provider_status = row.get("providerStatus", "N/A")
            consistency = row.get("consistency", {})
            consistency_status = consistency.get("status", "N/A") if isinstance(consistency, dict) else consistency
            
            # Determine gate decision
            if consistency_status in ("OK", "CONFIRMED_ONLY_OK"):
                gate_decision = "ALLOW"
            elif consistency_status.startswith("ERROR_") or consistency_status == "provider_error":
                gate_decision = "BLOCK"
            elif consistency_status == "WARNING_ONE_BUCKET_LAG":
                gate_decision = "BLOCK"
            else:
                gate_decision = "UNKNOWN"
            
            f.write(f"| {symbol} | {tf} | {provider_status} | {consistency_status} | {gate_decision} |\n")
        
        # Detailed table for each row
        f.write("\n## Detailed Diagnostics\n\n")
        
        for row in rows:
            symbol = row.get("symbol", "N/A")
            tf = row.get("timeframe", "N/A")
            provider_status = row.get("providerStatus", "N/A")
            provider_error = row.get("providerError")
            raw_status = row.get("stalenessSeverity", "N/A")
            raw_latest_iso = row.get("lastBarIso", "N/A")
            expected_current_iso = row.get("expectedCurrentBucketIso", "N/A")
            
            consistency = row.get("consistency", {})
            consistency_status = consistency.get("status", "N/A") if isinstance(consistency, dict) else consistency
            consistency_reason = consistency.get("reason", "") if isinstance(consistency, dict) else ""
            
            engine_policies = consistency.get("enginePolicies", {}) if isinstance(consistency, dict) else {}
            
            engine_a = engine_policies.get("engine_a", {})
            engine_b = engine_policies.get("engine_b", {})
            scanner = engine_policies.get("scanner", {})
            compare = engine_policies.get("compare", {})
            
            # Calculate expected confirmed bucket
            raw_latest_epoch = row.get("lastBarEpoch")
            expected_confirmed_iso = "N/A"
            if raw_latest_epoch:
                # H1 = 3600, H4 = 14400, D1 = 86400
                tf_seconds = {"H1": 3600, "H4": 14400, "D1": 86400}.get(tf, 3600)
                expected_confirmed_epoch = raw_latest_epoch - tf_seconds
                expected_confirmed_iso = datetime.utcfromtimestamp(expected_confirmed_epoch).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            # Determine gate decision
            if consistency_status in ("OK", "CONFIRMED_ONLY_OK"):
                gate_decision = "ALLOW"
                block_reason = ""
            elif consistency_status.startswith("ERROR_") or consistency_status == "provider_error":
                gate_decision = "BLOCK"
                block_reason = consistency_status
            elif consistency_status == "WARNING_ONE_BUCKET_LAG":
                gate_decision = "BLOCK"
                block_reason = "True one-bucket lag"
            else:
                gate_decision = "UNKNOWN"
                block_reason = ""
            
            f.write(f"### {symbol} - {tf}\n\n")
            f.write("| Field | Value |\n")
            f.write("|-------|-------|\n")
            f.write(f"| symbol | {symbol} |\n")
            f.write(f"| timeframe | {tf} |\n")
            f.write(f"| providerStatus | {provider_status} |\n")
            f.write(f"| rawStatus | {raw_status} |\n")
            f.write(f"| rawLatestBarIso | {raw_latest_iso} |\n")
            f.write(f"| expectedCurrentBucketIso | {expected_current_iso} |\n")
            f.write(f"| expectedConfirmedBucketIso | {expected_confirmed_iso} |\n")
            f.write(f"| consistencyStatus | {consistency_status} |\n")
            f.write(f"| engine_a policy | {engine_a.get('policy', 'N/A')} |\n")
            f.write(f"| engine_a policyStatus | {engine_a.get('policyStatus', 'N/A')} |\n")
            f.write(f"| engine_b policy | {engine_b.get('policy', 'N/A')} |\n")
            f.write(f"| engine_b policyStatus | {engine_b.get('policyStatus', 'N/A')} |\n")
            f.write(f"| scanner policyStatus | {scanner.get('policyStatus', 'N/A')} |\n")
            f.write(f"| compare policyStatus | {compare.get('policyStatus', 'N/A')} |\n")
            f.write(f"| gateDecision | {gate_decision} |\n")
            f.write(f"| blockReason | {block_reason} |\n")
            f.write(f"| reason | {consistency_reason} |\n")
            f.write("\n")
    
    print(f"Generated markdown summary at {output_path}")

if __name__ == "__main__":
    main()
