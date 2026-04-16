"""Diagnostic: dump last 30 closed trades from audit_log."""
import sqlite3, os
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT id,pair,ticket,entry_price,exit_price,pnl,r_multiple,exit_reason,style,engine "
    "FROM audit_log WHERE exit_price IS NOT NULL ORDER BY id DESC LIMIT 30"
).fetchall()
hdr = f"{'id':>5} {'pair':>12} {'ticket':>12} {'entry':>10} {'exit':>10} {'pnl':>10} {'R':>6} {'reason':>14} {'style':>10} {'engine':>10} {'flag':>6}"
print(hdr)
print("-" * len(hdr))
for r in rows:
    entry = r["entry_price"] or 0
    exit_p = r["exit_price"] or 0
    pnl = r["pnl"] or 0
    flag = "SAME!" if abs(entry - exit_p) < 1e-8 else ""
    print(
        f"{r['id']:>5} {str(r['pair'] or ''):>12} {str(r['ticket'] or ''):>12} "
        f"{entry:>10} {exit_p:>10} {pnl:>10.2f} {str(r['r_multiple'] or '-'):>6} "
        f"{str(r['exit_reason'] or ''):>14} {str(r['style'] or ''):>10} {str(r['engine'] or ''):>10} {flag:>6}"
    )
