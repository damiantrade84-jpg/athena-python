"""Read-only Engine A style diagnostic.

Follow-up to diagnose_edge.py and diagnose_exits.py. Breaks down closed
Engine A trades by (asset_class × style) to separate two hypotheses:

  H1 — "Scalp style is dragging the class"
       Sharp asymmetry: scalp SL_HIT >> swing SL_HIT, scalp avg_R << swing.
       Action: tighten or disable scalp style (class-scoped).

  H2 — "Entry timing is broken across all styles"
       WR/avg_R roughly flat across scalp/intraday/swing within each class.
       Action: score doesn't discriminate — redesign entry logic, not gates.

Usage:
    python diagnose_style.py [path/to/audit.db]

Never writes. Opens audit.db in read-only URI mode.
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


def wr(rows: list[sqlite3.Row]) -> float:
    if not rows:
        return 0.0
    wins = sum(1 for r in rows if (r["r_multiple"] or 0) > 0)
    return wins / len(rows)


def avg_r(rows: list[sqlite3.Row]) -> float:
    vals = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    return statistics.mean(vals) if vals else 0.0


def share(rows: list[sqlite3.Row], reason: str) -> float:
    if not rows:
        return 0.0
    hits = sum(
        1 for r in rows if str(r["exit_reason"] or "").upper() == reason.upper()
    )
    return hits / len(rows)


def median_hold(rows: list[sqlite3.Row]) -> float:
    vals = [
        r["holding_period_hours"]
        for r in rows
        if r["holding_period_hours"] is not None
    ]
    return statistics.median(vals) if vals else 0.0


def print_table(title: str, rows_by_key: dict[tuple, list[sqlite3.Row]]) -> None:
    print(f"\n=== {title} ===")
    print(
        f"{'asset_class':<12} {'style':<10} {'n':>5} "
        f"{'WR':>7} {'SL%':>7} {'TP%':>7} {'avgR':>8} {'medHold':>9}"
    )
    print("-" * 72)
    for key in sorted(rows_by_key.keys()):
        rows = rows_by_key[key]
        ac, style = key
        print(
            f"{ac:<12} {str(style):<10} {len(rows):>5} "
            f"{wr(rows):>6.1%} {share(rows, 'SL_HIT'):>6.1%} "
            f"{share(rows, 'TP_HIT'):>6.1%} {avg_r(rows):>+7.3f}R "
            f"{median_hold(rows):>8.2f}h"
        )


def print_class_totals(rows_by_class: dict[str, list[sqlite3.Row]]) -> None:
    print("\n=== CLASS TOTALS (all styles combined) ===")
    print(
        f"{'asset_class':<12} {'n':>5} {'WR':>7} {'SL%':>7} "
        f"{'TP%':>7} {'avgR':>8} {'medHold':>9}"
    )
    print("-" * 64)
    for ac in sorted(rows_by_class.keys()):
        rows = rows_by_class[ac]
        print(
            f"{ac:<12} {len(rows):>5} {wr(rows):>6.1%} "
            f"{share(rows, 'SL_HIT'):>6.1%} {share(rows, 'TP_HIT'):>6.1%} "
            f"{avg_r(rows):>+7.3f}R {median_hold(rows):>8.2f}h"
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "db",
        nargs="?",
        default="audit.db",
        help="Path to audit.db (default: ./audit.db)",
    )
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

    by_class_style: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    by_class: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        ac = (r["asset_class"] or "unknown").lower()
        style = (r["style"] or "unknown").lower()
        by_class_style[(ac, style)].append(r)
        by_class[ac].append(r)

    print_class_totals(by_class)
    print_table("CLASS x STYLE BREAKDOWN", by_class_style)

    # Asymmetry check within each class: biggest SL_HIT gap across styles
    print("\n=== STYLE DISPERSION WITHIN CLASS ===")
    print("(|max SL% - min SL%| across styles; large => H1; small => H2)")
    print(f"{'asset_class':<12} {'styles':<30} {'SL% range':>12} {'avgR range':>12}")
    print("-" * 70)
    for ac in sorted(by_class.keys()):
        style_groups = {
            k[1]: v for k, v in by_class_style.items() if k[0] == ac and len(v) >= 5
        }
        if len(style_groups) < 2:
            continue
        sl_rates = {s: share(rs, "SL_HIT") for s, rs in style_groups.items()}
        avg_rs = {s: avg_r(rs) for s, rs in style_groups.items()}
        sl_span = max(sl_rates.values()) - min(sl_rates.values())
        r_span = max(avg_rs.values()) - min(avg_rs.values())
        styles_str = ",".join(f"{s}({len(style_groups[s])})" for s in sorted(style_groups))
        print(
            f"{ac:<12} {styles_str:<30} {sl_span:>11.1%} {r_span:>+11.3f}R"
        )

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
