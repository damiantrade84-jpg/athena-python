
import sys
import os
import logging

# Ensure project root is in path
sys.path.insert(0, os.getcwd())

import risk_engine
from risk_engine import risk_check, RiskApproval
from config import CONFIG

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_repro_bug4():
    print("Testing Repro BUG 4 — MAX_SL_PCT Enforcement...")
    
    # Mocking standard CONFIG to ensure we know the thresholds
    CONFIG["MAX_SL_PCT"] = {
        "forex": 0.025,    # 2.5%
        "crypto": 0.08,     # 8%
        "commodity": 0.04,  # 4%
        "stock": 0.08,      # 8%
        "index": 0.04       # 4%
    }
    
    # 1. Forex test (Max is 2.5%). Let's try 3% stop.
    # Price 1.10, SL 1.067 (Approx 3%)
    entry = 1.10
    sl = 1.067 # (1.10 - 1.067) / 1.10 = 0.03
    print(f"CASE 1: Forex pair, 3% SL. Max allowed: 2.5%")
    signal = {
        "pair": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "price": entry,
        "sl": sl,
        "tp1": 1.15,
        "tp2": 1.18,
        "timestamp": "2026-04-01T12:00:00Z"
    }
    
    res = risk_check(
        signal=signal,
        account_balance=100000.0,
        account_equity=100000.0,
        open_positions=[],
        sizing_override=1.0
    )
    
    print(f"  Approved: {res.approved}, Reason: {res.reason}")
    if res.approved:
        print("!! REPRODUCED: Forex 3% SL was APPROVED but should be REJECTED (max 2.5%)")
    else:
        print("  Correctly REJECTED.")

    # 2. Crypto test (Max 8%). Try 10% stop.
    entry = 60000
    sl = 54000 # 10%
    print(f"\nCASE 2: Crypto pair, 10% SL. Max allowed: 8%")
    signal_crypto = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "price": entry,
        "sl": sl,
        "tp1": 70000,
        "tp2": 80000,
        "timestamp": "2026-04-01T12:00:00Z"
    }
    res_crypto = risk_check(
        signal=signal_crypto,
        account_balance=100000.0,
        account_equity=100000.0,
        open_positions=[],
        sizing_override=1.0
    )
    print(f"  Approved: {res_crypto.approved}, Reason: {res_crypto.reason}")
    if res_crypto.approved:
        print("!! REPRODUCED: Crypto 10% SL was APPROVED but should be REJECTED (max 8%)")
    else:
        print("  Correctly REJECTED.")

if __name__ == "__main__":
    test_repro_bug4()
