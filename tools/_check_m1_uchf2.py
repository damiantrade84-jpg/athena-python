"""Check M1 candles for USD/CHF around the close time."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

mt5_connect()

# Try to get the most recent candles
symbol = "USDCHF"
print(f"Getting recent USD/CHF M1 candles...")

# Get last 20 candles
candles = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 20)
if candles is not None and len(candles) > 0:
    print(f"Got {len(candles)} candles")
    print("\nLast 10 M1 candles:")
    for c in candles[-10:]:
        candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
        print(f"  {candle_time.strftime('%Y-%m-%d %H:%M')} O={c[1]:.5f} H={c[2]:.5f} L={c[3]:.5f} C={c[4]:.5f}")
    
    # Check around 15:12 UTC
    print("\nLooking for 15:12 UTC candle:")
    for c in candles:
        candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
        if candle_time.hour == 15 and candle_time.minute == 12:
            print(f"  Found: O={c[1]:.5f} H={c[2]:.5f} L={c[3]:.5f} C={c[4]:.5f}")
            
            # Check if SL was reached
            entry_price = 0.78396
            sl_price = 0.7847845711385434
            print(f"\n  Trade: SHORT entry={entry_price:.5f} SL={sl_price:.5f}")
            print(f"  Candle HIGH: {c[2]:.5f}")
            if c[2] >= sl_price:
                print(f"  *** SL REACHED: High {c[2]:.5f} >= SL {sl_price:.5f} ***")
            else:
                print(f"  SL NOT reached: High {c[2]:.5f} < SL {sl_price:.5f}")
            break
else:
    print("Failed to get candles")
    
    # Check if symbol is correct
    symbols = mt5.symbols_get()
    usdchf_syms = [s.name for s in symbols if "USD" in s.name and "CHF" in s.name]
    print(f"\nUSD/CHF symbols available: {usdchf_syms}")
