"""Test close with different filling modes to see if it fixes price recording."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect, _SYMBOL_FILLING_MODES

mt5_connect()

# Test closing a dummy position with different filling modes
# (We'll just inspect the current filling mode behavior)
print("Current filling modes:")
for sym, mode in _SYMBOL_FILLING_MODES.items():
    print(f"  {sym}: {mode}")

# Check what filling modes are available
print("\nAvailable MT5 filling modes:")
print(f"  IOC: {mt5.ORDER_FILLING_IOC}")
print(f"  FOK: {mt5.ORDER_FILLING_FOK}")
print(f"  RETURN: {mt5.ORDER_FILLING_RETURN}")

# Check current open positions for patterns
from mt5_executor import mt5_get_positions
pos_result = mt5_get_positions()
if not pos_result.get("error") and pos_result.get("positions"):
    print("\nCurrent open positions:")
    for pos in pos_result["positions"][:5]:
        print(f"  {pos['pair']} ticket={pos['ticket']} entry={pos['entry']} profit={pos['profit']}")

# Check recent deals for price anomalies
from datetime import datetime, timezone, timedelta
from_dt = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
to_dt = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
deals = mt5.history_deals_get(from_dt, to_dt)
if deals:
    print("\nRecent deals (last 24h) - checking for price anomalies:")
    for d in sorted(deals, key=lambda x: x.time)[-10:]:
        # Look for deals where price equals typical entry prices (same as previous deal)
        dt = datetime.fromtimestamp(d.time, tz=timezone.utc)
        print(f"  {dt.strftime('%H:%M:%S')} {d.symbol} deal={d.ticket} type={d.type} price={d.price} profit={d.profit}")
else:
    print("\nNo deals in last 24h")
