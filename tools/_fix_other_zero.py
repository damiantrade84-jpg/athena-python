"""Fix other zero-profit trades with the same Pepperstone bug."""
import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# Get trades with the bug
rows = con.execute(
    """SELECT id, pair, ticket, entry_price, exit_price, pnl, exit_reason, ts, exit_time
       FROM audit_log 
       WHERE exit_price IS NOT NULL 
       AND entry_price IS NOT NULL 
       AND ABS(exit_price - entry_price) < 1e-8
       AND (pnl IS NULL OR pnl = 0 OR pnl = 0.0)
       AND id > 1170
       ORDER BY id DESC"""
).fetchall()

mt5_connect()

print(f"Found {len(rows)} trades to fix:")
for r in rows:
    print(f"\n=== Trade {r['id']}: {r['pair']} ticket={r['ticket']} ===")
    print(f"  Current: entry={r['entry_price']} exit={r['exit_price']} pnl={r['pnl']} reason={r['exit_reason']}")
    
    # Get the actual close time
    if r['exit_time']:
        try:
            if r['exit_time'].isdigit():
                close_time = datetime.fromtimestamp(int(r['exit_time']), tz=timezone.utc)
            else:
                close_time = datetime.fromisoformat(r['exit_time'].replace('Z', '+00:00'))
            print(f"  Close time: {close_time.strftime('%Y-%m-%d %H:%M UTC')}")
        except:
            close_time = None
    
    # Try to get M1 candles around close time
    if close_time:
        # Convert to broker time (UTC+3)
        broker_time = close_time + timedelta(hours=3)
        
        # Get symbol name for MT5
        symbol_map = {
            'S&P 500': 'US500',
            'NZD/USD': 'NZDUSD',
            'USD/CHF': 'USDCHF',
            'GBP/USD': 'GBPUSD',
            'EUR/JPY': 'EURJPY',
            'EUR/GBP': 'EURGBP',
            'GBP/JPY': 'GBPJPY',
            'XAG/USD': 'XAGUSD',
            'INTC': 'INTC.US'
        }
        
        mt5_symbol = symbol_map.get(r['pair'], r['pair'].replace('/', ''))
        
        try:
            # Get candles around the close time
            candles = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M1, 0, 100)
            
            if candles is not None and len(candles) > 0:
                # Find candle closest to close time
                target_candle = None
                min_diff = float('inf')
                
                for c in candles:
                    candle_time = datetime.fromtimestamp(c[0], tz=timezone.utc)
                    # Convert to UTC for comparison
                    candle_utc = candle_time - timedelta(hours=3)  # Broker time -> UTC
                    
                    diff = abs((candle_utc - close_time).total_seconds())
                    if diff < min_diff and diff < 300:  # Within 5 minutes
                        min_diff = diff
                        target_candle = c
                
                if target_candle:
                    candle_utc = datetime.fromtimestamp(target_candle[0], tz=timezone.utc) - timedelta(hours=3)
                    print(f"  Found candle at {candle_utc.strftime('%H:%M UTC')}:")
                    print(f"    O={target_candle[1]:.5f} H={target_candle[2]:.5f} L={target_candle[3]:.5f} C={target_candle[4]:.5f}")
                    
                    # Determine direction from entry/exit
                    entry = float(r['entry_price'])
                    
                    # For now, just use the close price as estimate
                    estimated_exit = float(target_candle[4])
                    
                    # Skip if it's the same as entry (would be zero profit)
                    if abs(estimated_exit - entry) < 1e-8:
                        print(f"  Skipping: estimated exit price equals entry price")
                        continue
                    
                    # Update with estimated values
                    con.execute(
                        """UPDATE audit_log 
                           SET exit_price=?, pnl=?
                           WHERE id=?""",
                        (estimated_exit, None, r['id'])  # Set pnl to NULL to trigger recalculation
                    )
                    con.commit()
                    
                    print(f"  Updated with estimated exit price: {estimated_exit:.5f}")
                else:
                    print(f"  No candle found within 5 minutes of close time")
            else:
                print(f"  No candles available for {mt5_symbol}")
        except Exception as e:
            print(f"  Error getting candles: {e}")
    else:
        print(f"  No close time available")

print("\nDone!")
