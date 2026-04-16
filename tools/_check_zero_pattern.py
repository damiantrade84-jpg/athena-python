"""Check pattern of zero-profit trades vs their exit_reason."""
import os, sqlite3
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# Get all zero-profit trades
zero_rows = con.execute(
    "SELECT id,pair,ticket,entry_price,exit_price,pnl,exit_reason,style,engine,ts,exit_time "
    "FROM audit_log WHERE exit_price IS NOT NULL AND (pnl IS NULL OR pnl=0 OR pnl=0.0) "
    "ORDER BY id DESC LIMIT 20"
).fetchall()

print("Zero-profit trades pattern:")
print(f"{'id':>5} {'pair':>12} {'ticket':>12} {'entry':>10} {'exit':>10} {'pnl':>8} {'reason':>14} {'style':>10} {'engine':>10}")
for r in zero_rows:
    entry = r["entry_price"] or 0
    exit_p = r["exit_price"] or 0
    same = "*" if abs(entry - exit_p) < 1e-8 else ""
    print(
        f"{r['id']:>5} {str(r['pair'] or ''):>12} {str(r['ticket'] or ''):>12} "
        f"{entry:>10} {exit_p:>10} {(r['pnl'] or 0):>8.2f} {str(r['exit_reason'] or ''):>14} "
        f"{str(r['style'] or ''):>10} {str(r['engine'] or ''):>10} {same}"
    )

# Check if all zero-profit trades are TIMED_CLOSE or MANUAL_CLOSE
timed_or_manual = [r for r in zero_rows if r["exit_reason"] in ("TIMED_CLOSE", "MANUAL_CLOSE", "MANUAL")]
print(f"\nZero-profit trades with TIMED_CLOSE/MANUAL_CLOSE: {len(timed_or_manual)}/{len(zero_rows)}")

# Compare with a sample of non-zero trades
print("\nSample of non-zero trades for comparison:")
nonzero_rows = con.execute(
    "SELECT id,pair,ticket,entry_price,exit_price,pnl,exit_reason,style,engine "
    "FROM audit_log WHERE exit_price IS NOT NULL AND pnl IS NOT NULL AND pnl != 0 "
    "ORDER BY id DESC LIMIT 5"
).fetchall()
for r in nonzero_rows:
    entry = r["entry_price"] or 0
    exit_p = r["exit_price"] or 0
    print(
        f"{r['id']:>5} {str(r['pair'] or ''):>12} {str(r['ticket'] or ''):>12} "
        f"{entry:>10} {exit_p:>10} {(r['pnl'] or 0):>8.2f} {str(r['exit_reason'] or ''):>14} "
        f"{str(r['style'] or ''):>10} {str(r['engine'] or ''):>10}"
    )
