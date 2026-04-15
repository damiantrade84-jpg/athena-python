"""repair_pnl_backfill.py — One-time script to backfill PnL for closed trades.

Finds audit_log rows where exit_price IS NOT NULL but pnl IS NULL or pnl = 0,
then re-queries MT5 deal history to write the correct values.

Run from the athena-python directory:
    python tools/repair_pnl_backfill.py [--dry-run]

Flags:
    --dry-run   Print what would be updated without writing to DB.
    --all       Also re-process rows where pnl != 0 (full re-audit).
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AUDIT_DB = os.path.join(ROOT, "audit.db")

# ── arg parsing ───────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Backfill missing PnL in audit_log")
parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
parser.add_argument("--all",     action="store_true", help="Re-audit ALL closed rows (not just 0/NULL pnl)")
args = parser.parse_args()

DRY_RUN = args.dry_run
ALL_ROWS = args.all

# ── connect MT5 ───────────────────────────────────────────────────────────────
print("Connecting to MT5...")
try:
    from mt5_executor import mt5_connect
    import MetaTrader5 as mt5

    if not mt5_connect():
        print("ERROR: MT5 connection failed. Make sure MT5 is running and terminal is open.")
        sys.exit(1)
    print("MT5 connected OK")
except ImportError:
    print("ERROR: MetaTrader5 not installed — only MT5 rows can be repaired by this script.")
    sys.exit(1)

# ── helpers (mirrors logic in athena.py) ──────────────────────────────────────

def _deals_for_ticket(ticket_int: int) -> list:
    """Fetch all MT5 deals associated with a ticket/position id."""
    deals = mt5.history_deals_get(position=ticket_int)
    if not deals:
        by_order = mt5.history_deals_get(ticket=ticket_int)
        if by_order:
            pids = {int(d.position_id) for d in by_order if getattr(d, "position_id", 0) not in (None, 0)}
            for pid in pids:
                expanded = mt5.history_deals_get(position=pid)
                if expanded:
                    deals = expanded
                    break
            if not deals and pids:
                deals = mt5.history_deals_get(position=max(pids))
    if not deals:
        from_dt = datetime.now(timezone.utc) - timedelta(days=365)
        to_dt   = datetime.now(timezone.utc)
        raw = mt5.history_deals_get(int(from_dt.timestamp()), int(to_dt.timestamp()))
        if raw:
            related = [
                d for d in raw
                if int(getattr(d, "position_id", 0) or 0) == ticket_int
                or int(getattr(d, "order", 0) or 0) == ticket_int
            ]
            if related:
                pids = {int(d.position_id) for d in related if getattr(d, "position_id", 0) not in (None, 0)}
                if len(pids) == 1:
                    pid = next(iter(pids))
                    deals = mt5.history_deals_get(position=pid) or related
                else:
                    deals = related
    return list(deals) if deals else []


def _r_multiple(pnl, risk_amount, entry, sl, volume):
    """Compute R-multiple from stored row values."""
    if pnl is None:
        return None
    if risk_amount and float(risk_amount) > 0:
        return round(pnl / float(risk_amount), 2)
    if entry and sl and volume:
        dist = abs(float(entry) - float(sl))
        if dist > 0:
            return round(pnl / (dist * float(volume)), 2)
    return None


def _exit_reason(exit_price, sl, tp):
    """Simple exit reason inference."""
    if sl and abs(float(exit_price) - float(sl)) / max(abs(float(sl)), 1e-12) < 0.001:
        return "SL_HIT"
    if tp and abs(float(exit_price) - float(tp)) / max(abs(float(tp)), 1e-12) < 0.001:
        return "TP_HIT"
    return "MANUAL"


# ── fetch rows to repair ──────────────────────────────────────────────────────
con = sqlite3.connect(AUDIT_DB, timeout=15.0)
con.row_factory = sqlite3.Row

if ALL_ROWS:
    where = "exit_price IS NOT NULL AND ticket IS NOT NULL AND ticket != ''"
else:
    where = "exit_price IS NOT NULL AND ticket IS NOT NULL AND ticket != '' AND (pnl IS NULL OR pnl = 0 OR pnl = 0.0)"

rows = con.execute(
    f"SELECT id, ticket, pair, direction, entry_price, sl, tp, volume, ts, risk_amount, asset_class, pnl, r_multiple "
    f"FROM audit_log WHERE {where} ORDER BY id ASC"
).fetchall()

print(f"\nFound {len(rows)} row(s) to repair {'(--all mode)' if ALL_ROWS else '(pnl=NULL or 0)'}")
if not rows:
    print("Nothing to do.")
    sys.exit(0)

# ── process each row ─────────────────────────────────────────────────────────
repaired   = 0
skipped    = 0
not_found  = 0

for row in rows:
    ticket_str = str(row["ticket"]).strip()
    pair       = row["pair"] or "?"

    # Skip Bybit rows (USDT pairs) — those need Bybit history, not MT5
    if "USDT" in pair.upper():
        print(f"  SKIP  [{row['id']:>6}] {pair:>12} ticket={ticket_str} — Bybit trade, skipping")
        skipped += 1
        continue

    try:
        ticket_int = int(ticket_str)
    except ValueError:
        print(f"  SKIP  [{row['id']:>6}] {pair:>12} ticket={ticket_str} — non-integer ticket")
        skipped += 1
        continue

    deals = _deals_for_ticket(ticket_int)
    if not deals:
        print(f"  MISS  [{row['id']:>6}] {pair:>12} ticket={ticket_int} — no MT5 deal history found")
        not_found += 1
        continue

    deals_sorted = sorted(deals, key=lambda d: d.time)
    close_deal   = deals_sorted[-1]
    total_pnl    = sum(float(d.profit) for d in deals_sorted)
    exit_px      = float(close_deal.price)
    exit_iso     = datetime.fromtimestamp(close_deal.time, tz=timezone.utc).isoformat()

    r_mult = _r_multiple(
        total_pnl,
        row["risk_amount"],
        row["entry_price"],
        row["sl"],
        row["volume"],
    )

    # Infer holding period
    holding_h = None
    if row["ts"]:
        try:
            t_entry = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
            t_exit  = datetime.fromisoformat(exit_iso)
            holding_h = round((t_exit - t_entry).total_seconds() / 3600, 2)
        except Exception:
            pass

    win_tag = "WIN " if total_pnl > 0 else ("LOSS" if total_pnl < 0 else "BE  ")
    print(
        f"  {win_tag}  [{row['id']:>6}] {pair:>12} ticket={ticket_int:>10} "
        f"old_pnl={str(row['pnl'] or 'NULL'):>8}  new_pnl={total_pnl:>+10.4f}  "
        f"R={str(r_mult) if r_mult is not None else '-':>6}  "
        f"exit_px={exit_px}"
    )

    if not DRY_RUN:
        exit_reason = _exit_reason(exit_px, row["sl"], row["tp"])
        con.execute(
            "UPDATE audit_log SET pnl=?, r_multiple=?, exit_price=?, exit_time=?, "
            "exit_reason=?, holding_period_hours=? "
            "WHERE id=?",
            (
                round(total_pnl, 4),
                r_mult,
                exit_px,
                exit_iso,
                exit_reason,
                holding_h,
                row["id"],
            ),
        )
        con.commit()
    repaired += 1

# ── summary ───────────────────────────────────────────────────────────────────
print(f"\n{'[DRY RUN] Would repair' if DRY_RUN else 'Repaired'}: {repaired}")
print(f"Skipped (Bybit/non-int): {skipped}")
print(f"Not found in MT5 history: {not_found}")

if DRY_RUN:
    print("\nRe-run without --dry-run to apply changes.")
else:
    print("\nDone. Restart Athena or refresh the Performance tab to see updated values.")

con.close()
