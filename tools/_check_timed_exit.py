"""Check if timed exit monitor ran for USD/CHF trade."""
import os, sqlite3
from datetime import datetime, timezone, timedelta

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# Get the USD/CHF trade
row = con.execute(
    "SELECT * FROM audit_log WHERE id=1180"
).fetchone()

if row:
    print("USD/CHF Trade 1180:")
    print(f"  Opened:  {row['ts']}")
    print(f"  Closed:  {row['exit_time']}")
    print(f"  Style:   {row['style']}")
    print(f"  Engine:  {row['engine']}")
    
    # Calculate how long it was open
    if row['ts'] and row['exit_time']:
        try:
            open_time = datetime.fromisoformat(row['ts'].replace('Z', '+00:00'))
            close_time = datetime.fromisoformat(row['exit_time'].replace('Z', '+00:00'))
            duration = close_time - open_time
            print(f"  Duration: {duration.total_seconds() / 60:.1f} minutes")
            
            # Check timed exit thresholds
            style = row['style'] or 'intraday'
            print(f"\nTimed exit thresholds for {style}:")
            if style == 'scalp':
                be_min = 3  # minutes
                close_min = 5  # minutes
            elif style == 'intraday':
                be_min = 15
                close_min = 30
            elif style == 'swing':
                be_min = 1440  # 24h
                close_min = 2880  # 48h
            else:
                be_min = 15
                close_min = 30
            
            print(f"  Breakeven after: {be_min} minutes")
            print(f"  Close after: {close_min} minutes")
            
            if duration.total_seconds() / 60 >= close_min:
                print(f"\n*** Trade was open for {duration.total_seconds() / 60:.1f} minutes >= {close_min} min threshold")
                print(f"*** Timed exit SHOULD HAVE triggered")
            else:
                print(f"\nTrade was only open for {duration.total_seconds() / 60:.1f} minutes < {close_min} min threshold")
                print(f"Timed exit would NOT trigger")
        except Exception as e:
            print(f"  Error calculating duration: {e}")

# Check if there's any evidence of TIMED_CLOSE in the audit
print("\nChecking for TIMED_CLOSE evidence:")
rows = con.execute(
    "SELECT id, exit_reason, exit_price, pnl FROM audit_log WHERE ticket=? ORDER BY id",
    (str(row['ticket']),)
).fetchall()
for r in rows:
    print(f"  id={r['id']} reason={r['exit_reason']} exit_price={r['exit_price']} pnl={r['pnl']}")
