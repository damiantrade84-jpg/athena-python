"""Check recent USD/CHF and GBP/USD trades for the reported issues."""
import os, sqlite3
from datetime import datetime, timezone, timedelta

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# Get recent trades for USD/CHF and GBP/USD
rows = con.execute(
    "SELECT id,pair,ticket,entry_price,exit_price,pnl,exit_reason,style,engine,ts,exit_time "
    "FROM audit_log WHERE pair IN ('USD/CHF', 'GBP/USD') ORDER BY id DESC LIMIT 10"
).fetchall()

print("Recent USD/CHF and GBP/USD trades:")
print(f"{'id':>5} {'pair':>12} {'ticket':>12} {'entry':>10} {'exit':>10} {'pnl':>8} {'reason':>12} {'ts':>8} {'exit':>8}")
for r in rows:
    entry = r["entry_price"] or "NULL"
    exit_p = r["exit_price"] or "NULL"
    pnl = r["pnl"] if r["pnl"] else "NULL"
    ts = r["ts"][-8:] if r["ts"] else "NULL"
    exit_t = r["exit_time"][-8:] if r["exit_time"] else "NULL"
    print(f"{r['id']:>5} {str(r['pair'] or ''):>12} {str(r['ticket'] or ''):>12} {str(entry):>10} {str(exit_p):>10} {str(pnl):>8} {str(r['exit_reason'] or ''):>12} {ts:>8} {exit_t:>8}")

# Check if any have exit_price = entry_price (zero profit bug)
zero_profit = [r for r in rows if r["exit_price"] and r["entry_price"] and abs(r["exit_price"] - r["entry_price"]) < 1e-8]
if zero_profit:
    print(f"\n*** Found {len(zero_profit)} trades with exit_price = entry_price ***")
    for r in zero_profit:
        print(f"  id={r['id']} {r['pair']} ticket={r['ticket']} entry={r['entry_price']} exit={r['exit_price']} reason={r['exit_reason']}")

# Check MT5 deals for the most recent problematic trade
if rows:
    latest = rows[0]
    if latest["ticket"]:
        print(f"\n=== Checking MT5 deals for {latest['pair']} ticket={latest['ticket']} ===")
        try:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            import MetaTrader5 as mt5
            from mt5_executor import mt5_connect
            
            if mt5_connect():
                deals = mt5.history_deals_get(position=int(latest["ticket"]))
                if deals:
                    for d in sorted(deals, key=lambda x: x.time):
                        dt = datetime.fromtimestamp(d.time, tz=timezone.utc)
                        print(f"  {dt.strftime('%H:%M:%S')} deal={d.ticket} type={d.type} price={d.price} profit={d.profit} order={d.order}")
                else:
                    print("  No deals found")
        except Exception as e:
            print(f"  Error checking deals: {e}")
