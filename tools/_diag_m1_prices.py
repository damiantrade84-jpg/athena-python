"""Check actual M1 candle prices at the time these trades closed."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

mt5_connect()

# Broker is UTC+3 based on the timezone check
BROKER_OFFSET = 3  # hours

# Today's zero-profit trades (deal close times in broker server time)
trades = [
    ("US500",  287574044, 1776340802, "LONG",  7032.8),  # close deal time, direction, entry
    ("NZDUSD", 287572593, 1776341214, "SHORT", 0.58955),
]

for sym, pos_id, close_server_ts, direction, entry_px in trades:
    # Convert server time to UTC
    close_utc_ts = close_server_ts - BROKER_OFFSET * 3600
    close_utc = datetime.fromtimestamp(close_utc_ts, tz=timezone.utc)
    
    print(f"\n{'='*60}")
    print(f"{sym} position={pos_id}")
    print(f"Direction: {direction}, Entry: {entry_px}")
    print(f"Close time (UTC): {close_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Get M1 candles around the close time (30 min window)
    from_time = close_utc - timedelta(minutes=15)
    to_time = close_utc + timedelta(minutes=15)
    
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, from_time, to_time)
    if rates is not None and len(rates) > 0:
        print(f"\nM1 candles around close time ({len(rates)} bars):")
        for r in rates:
            bar_time = datetime.fromtimestamp(r[0], tz=timezone.utc)
            # Highlight the bar containing the close
            marker = " <-- CLOSE BAR" if abs(r[0] - close_server_ts) < 60 else ""
            # Also check UTC-adjusted close
            marker2 = " <-- CLOSE UTC" if abs(r[0] - close_utc_ts) < 60 else ""
            print(
                f"  {bar_time.strftime('%H:%M:%S')} O={r[1]:.5f} H={r[2]:.5f} "
                f"L={r[3]:.5f} C={r[4]:.5f} V={r[5]}{marker}{marker2}"
            )
        
        # Find the M1 bar at close time
        for r in rates:
            if abs(r[0] - close_server_ts) < 60:
                print(f"\n  >>> At close time (server): bar O={r[1]:.5f} H={r[2]:.5f} L={r[3]:.5f} C={r[4]:.5f}")
                if direction == "LONG":
                    print(f"  >>> Expected profit direction: close > {entry_px} = winning")
                    print(f"  >>> Bar range: {r[3]:.5f} to {r[2]:.5f}")
                else:
                    print(f"  >>> Expected profit direction: close < {entry_px} = winning")
                    print(f"  >>> Bar range: {r[3]:.5f} to {r[2]:.5f}")
            if abs(r[0] - close_utc_ts) < 60:
                print(f"\n  >>> At close time (UTC-adjusted): bar O={r[1]:.5f} H={r[2]:.5f} L={r[3]:.5f} C={r[4]:.5f}")
    else:
        print(f"  No M1 data available for {sym} around close time")

# Also: check if copy_rates_from_pos returns M1 bars at all for these symbols
print(f"\n{'='*60}")
print("Quick check: M1 data availability")
for sym in ["US500", "NZDUSD"]:
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 5)
    if rates is not None:
        print(f"  {sym}: {len(rates)} recent M1 bars, last close={rates[-1][4]:.5f}")
    else:
        print(f"  {sym}: NO M1 data")
