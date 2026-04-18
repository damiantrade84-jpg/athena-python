"""Read-only Engine A score-to-outcome correlation probe.

Final diagnostic in the edge-investigation chain. Answers three questions:

  Q1 — Does raw Engine A score rank winners above losers?
       Spearman rank correlation between `score` and `r_multiple`.
       |rho| < 0.05 => scorer is ranking noise.

  Q2 — Is WR monotonic across score quintiles?
       Q1 (lowest score) ... Q5 (highest). If Q5 WR is not higher than Q1,
       the score provides no admission-filter value.

  Q3 — Even without SL/TP mechanics, does signed direction predict
       price-move direction?
       For LONG: did exit_price > entry_price? For SHORT: the reverse.
       This isolates prediction quality from exit-management noise.

Usage:
    python diagnose_score_direction.py [path/to/audit.db]

Never writes. Opens audit.db in read-only URI mode.
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path


MIN_CELL_N = 20


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
          AND score IS NOT NULL
          AND entry_price IS NOT NULL
          AND (grade IS NULL OR grade NOT LIKE 'AUTO-ERR%')
    """
    return list(conn.execute(sql))


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation without scipy."""
    n = len(xs)
    if n < 3:
        return 0.0

    def rank(vals: list[float]) -> list[float]:
        indexed = sorted(enumerate(vals), key=lambda t: t[1])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(indexed):
            j = i
            while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-based average rank for ties
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    rx = rank(xs)
    ry = rank(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def quintile_wr(rows: list[sqlite3.Row]) -> list[tuple[int, int, float, float]]:
    """Sort by score, split into 5 buckets, return (q, n, wr, avg_score) per q."""
    rows_sorted = sorted(rows, key=lambda r: r["score"])
    n = len(rows_sorted)
    if n < 10:
        return []
    out: list[tuple[int, int, float, float]] = []
    for q in range(5):
        lo = q * n // 5
        hi = (q + 1) * n // 5 if q < 4 else n
        bucket = rows_sorted[lo:hi]
        if not bucket:
            continue
        wins = sum(1 for r in bucket if (r["r_multiple"] or 0) > 0)
        wr = wins / len(bucket)
        avg_sc = statistics.mean(r["score"] for r in bucket)
        out.append((q + 1, len(bucket), wr, avg_sc))
    return out


def directional_hitrate(rows: list[sqlite3.Row]) -> tuple[int, int, float]:
    """How often did price actually move in the predicted direction?"""
    hits = 0
    total = 0
    for r in rows:
        try:
            entry = float(r["entry_price"])
            exitp = float(r["exit_price"])
        except (TypeError, ValueError):
            continue
        direction = (r["direction"] or "").upper()
        if direction not in ("LONG", "SHORT"):
            continue
        total += 1
        if direction == "LONG" and exitp > entry:
            hits += 1
        elif direction == "SHORT" and exitp < entry:
            hits += 1
    rate = hits / total if total else 0.0
    return hits, total, rate


def wr(rows: list[sqlite3.Row]) -> float:
    if not rows:
        return 0.0
    wins = sum(1 for r in rows if (r["r_multiple"] or 0) > 0)
    return wins / len(rows)


def print_class_block(ac: str, rows: list[sqlite3.Row]) -> None:
    n = len(rows)
    print(f"\n=== {ac.upper()} (n={n}) ===")
    if n < MIN_CELL_N:
        print(f"  sample too small (< {MIN_CELL_N}) — skipping")
        return

    scores = [float(r["score"]) for r in rows]
    r_vals = [float(r["r_multiple"]) for r in rows]
    rho = spearman(scores, r_vals)

    hits, total, dir_rate = directional_hitrate(rows)

    print(f"  Spearman(score, R):        {rho:+.3f}")
    print(f"  Directional hit-rate:      {hits}/{total} = {dir_rate:.1%}")
    print(f"  Overall WR:                {wr(rows):.1%}")
    print(f"  Score range:               [{min(scores):.2f}, {max(scores):.2f}]")

    qs = quintile_wr(rows)
    if qs:
        print(f"  Score quintile WR:")
        print(f"    {'Q':>2} {'n':>5} {'avgScore':>10} {'WR':>7}")
        for q, nn, wrq, avgsc in qs:
            print(f"    Q{q} {nn:>5} {avgsc:>10.3f} {wrq:>6.1%}")
        # monotonic check
        wrs = [q[2] for q in qs]
        mono_up = all(wrs[i] <= wrs[i + 1] for i in range(len(wrs) - 1))
        mono_down = all(wrs[i] >= wrs[i + 1] for i in range(len(wrs) - 1))
        if mono_up:
            print(f"  Monotonic: UP (higher score → higher WR) ✓")
        elif mono_down:
            print(f"  Monotonic: DOWN (higher score → lower WR) ✗ inverted")
        else:
            print(f"  Monotonic: NO (score does not rank WR)")


def print_verdict(per_class: dict[str, list[sqlite3.Row]]) -> None:
    print("\n" + "=" * 60)
    print("VERDICT")
    print("=" * 60)

    all_rho = []
    all_dir = []
    for ac, rows in per_class.items():
        if len(rows) < MIN_CELL_N:
            continue
        scores = [float(r["score"]) for r in rows]
        r_vals = [float(r["r_multiple"]) for r in rows]
        all_rho.append((ac, spearman(scores, r_vals)))
        _, _, dr = directional_hitrate(rows)
        all_dir.append((ac, dr))

    max_abs_rho = max((abs(r) for _, r in all_rho), default=0.0)
    avg_dir = statistics.mean(d for _, d in all_dir) if all_dir else 0.0

    print(f"\nMax |Spearman| across classes: {max_abs_rho:.3f}")
    print(f"Avg directional hit-rate:      {avg_dir:.1%}")

    print()
    if max_abs_rho < 0.05:
        print("=> SCORER-IS-NOISE BRANCH")
        print("   Score does not rank winners above losers at any gate level.")
        print("   Gate tuning cannot rescue. Entry-timing redesign needed.")
    elif avg_dir > 0.55:
        print("=> EXITS-ARE-BROKEN BRANCH")
        print("   Direction prediction works (>55%) but WR is dragged by")
        print("   SL/TP mechanics. Revisit STYLE_ATR_MULTS and exit logic.")
    elif avg_dir < 0.48:
        print("=> DIRECTION-IS-RANDOM BRANCH")
        print("   Signed direction is worse than coin-flip. Scorer has")
        print("   structural bias — sign-flip or model rework needed.")
    else:
        per_class_rhos = ", ".join(f"{ac}={r:+.2f}" for ac, r in all_rho)
        print(f"=> MIXED / PER-CLASS branch: {per_class_rhos}")
        print("   Investigate the class with strongest |rho| first.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?", default="audit.db")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found", file=sys.stderr)
        return 2

    conn = open_readonly(db_path)
    rows = closed_rows(conn)
    if not rows:
        print("No closed trades in audit_log.")
        return 0

    print(f"Loaded {len(rows)} closed Engine A trades from {db_path}")

    per_class: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        ac = (r["asset_class"] or "unknown").lower()
        per_class[ac].append(r)

    # Overall (all classes pooled)
    print_class_block("ALL", rows)
    for ac in sorted(per_class.keys()):
        print_class_block(ac, per_class[ac])

    print_verdict(per_class)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
