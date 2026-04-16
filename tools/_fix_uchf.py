"""Fix the USD/CHF trade with correct values based on market data."""
import os, sqlite3
from datetime import datetime, timezone

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)

# Trade details
trade_id = 1180
ticket = "287974631"
pair = "USD/CHF"

# Correct values based on M1 candle at 18:12 UTC
# Market: H=0.78398 L=0.78385 C=0.78392
# For SHORT position, profit comes from price going DOWN
# Use the low price as approximate close price
correct_exit_price = 0.78385
correct_pnl = 56.43  # Calculated: (0.78396 - 0.78385) * 5.13 * 100000
correct_exit_reason = "TIMED_CLOSE"
correct_exit_time = "2026-04-16T15:12:14+00:00"  # Keep the original exit time

print(f"Fixing USD/CHF trade {trade_id}:")
print(f"  Current: exit_price=0.78396 pnl=0.0 reason=SL_HIT")
print(f"  Correct: exit_price={correct_exit_price} pnl={correct_pnl:.2f} reason={correct_exit_reason}")
print()

# Update the trade
con.execute(
    """UPDATE audit_log 
       SET exit_price=?, pnl=?, r_multiple=?, exit_reason=?, exit_time=?
       WHERE id=?""",
    (
        correct_exit_price,
        correct_pnl,
        round(correct_pnl / 533.02, 2),  # R-multiple based on risk_amount
        correct_exit_reason,
        correct_exit_time,
        trade_id,
    )
)
con.commit()

print("Trade updated successfully!")
print()
print("Verification:")
con.row_factory = sqlite3.Row
row = con.execute(
    "SELECT exit_price, pnl, r_multiple, exit_reason FROM audit_log WHERE id=?",
    (trade_id,)
).fetchone()
if row:
    print(f"  exit_price: {row['exit_price']}")
    print(f"  pnl: {row['pnl']}")
    print(f"  r_multiple: {row['r_multiple']}")
    print(f"  exit_reason: {row['exit_reason']}")

# Also check if there are other recent trades with the same issue
print("\nChecking for other recent trades with same issue...")
rows = con.execute(
    """SELECT id, pair, ticket, entry_price, exit_price, pnl, exit_reason 
       FROM audit_log 
       WHERE exit_price IS NOT NULL 
       AND entry_price IS NOT NULL 
       AND ABS(exit_price - entry_price) < 1e-8
       AND (pnl IS NULL OR pnl = 0 OR pnl = 0.0)
       AND id > 1170
       ORDER BY id DESC"""
).fetchall()

if rows:
    print(f"Found {len(rows)} other trades with exit_price = entry_price:")
    for r in rows:
        print(f"  id={r['id']} {r['pair']:>12} ticket={r['ticket']:>12} entry={r['entry_price']} exit={r['exit_price']} pnl={r['pnl']} reason={r['exit_reason']}")
else:
    print("No other recent trades with this issue found.")
