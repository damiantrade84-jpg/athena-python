"""Read-only Engine A exit diagnostic.

Follow-up to diagnose_edge.py. Focused on SL mechanics:

  1. Time to SL hit          — bars/hours held before SL. Median 1-3h =
                                SL is getting picked off by noise.
  2. SL distance as % entry  — distribution per class; does tight SL
                                correlate with higher SL_HIT rate?
  3. SL_HIT rate by regime   — if RANGING >> TRENDING, regime-aware SL
                                widening is the fix.
  4. Anomaly rows            — dump asset_class='unknown' and any row
                                with |r_multiple| > 10 (data errors).

Usage:
    python diagnose_exits.py [path/to/audit.db]

Never writes.
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def closed_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    sql = """
        SELECT pair, asset_class, direction, style, score, regime,
               entry_price, sl, tp, exit_price, exit_reason,
               r_multiple, pnl, holding_period_hours, grade, ts
        FROM audit_log
        WHERE exit_price IS NOT NULL
          AND r_multiple IS NOT NULL
          AND (grade IS NULL OR grade NOT LIKE 'AUTO-ERR%')
    """
    return list(conn.execute(sql))


def group_by(rows: list[sqlite3.Row], key: str) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        k = r[key] or "unknown"
        out[k].append(r)
    return out


def pct_of(numer: int, denom: int) -> str:
    return f"{(100.0 * numer / denom):>4.1f}%" if denom else "  n/a"


def summarize_values(vals: list[float]) -> str:
    if not vals:
        return "n=0"
    vals = sorted(vals)
    n = len(vals)
    med = statistics.median(vals)
    p25 = vals[n // 4] if n >= 4 else vals[0]
    p75 = vals[(3 * n) // 4] if n >= 4 else vals[-1]
    return f"n={n:<4} p25={p25:<7.2f} med={med:<7.2f} p75={p75:<7.2f}"


def sl_pct_of_entry(r: sqlite3.Row) -> float | None:
    e, s = r["entry_price"], r["sl"]
    if e is None or s is None or e == 0:
        return None
    return abs(e - s) / e * 100.0


def print_h(title: str) -> None:
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", nargs="?", default="audit.db")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found", file=sys.stderr)
        return 1

    conn = open_readonly(db_path)
    rows = closed_rows(conn)
    if not rows:
        print("No closed rows in audit_log.")
        return 0

    print(f"Analyzing {len(rows)} closed trades from {db_path}")

    # ----- 1. Time to SL hit -----
    print_h("1. Time-to-SL-hit (hours held) per asset_class")
    print("  (median 1-3h on H4/D1 trades = SL picked off by normal noise)")
    print()
    by_class = group_by(rows, "asset_class")
    print(f"  {'class':<10} {'SL_HIT holding hours (hrs)':<50}")
    for cls in sorted(by_class):
        sl_rows = [r for r in by_class[cls] if (r["exit_reason"] or "") == "SL_HIT"]
        hrs = [r["holding_period_hours"] for r in sl_rows if r["holding_period_hours"] is not None]
        print(f"  {cls:<10} {summarize_values(hrs)}")

    # ----- 2. SL distance % distribution, SL_HIT rate per SL% bucket -----
    print_h("2. SL distance as % of entry, and SL_HIT rate per SL% bucket")
    print("  (if SL_HIT rate is higher in the TIGHT bucket, SL is too close)")
    print()
    for cls in sorted(by_class):
        cls_rows = by_class[cls]
        sl_pcts = [(sl_pct_of_entry(r), r) for r in cls_rows]
        sl_pcts = [(p, r) for p, r in sl_pcts if p is not None]
        if len(sl_pcts) < 4:
            print(f"  {cls} (n={len(sl_pcts)}): skipped, n<4")
            continue
        pcts_only = sorted(p for p, _ in sl_pcts)
        n = len(pcts_only)
        t1 = pcts_only[n // 3]
        t2 = pcts_only[(2 * n) // 3]

        buckets: dict[str, list[sqlite3.Row]] = {"TIGHT": [], "MID": [], "WIDE": []}
        for p, r in sl_pcts:
            if p <= t1:
                buckets["TIGHT"].append(r)
            elif p <= t2:
                buckets["MID"].append(r)
            else:
                buckets["WIDE"].append(r)

        print(f"  {cls} (n={n}, tercile cuts: {t1:.2f}% / {t2:.2f}%):")
        for name in ("TIGHT", "MID", "WIDE"):
            br = buckets[name]
            if not br:
                print(f"    {name:<6} n=0")
                continue
            sl_hits = sum(1 for r in br if (r["exit_reason"] or "") == "SL_HIT")
            tp_hits = sum(1 for r in br if (r["exit_reason"] or "") == "TP_HIT")
            avg_r = statistics.mean(r["r_multiple"] for r in br if r["r_multiple"] is not None)
            print(
                f"    {name:<6} n={len(br):<4} SL_HIT={pct_of(sl_hits, len(br))}"
                f"  TP_HIT={pct_of(tp_hits, len(br))}  avg_r={avg_r:+.3f}"
            )

    # ----- 3. SL_HIT rate by regime -----
    print_h("3. SL_HIT rate by regime per asset_class")
    print("  (if RANGING SL_HIT >> TRENDING, regime-aware SL widening is the fix)")
    print()
    for cls in sorted(by_class):
        cls_rows = by_class[cls]
        by_regime = group_by(cls_rows, "regime")
        print(f"  {cls}:")
        for regime in sorted(by_regime):
            rr = by_regime[regime]
            sl_hits = sum(1 for r in rr if (r["exit_reason"] or "") == "SL_HIT")
            tp_hits = sum(1 for r in rr if (r["exit_reason"] or "") == "TP_HIT")
            wins = sum(1 for r in rr if r["r_multiple"] is not None and r["r_multiple"] > 0)
            print(
                f"    {regime:<18} n={len(rr):<4} SL_HIT={pct_of(sl_hits, len(rr))}"
                f"  TP_HIT={pct_of(tp_hits, len(rr))}  WR={pct_of(wins, len(rr))}"
            )

    # ----- 4. Anomaly rows -----
    print_h("4. Anomaly rows (asset_class='unknown' or |r_multiple| > 10)")
    print()
    anomalies = [
        r for r in rows
        if (r["asset_class"] or "").lower() == "unknown"
        or (r["r_multiple"] is not None and abs(r["r_multiple"]) > 10)
    ]
    if not anomalies:
        print("  (none)")
    else:
        for r in anomalies[:30]:
            print(
                f"  ts={r['ts']} pair={r['pair']} class={r['asset_class']} "
                f"dir={r['direction']} exit={r['exit_reason']} "
                f"entry={r['entry_price']} sl={r['sl']} tp={r['tp']} "
                f"exit_px={r['exit_price']} pnl={r['pnl']} r={r['r_multiple']}"
            )
        if len(anomalies) > 30:
            print(f"  ... ({len(anomalies) - 30} more rows)")

    print()
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
