"""Check M1 candles for USD/CHF around the close time to verify actual price."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

mt5_connect()

# USD/CHF trade closed at 2026-04-16T15:12:14+00:00
# Let's check M1 candles around that time
symbol = "USDCHF"
close_time = datetime(2026, 4, 16, 15, 12, tzinfo=timezone.utc)

# Get candles from 5 minutes before to 5 minutes after
from_dt = close_time - timedelta(minutes=5)
to_dt = close_time + timedelta(minutes=5)

print(f"Checking USD/CHF M1 candles around {close_time.strftime('%H:%M:%S UTC')}")
print(f"From: {from_dt.strftime('%H:%M:%S')} To: {to_dt.strftime('%H:%M:%S')}")
print()

candles = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 20)
if candles is not None and len(candles) > 0:
    # Find candles around the close time
    for c in candles[-10:]:
        candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
        if from_dt <= candle_time <= to_dt:
            print(f"  {candle_time.strftime('%H:%M:%S')} O={c[1]:.5f} H={c[2]:.5f} L={c[3]:.5f} C={c[4]:.5f}")
            if candle_time.strftime('%H:%M') == "15:12":
                print(f"    ^^^ This is the close minute")
    
    # Show the entry and SL levels
    entry_price = 0.78396
    sl_price = 0.7847845711385434  # SL for SHORT position
    
    print()
    print(f"Trade details:")
    print(f"  Entry: {entry_price:.5f}")
    print(f"  SL:    {sl_price:.5f} (SHORT position, above entry)")
    print(f"  Direction: SHORT")
    print()
    print("For a SHORT position:")
    print("  - Profit when price goes DOWN")
    print("  - Loss when price goes UP")
    print(f"  - SL at {sl_price:.5f} should trigger if price reaches that level")
    
    # Check if any candle reached the SL
    print()
    print("Checking if SL was reached:")
    for c in candles[-10:]:
        candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
        if from_dt <= candle_time <= to_dt:
            high = c[2]
            if high >= sl_price:
                print(f"  {candle_time.strftime('%H:%M:%S')}: HIGH {high:.5f} >= SL {sl_price:.5f} *** SL HIT ***")
                break
else:
    print("Failed to get M1 candles")
