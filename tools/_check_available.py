"""Check available candles for the problematic trades."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

mt5_connect()

# S&P 500 trade closed at 12:00 UTC (15:00 broker time)
# NZD/USD trade closed at 12:06 UTC (15:06 broker time)

symbols = [
    ("US500", "2026-04-16 12:00 UTC"),
    ("NZDUSD", "2026-04-16 12:06 UTC")
]

for symbol, close_time_str in symbols:
    print(f"\n=== {symbol} - Close time: {close_time_str} ===")
    
    close_time = datetime.strptime(close_time_str, "%Y-%m-%d %H:%M UTC")
    close_time = close_time.replace(tzinfo=timezone.utc)
    
    # Get last 100 candles
    candles = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 100)
    
    if candles is not None and len(candles) > 0:
        print(f"Got {len(candles)} candles")
        
        # Show time range
        first_time = datetime.fromtimestamp(candles[0][0], tz=timezone.utc) - timedelta(hours=3)
        last_time = datetime.fromtimestamp(candles[-1][0], tz=timezone.utc) - timedelta(hours=3)
        print(f"Time range: {first_time.strftime('%H:%M')} - {last_time.strftime('%H:%M')} UTC")
        
        # Look for candles around close time
        print("\nCandles around close time:")
        for c in candles:
            candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc) - timedelta(hours=3)
            
            # Show candles within 10 minutes
            if abs((candle_time - close_time).total_seconds()) <= 600:
                print(f"  {candle_time.strftime('%H:%M UTC')} O={c[1]:.5f} H={c[2]:.5f} L={c[3]:.5f} C={c[4]:.5f}")
                
                # Highlight the closest one
                if abs((candle_time - close_time).total_seconds()) <= 60:
                    print(f"    ^^^ CLOSEST (within 1 min)")
    else:
        print("No candles available")
