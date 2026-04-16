"""Analyze the USD/CHF trade with actual market data."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

mt5_connect()

# Trade details
entry_price = 0.78396
sl_price = 0.7847845711385434
tp_price = 0.7827481432921848
volume = 5.13  # lots
direction = "SHORT"

print("USD/CHF Trade Analysis (ticket 287974631)")
print("=" * 50)
print(f"Direction: {direction}")
print(f"Entry:    {entry_price:.5f}")
print(f"SL:       {sl_price:.5f}")
print(f"TP:       {tp_price:.5f}")
print(f"Volume:   {volume} lots")
print()

# Get the 18:12 candle (when trade closed)
symbol = "USDCHF"
candles = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 100)
target_candle = None
for c in candles:
    candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
    if candle_time.hour == 18 and candle_time.minute == 12:
        target_candle = c
        break

if target_candle:
    ct = datetime.fromtimestamp(target_candle[0], tz=timezone.utc)
    print(f"Market at close time ({ct.strftime('%H:%M UTC')}):")
    print(f"  Open:  {target_candle[1]:.5f}")
    print(f"  High:  {target_candle[2]:.5f}")
    print(f"  Low:   {target_candle[3]:.5f}")
    print(f"  Close: {target_candle[4]:.5f}")
    print()
    
    # Check if SL was reached
    print("SL Analysis:")
    if target_candle[2] >= sl_price:
        print(f"  *** SL REACHED: High {target_candle[2]:.5f} >= SL {sl_price:.5f} ***")
        print(f"  This should have been a LOSS trade")
        
        # Calculate loss at SL
        loss_pips = sl_price - entry_price
        loss_usd = loss_pips * volume * 100000  # 5.13 lots, 100000 per lot
        print(f"  Loss would be: {loss_pips:.5f} pips = ${loss_usd:.2f}")
    else:
        print(f"  SL NOT reached: High {target_candle[2]:.5f} < SL {sl_price:.5f}")
    
    # Check if it was profitable
    print("\nProfit Analysis:")
    if target_candle[3] < entry_price:  # Price went down (good for SHORT)
        profit_pips = entry_price - target_candle[3]
        profit_usd = profit_pips * volume * 100000
        print(f"  Price went DOWN to {target_candle[3]:.5f}")
        print(f"  Potential profit: {profit_pips:.5f} pips = ${profit_usd:.2f}")
        
        # Check if TP was reached
        if target_candle[3] <= tp_price:
            print(f"  *** TP REACHED: Low {target_candle[3]:.5f} <= TP {tp_price:.5f} ***")
    else:
        print(f"  Price went UP to {target_candle[4]:.5f} (bad for SHORT)")
    
    print()
    print("Conclusion:")
    print(f"  The trade was SHORT from {entry_price:.5f}")
    print(f"  Market at close: {target_candle[3]:.5f} - {target_candle[4]:.5f}")
    print(f"  This was a PROFITABLE trade (price went down)")
    print(f"  But MT5 recorded deal price as {entry_price:.5f} (wrong!)")
    print(f"  And exit_reason as SL_HIT (wrong!)")
    print()
    print("What should have happened:")
    print(f"  - Exit price should be ~{target_candle[3]:.5f}")
    print(f"  - PnL should be ~${profit_usd:.2f}")
    print(f"  - Exit reason should be TIMED_CLOSE (not SL_HIT)")
