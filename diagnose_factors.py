"""Read-only Engine A factor-to-outcome probe.

Parses audit_log.factors_json and asks: which individual factor's value
correlates negatively with realized R? That factor is the prime suspect
for a sign / polarity bug.

Caveat — historical data is sparse. Only rows written AFTER the camelCase
audit-writer fix (athena.py:4788 / 6050) will have populated factor scores.
The script tolerates partial data and flags low-sample results.

Usage:
    python diagnose_factors.py [path/to/audit.db]

Never writes.
"""

from __future__ import annotations

import argparse
import json
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


def load_factor_rows(conn: sqlite3.Connection) -> list[dict]:
    """Closed trades with populated factor scores only."""
    sql = """
        SELECT factors_json, score, r_multiple, direction, asset_class, engine, style
        FROM audit_log
        WHERE exit_price IS NOT NULL
          AND r_multiple IS NOT NULL
          AND factors_json IS NOT NULL
    """
    out: list[dict] = []
    for r in conn.execute(sql):
        try:
            fj = json.loads(r["factors_json"])
        except Exception:
            continue
        scores = fj.get("scores")
        if not scores or not isinstance(scores, dict):
            continue
        out.append({
            "scores": scores,
            "r_multiple": float(r["r_multiple"]),
            "direction": (r["direction"] or "").upper(),
            "asset_class": r["asset_class"],
            "engine": r["engine"],
            "style": r["style"],
            "raw_score": r["score"],
        })
    return out


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = sum((a - mx) ** 2 for a in xs) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def factor_report(rows: list[dict], title: str) -> None:
    print(f"\n=== {title} (n={len(rows)}) ===")
    if len(rows) < 5:
        print(f"  sample too small — skipping")
        return

    # Collect factor values per key across all rows (skip keys present in <60% of rows)
    all_keys: set[str] = set()
    for r in rows:
        all_keys.update(r["scores"].keys())

    results: list[tuple[str, int, float, float, float]] = []
    for k in sorted(all_keys):
        xs: list[float] = []
        ys: list[float] = []
        for r in rows:
            v = r["scores"].get(k)
            if v is None:
                continue
            try:
                xf = float(v)
            except (TypeError, ValueError):
                continue
            xs.append(xf)
            ys.append(r["r_multiple"])
        if len(xs) < max(5, int(0.6 * len(rows))):
            continue
        if len(set(xs)) < 2:
            continue  # constant factor, no info
        rho = pearson(xs, ys)
        mean_x = statistics.mean(xs)
        stdev_x = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        results.append((k, len(xs), rho, mean_x, stdev_x))

    if not results:
        print("  no factor keys with usable variance and coverage")
        return

    results.sort(key=lambda t: t[2])  # most negative correlation first

    print(f"  {'factor':<28} {'n':>4} {'corr(R)':>9} {'mean':>10} {'std':>10}")
    print(f"  {'-' * 28} {'-' * 4} {'-' * 9} {'-' * 10} {'-' * 10}")
    for k, n, rho, m, s in results:
        flag = ""
        if abs(rho) >= 0.25 and n >= 10:
            flag = " <-- SUSPECT" if rho < 0 else " <-- PROTECTIVE"
        print(f"  {k:<28} {n:>4} {rho:>+8.3f} {m:>+10.3f} {s:>10.3f}{flag}")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("db", nargs="?", default="audit.db")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found", file=sys.stderr)
        return 2

    conn = open_readonly(db_path)
    rows = load_factor_rows(conn)
    total_closed = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE exit_price IS NOT NULL AND r_multiple IS NOT NULL"
    ).fetchone()[0]

    print(f"Closed trades in audit_log: {total_closed}")
    print(f"Rows with populated factors: {len(rows)}")
    if total_closed:
        cov = len(rows) / total_closed
        print(f"Coverage: {cov:.1%}")
        if cov < 0.3:
            print(
                "  NOTE: coverage is low. Most historical rows pre-date the\n"
                "  camelCase audit-writer fix (athena.py:4788 / 6050). New\n"
                "  trades after the fix will populate factor scores correctly."
            )

    if not rows:
        print("No factor-populated rows available. Run a few live trades or an\n"
              "instrumented backtest with factor-dump enabled, then re-run.")
        return 0

    # By engine
    by_engine: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["engine"] or "UNKNOWN") + "/" + (r["style"] or "?")
        by_engine[key].append(r)

    factor_report(rows, "ALL POPULATED")

    for k in sorted(by_engine.keys()):
        factor_report(by_engine[k], k)

    print(
        "\nReading guide:\n"
        "  corr(R) = Pearson correlation of factor value vs R-multiple.\n"
        "  Negative corr with |rho| >= 0.25 on n>=10 => SUSPECT factor\n"
        "    (high values push score up but align with losers).\n"
        "  Positive corr => factor is doing its job (protective).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
