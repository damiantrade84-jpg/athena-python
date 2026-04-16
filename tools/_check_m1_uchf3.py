"""Check M1 candles for USD/CHF around the close time (broker time UTC+3)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

mt5_connect()

# Trade closed at 2026-04-16T15:12:14+00:00 UTC
# Broker is UTC+3, so this is 18:12 broker time
symbol = "USDCHF"
close_time_utc = datetime(2026, 4, 16, 15, 12, tzinfo=timezone.utc)
close_time_broker = close_time_utc + timedelta(hours=3)  # UTC+3

print(f"Trade close time: {close_time_utc.strftime('%Y-%m-%d %H:%M UTC')}")
print(f"Broker time (UTC+3): {close_time_broker.strftime('%Y-%m-%d %H:%M')}")
print()

# Get last 50 candles to find the 18:12 candle
candles = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 50)
if candles is not None and len(candles) > 0:
    print(f"Got {len(candles)} candles")
    
    # Look for the 18:12 candle
    target_candle = None
    for c in candles:
        candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
        if candle_time.hour == 18 and candle_time.minute == 12:
            target_candle = c
            break
    
    if target_candle:
        ct = datetime.fromtimestamp(target_candle[0], tz=timezone.utc)
        print(f"\nFound 18:12 candle (UTC {ct.strftime('%H:%M')}):")
        print(f"  O={target_candle[1]:.5f} H={target_candle[2]:.5f} L={target_candle[3]:.5f} C={target_candle[4]:.5f}")
        
        # Trade details
        entry_price = 0.78396
        sl_price = 0.7847845711385434
        print(f"\nTrade: SHORT entry={entry_price:.5f} SL={sl_price:.5f}")
        
        # Check if SL was reached
        if target_candle[2] >= sl_price:
            print(f"*** SL REACHED: High {target_candle[2]:.5f} >= SL {sl_price:.5f} ***")
            # Calculate approximate PnL if closed at SL
            pnl_at_sl = (entry_price - sl_price) * 5.13 * 100000  # 5.13 lots, 100000 per lot
            print(f"Approx PnL at SL: ${pnl_at_sl:.2f} (LOSS)")
        else:
            print(f"SL NOT reached: High {target_candle[2]:.5f} < SL {sl_price:.5f}")
            
            # If price went down, it would be profitable
            if target_candle[3] < entry_price:
                pnl_profit = (entry_price - target_candle[3]) * 5.13 * 100000
                print(f"If closed at low {target_candle[3]:.5f}: PnL = ${pnl_profit:.2f} (PROFIT)")
    else:
        print("\n18:12 candle not found. Showing candles around 18:00-18:20:")
        for c in candles:
            candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
            if 18 <= candle_time.hour <= 18 and 0 <= candle_time.minute <= 20:
                print(f"  {candle_time.strftime('%H:%M')} O={c[1]:.5f} H={c[2]:.5f} L={c[3]:.5f} C={c[4]:.5f}")
else:
    print("Failed to get candles")
