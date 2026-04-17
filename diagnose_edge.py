"""Read-only Engine A edge diagnostic.

Answers three questions about closed live trades in ``audit.db``:

  1. Does WR rise with entry score?        (WR by score quartile per class)
  2. Is SL hitting before TP?              (exit-reason mix per class)
  3. Is one direction failing more?        (WR by LONG/SHORT per class)

Usage:
    python diagnose_edge.py [path/to/audit.db]

Never writes.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def closed_engine_a_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Closed Engine A trades with usable score + r_multiple.

    Filters:
      - exit_price IS NOT NULL (closed)
      - r_multiple IS NOT NULL (outcome known)
      - score IS NOT NULL
      - grade NOT LIKE 'AUTO-ERR%' (exclude failed executions)
    """
    sql = """
        SELECT pair, asset_class, direction, style, score, max_score,
               regime, exit_reason, r_multiple, pnl, grade
        FROM audit_log
        WHERE exit_price IS NOT NULL
          AND r_multiple IS NOT NULL
          AND score IS NOT NULL
          AND (grade IS NULL OR grade NOT LIKE 'AUTO-ERR%')
    """
    return list(conn.execute(sql))


def wr_pf(rows: list[sqlite3.Row]) -> tuple[float, float, int]:
    n = len(rows)
    if not n:
        return 0.0, 0.0, 0
    wins = [r["r_multiple"] for r in rows if r["r_multiple"] > 0]
    losses = [r["r_multiple"] for r in rows if r["r_multiple"] <= 0]
    wr = 100.0 * len(wins) / n
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    pf = (gross_w / gross_l) if gross_l > 0 else float("inf")
    return wr, pf, n


def group_by(rows: list[sqlite3.Row], key: str) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        k = r[key] or "unknown"
        out[k].append(r)
    return out


def score_quartile(score: float, q: tuple[float, float, float]) -> str:
    q25, q50, q75 = q
    if score < q25:
        return "Q1 (low)"
    if score < q50:
        return "Q2"
    if score < q75:
        return "Q3"
    return "Q4 (high)"


def quartile_cuts(rows: list[sqlite3.Row]) -> tuple[float, float, float]:
    vals = sorted(r["score"] for r in rows)
    n = len(vals)
    if n < 4:
        return (0.0, 0.0, 0.0)
    return (vals[n // 4], vals[n // 2], vals[(3 * n) // 4])


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
    rows = closed_engine_a_rows(conn)
    if not rows:
        print("No closed Engine A rows in audit_log. Nothing to analyze.")
        return 0

    print(f"Analyzing {len(rows)} closed Engine A trades from {db_path}")

    # ----- 1. WR by score quartile per class -----
    print_h("1. WR by score quartile per asset_class")
    print("  (if WR rises Q1 -> Q4, score is predictive; if flat, scoring doesn't discriminate)")
    print()
    by_class = group_by(rows, "asset_class")
    header = f"  {'class':<10} {'n':>4}  {'Q1':>15} {'Q2':>15} {'Q3':>15} {'Q4':>15}"
    print(header)
    for cls in sorted(by_class):
        cls_rows = by_class[cls]
        if len(cls_rows) < 4:
            print(f"  {cls:<10} {len(cls_rows):>4}  (n<4, skipped)")
            continue
        q = quartile_cuts(cls_rows)
        buckets: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for r in cls_rows:
            buckets[score_quartile(r["score"], q)].append(r)
        parts = []
        for label in ("Q1 (low)", "Q2", "Q3", "Q4 (high)"):
            br = buckets.get(label, [])
            if not br:
                parts.append("     n=0      ")
                continue
            wr, pf, n = wr_pf(br)
            parts.append(f"{wr:>4.0f}% n={n:<3} pf{pf:<4.2f}")
        print(f"  {cls:<10} {len(cls_rows):>4}  " + " ".join(f"{p:<14}" for p in parts))
        print(f"    cuts: q25={q[0]:.3f}  q50={q[1]:.3f}  q75={q[2]:.3f}")

    # ----- 2. Exit reason mix per class -----
    print_h("2. Exit-reason mix per asset_class")
    print("  (if SL_HIT >> TP_HIT, exits are the problem — tight SL or bad tp placement)")
    print()
    for cls in sorted(by_class):
        cls_rows = by_class[cls]
        reason_counts: dict[str, int] = defaultdict(int)
        reason_wr: dict[str, list[float]] = defaultdict(list)
        for r in cls_rows:
            reason = r["exit_reason"] or "UNKNOWN"
            reason_counts[reason] += 1
            reason_wr[reason].append(r["r_multiple"])
        print(f"  {cls} (n={len(cls_rows)}):")
        for reason in sorted(reason_counts, key=lambda k: -reason_counts[k]):
            c = reason_counts[reason]
            pct = 100.0 * c / len(cls_rows)
            rs = reason_wr[reason]
            avg_r = sum(rs) / len(rs) if rs else 0.0
            print(f"    {reason:<20} {c:>4} ({pct:>4.1f}%)  avg_r={avg_r:+.3f}")

    # ----- 3. WR by direction per class -----
    print_h("3. WR by direction per asset_class")
    print("  (if LONG and SHORT both < 50%, it's not a direction bias — it's edge absence)")
    print()
    print(f"  {'class':<10} {'LONG':>20} {'SHORT':>20}")
    for cls in sorted(by_class):
        cls_rows = by_class[cls]
        longs = [r for r in cls_rows if (r["direction"] or "").upper() == "LONG"]
        shorts = [r for r in cls_rows if (r["direction"] or "").upper() == "SHORT"]
        lwr, lpf, ln = wr_pf(longs)
        swr, spf, sn = wr_pf(shorts)
        lstr = f"{lwr:>4.0f}% n={ln:<3} pf{lpf:<4.2f}"
        sstr = f"{swr:>4.0f}% n={sn:<3} pf{spf:<4.2f}"
        print(f"  {cls:<10} {lstr:>20} {sstr:>20}")

    print()
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
