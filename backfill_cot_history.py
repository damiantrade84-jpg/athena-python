"""One-off: deepen cot_cache.db so COT history covers the ASE training window.

The default cot_feed._seed_history(years=3) only seeds 2024+, but ASE events go
back to 2018. With COT_MIN_WEEKS=104, enriched features can't activate in the
chronological-80% training span unless COT history starts ~2 years before the
earliest events. Seed ~11 years (back to ~2016), then report new depth.
"""
from __future__ import annotations

import logging
import sqlite3

import cot_feed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

YEARS = 11  # 2026 .. 2016 inclusive


def main() -> int:
    # Force re-download by clearing the per-source fetch markers.
    with cot_feed._db_lock:
        con = sqlite3.connect(cot_feed._DB_PATH, timeout=15.0)
        con.execute("DELETE FROM cot_meta")
        con.commit()
        con.close()
    print(f"[backfill] seeding {YEARS} years of CFTC history ...")
    cot_feed._seed_history(years=YEARS)
    cot_feed._update_weekly()
    print("[backfill] done. Per-asset depth:")
    con = sqlite3.connect(cot_feed._DB_PATH, timeout=15.0)
    rows = con.execute(
        "SELECT asset, COUNT(*), MIN(report_date), MAX(report_date) "
        "FROM cot_net GROUP BY asset ORDER BY asset"
    ).fetchall()
    con.close()
    for a, n, mn, mx in rows:
        print(f"  {a:12} n={n:4}  {mn} -> {mx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
