"""Manually fix the remaining zero-profit trades."""
import os, sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)

# These trades were affected by the Pepperstone bug
# We'll set them to NULL so the repair tool can attempt to fix them later
# or at least they won't show as zero profit

fixes = [
    {
        "id": 1178,
        "pair": "S&P 500",
        "ticket": "287574044",
        "entry": 7032.8,
        "exit_reason": "MANUAL"
    },
    {
        "id": 1176,
        "pair": "NZD/USD", 
        "ticket": "287572593",
        "entry": 0.58955,
        "exit_reason": "MANUAL"
    }
]

print("Manually fixing remaining Pepperstone bug trades...")
print()

for fix in fixes:
    print(f"Trade {fix['id']}: {fix['pair']}")
    print(f"  Current: exit_price={fix['entry']} (same as entry) pnl=0.0")
    print(f"  Action: Setting exit_price and pnl to NULL")
    
    # Set to NULL so it doesn't show as zero profit
    con.execute(
        "UPDATE audit_log SET exit_price=NULL, pnl=NULL, r_multiple=NULL WHERE id=?",
        (fix['id'],)
    )
    
    print(f"  Updated")

con.commit()

print("\nAll trades updated!")
print("\nVerification:")
rows = con.execute(
    "SELECT id, pair, exit_price, pnl, exit_reason FROM audit_log WHERE id IN (1176, 1178)"
).fetchall()
for r in rows:
    print(f"  id={r[0]} {r[1]:>12} exit_price={r[2]} pnl={r[3]} reason={r[4]}")

print("\nNote: These trades now show NULL instead of 0.00")
print("The actual profit values couldn't be determined due to lack of historical M1 data")
