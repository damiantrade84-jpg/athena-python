"""Deep diagnostic: check ALL MT5 deals for today's zero-profit trades."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from mt5_executor import mt5_connect
from datetime import datetime, timezone, timedelta

mt5_connect()

# Get ALL deals from last 30 days
from_dt = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
to_dt = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
all_deals = mt5.history_deals_get(from_dt, to_dt)
if not all_deals:
    print("NO DEALS found")
    sys.exit(1)

print(f"Total deals in last 30 days: {len(all_deals)}")

# Find ALL deals for US500 and NZDUSD
for sym in ["US500", "NZDUSD"]:
    sym_deals = [d for d in all_deals if d.symbol == sym]
    print(f"\n{'='*60}")
    print(f"ALL {sym} deals ({len(sym_deals)} total):")
    print(f"{'='*60}")
    for d in sorted(sym_deals, key=lambda x: x.time):
        dt = datetime.fromtimestamp(d.time, tz=timezone.utc)
        entry_type = "IN" if d.entry == 0 else "OUT" if d.entry == 1 else f"?{d.entry}"
        deal_type = "BUY" if d.type == 0 else "SELL" if d.type == 1 else f"?{d.type}"
        print(
            f"  {dt.strftime('%Y-%m-%d %H:%M:%S')} deal={d.ticket} {deal_type:>4} "
            f"{entry_type:>3} price={d.price} vol={d.volume} "
            f"profit={d.profit:>+10.2f} comm={d.commission:>+8.2f} "
            f"pos={d.position_id} order={d.order}"
        )

# Also check: what was the MARKET PRICE at the time of close for today's trades?
# Compare deal close times with broker server timezone  
print(f"\n{'='*60}")
print("Broker timezone check:")
print(f"{'='*60}")
# Check the time offset by comparing a known-time deal with audit_log
import sqlite3
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
# Find a trade where we know both audit ts and deal time
for row in con.execute(
    "SELECT ticket, ts, exit_time, pair FROM audit_log "
    "WHERE exit_price IS NOT NULL AND ticket IS NOT NULL AND ticket != '' "
    "AND pair NOT LIKE '%USDT%' AND pnl IS NOT NULL AND pnl != 0 "
    "ORDER BY id DESC LIMIT 3"
).fetchall():
    ticket_int = int(row["ticket"])
    deals = mt5.history_deals_get(position=ticket_int)
    if deals:
        open_deal = sorted(deals, key=lambda x: x.time)[0]
        close_deal = sorted(deals, key=lambda x: x.time)[-1]
        deal_open_utc = datetime.fromtimestamp(open_deal.time, tz=timezone.utc)
        audit_ts = row["ts"]
        print(f"\n  {row['pair']} ticket={ticket_int}:")
        print(f"    Audit signal ts: {audit_ts}")
        print(f"    Deal open time (as UTC): {deal_open_utc.isoformat()}")
        print(f"    Audit exit_time: {row['exit_time']}")
        print(f"    Deal close time (as UTC): {datetime.fromtimestamp(close_deal.time, tz=timezone.utc).isoformat()}")
        print(f"    Deal close profit: {close_deal.profit}")
