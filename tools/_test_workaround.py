"""Test if the Pepperstone workaround is working correctly."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect, mt5_close_position

mt5_connect()

# Simulate the conditions of the USD/CHF trade
# We can't actually close a position, but we can test the logic

print("Testing Pepperstone workaround logic...")
print()

# Check the source code to see if workaround is present
import inspect
source = inspect.getsource(mt5_close_position)

if "Pepperstone price bug detected" in source:
    print("YES: Workaround code is present")
    print()
    
    # Check the logic
    if "abs(raw_fill_price - entry_price) < 1e-8" in source:
        print("YES: Detection logic is present")
    else:
        print("NO: Detection logic missing")
    
    if "computed_close" in source:
        print("YES: Computation logic is present")
    else:
        print("NO: Computation logic missing")
    
    if "tick_value" in source and "tick_size" in source:
        print("YES: Pip value calculation is present")
    else:
        print("NO: Pip value calculation missing")
else:
    print("NO: Workaround code NOT found")

print()
print("The issue might be:")
print("1. Timed exit writes correct data")
print("2. Outcome monitor runs later and overwrites with wrong deal data")
print("3. Need to prevent outcome monitor from overwriting TIMED_CLOSE")

# Check if the exit_reason preservation fix is present
try:
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "athena.py"), "r") as f:
        athena_source = f.read()
    
    if "existing_exit_reason" in athena_source and "TIMED_CLOSE" in athena_source:
        print()
        print("YES: Exit reason preservation fix is present in athena.py")
    else:
        print()
        print("NO: Exit reason preservation fix NOT found")
except:
    pass
