"""Check when the problematic trade occurred vs when fixes were deployed."""
import os, sqlite3
from datetime import datetime, timezone

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# Check the USD/CHF trade timing
row = con.execute("SELECT ts, exit_time FROM audit_log WHERE id=1180").fetchone()
if row:
    print(f"USD/CHF trade 1180:")
    print(f"  Opened: {row['ts']}")
    print(f"  Closed: {row['exit_time']}")
    
    if row['ts']:
        try:
            dt_open = datetime.fromisoformat(row['ts'].replace('Z', '+00:00'))
            print(f"  Opened at: {dt_open.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        except:
            pass
    
    if row['exit_time']:
        try:
            # Check if exit_time is a timestamp or ISO
            if row['exit_time'].isdigit():
                dt_close = datetime.fromtimestamp(int(row['exit_time']), tz=timezone.utc)
            else:
                dt_close = datetime.fromisoformat(row['exit_time'].replace('Z', '+00:00'))
            print(f"  Closed at: {dt_close.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        except:
            pass

# Check when our fix was committed
print("\nFix commit timing:")
print("  Fixes were committed on: 2026-04-16 ~13:00 UTC")
print("  If trade closed before this time, the fix wouldn't apply")

# Check if the workaround is being used in current code
print("\nChecking if workaround code is present:")
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mt5_executor import mt5_close_position
    import inspect
    
    source = inspect.getsource(mt5_close_position)
    if "Pepperstone price bug detected" in source:
        print("  YES: Workaround code is present in mt5_close_position")
    else:
        print("  NO: Workaround code NOT found")
except Exception as e:
    print(f"  Error checking: {e}")
