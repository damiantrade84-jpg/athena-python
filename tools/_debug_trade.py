"""Debug what happened to USD/CHF trade 1180."""
import os, sqlite3
from datetime import datetime, timezone

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# Get full details of the trade
row = con.execute(
    "SELECT * FROM audit_log WHERE id=1180"
).fetchone()

if row:
    print("USD/CHF trade 1180 details:")
    for key in row.keys():
        if row[key] is not None:
            print(f"  {key}: {row[key]}")
    
    # Check if it was a timed exit
    print("\nChecking if this was a TIMED_CLOSE:")
    if row["exit_reason"] == "TIMED_CLOSE":
        print("  YES: exit_reason is TIMED_CLOSE")
    else:
        print(f"  NO: exit_reason is '{row['exit_reason']}'")
    
    # Check the deal history
    print("\nMT5 Deal History:")
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import MetaTrader5 as mt5
        from mt5_executor import mt5_connect
        
        if mt5_connect():
            deals = mt5.history_deals_get(position=int(row["ticket"]))
            if deals:
                for d in sorted(deals, key=lambda x: x.time):
                    dt = datetime.fromtimestamp(d.time, tz=timezone.utc)
                    print(f"  {dt.strftime('%H:%M:%S')} deal={d.ticket} type={d.type} price={d.price} profit={d.profit} order={d.order}")
                    
                    # Check if this is the close deal
                    if d.type == 1:  # SELL for LONG position
                        print(f"    -> This is the CLOSE deal")
                        print(f"    -> Price: {d.price}, Profit: {d.profit}")
                        
                        # Check if price equals entry
                        if abs(d.price - row["entry_price"]) < 1e-8:
                            print("    *** BUG: Deal price equals entry price! ***")
                        else:
                            print("    -> Deal price differs from entry (good)")
            else:
                print("  No deals found")
    except Exception as e:
        print(f"  Error checking deals: {e}")

# Check if there are multiple audit rows for this ticket
print("\nAll audit rows for this ticket:")
rows = con.execute(
    "SELECT id, exit_price, pnl, exit_reason, ts, exit_time FROM audit_log WHERE ticket=? ORDER BY id",
    (str(row["ticket"]),)
).fetchall()
for r in rows:
    print(f"  id={r['id']} exit_price={r['exit_price']} pnl={r['pnl']} reason={r['exit_reason']} ts={r['ts'][-8:]} exit={r['exit_time'][-8:] if r['exit_time'] else 'NULL'}")
