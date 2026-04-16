"""Check what time period the USD/CHF candles cover."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

mt5_connect()

symbol = "USDCHF"

# Get last 100 candles
candles = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 100)
if candles is not None and len(candles) > 0:
    print(f"Got {len(candles)} candles")
    print(f"\nTime range:")
    first_time = datetime.fromtimestamp(candles[0][0], tz=timezone.utc)
    last_time = datetime.fromtimestamp(candles[-1][0], tz=timezone.utc)
    print(f"  From: {first_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  To:   {last_time.strftime('%Y-%m-%d %H:%M UTC')}")
    
    # Check if we have the 18:12 candle
    print("\nLooking for 18:12 candle:")
    found = False
    for c in candles:
        candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
        if candle_time.hour == 18 and candle_time.minute == 12:
            found = True
            print(f"  Found at {candle_time.strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"  O={c[1]:.5f} H={c[2]:.5f} L={c[3]:.5f} C={c[4]:.5f}")
            break
    
    if not found:
        print("  18:12 candle not found in recent 100 candles")
        print("\nLast 10 candles:")
        for c in candles[-10:]:
            candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
            print(f"  {candle_time.strftime('%Y-%m-%d %H:%M UTC')} O={c[1]:.5f} H={c[2]:.5f} L={c[3]:.5f} C={c[4]:.5f}")
else:
    print("Failed to get candles")
