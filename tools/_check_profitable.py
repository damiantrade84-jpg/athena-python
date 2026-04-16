"""Check a profitable MT5 trade to see if its deal prices are correct."""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect

mt5_connect()

# Find a profitable MT5 trade from audit
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT id,pair,ticket,entry_price,exit_price,pnl FROM audit_log "
    "WHERE exit_price IS NOT NULL AND pnl IS NOT NULL AND pnl != 0 "
    "AND pair NOT LIKE '%USDT%' ORDER BY id DESC LIMIT 5"
).fetchall()

print("Profitable MT5 trades from audit:")
for r in rows:
    print(f"  id={r['id']:>5} {str(r['pair']):>12} ticket={str(r['ticket']):>12} entry={r['entry_price']} exit={r['exit_price']} pnl={r['pnl']:.2f}")

if rows:
    # Check the deals for the most recent profitable trade
    sample = rows[0]
    ticket = int(sample["ticket"])
    print(f"\n=== Checking deals for {sample['pair']} ticket={ticket} ===")
    deals = mt5.history_deals_get(position=ticket)
    if deals:
        for d in sorted(deals, key=lambda x: x.time):
            print(f"  deal={d.ticket} type={d.type} entry={d.entry} price={d.price} vol={d.volume} profit={d.profit} order={d.order}")
    else:
        print("  NO DEALS FOUND")
else:
    print("No profitable MT5 trades found")
