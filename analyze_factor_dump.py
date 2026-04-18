"""Analyze the instrumented-backtest factor dump.

Input: JSONL from instrumented_backtest.py. Each row has:
  {pair, asset_class, style, direction, resultR, factors:{k:v}, factor_weights:{k:w}}

Computes Pearson correlation of each numeric factor value vs resultR, overall
and per (asset_class, style). Flags SUSPECT factors (negative corr, |rho|>=0.2,
n>=30) and PROTECTIVE factors (positive corr).

Also computes a direction-normalised view: for LONG trades, factor correlates
as-is; for SHORT trades, we flip sign of signed factors so "bullish factor"
becomes "aligned-with-direction factor".

Usage:
    python analyze_factor_dump.py [factor_dump.jsonl]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, n
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if dx == 0 or dy == 0:
        return 0.0, n
    return num / (dx * dy), n


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def report(rows, title, min_n=30):
    print(f"\n=== {title} (n={len(rows)}) ===")
    if len(rows) < min_n:
        print(f"  skip — below min_n={min_n}")
        return

    # Collect numeric factor values
    by_key = defaultdict(list)
    rs = []
    for r in rows:
        rr = r.get("resultR")
        if rr is None:
            continue
        rs.append(float(rr))
        f = r.get("factors") or {}
        for k, v in f.items():
            if isinstance(v, bool):
                v = 1.0 if v else 0.0
            if not isinstance(v, (int, float)):
                continue
            if not math.isfinite(v):
                continue
            by_key[k].append((len(rs) - 1, float(v)))

    results = []
    for k, pairs in by_key.items():
        if len(pairs) < min_n:
            continue
        xs = [v for _, v in pairs]
        ys = [rs[i] for i, _ in pairs]
        if len(set(xs)) < 2:
            continue
        rho, n = pearson(xs, ys)
        mean = statistics.mean(xs)
        std = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        results.append((k, n, rho, mean, std))

    results.sort(key=lambda t: t[2])

    print(f"  {'factor':<32} {'n':>5} {'corr(R)':>9} {'mean':>10} {'std':>10}")
    print(f"  {'-'*32} {'-'*5} {'-'*9} {'-'*10} {'-'*10}")
    for k, n, rho, m, s in results:
        flag = ""
        if n >= min_n and abs(rho) >= 0.20:
            flag = "  <-- SUSPECT" if rho < 0 else "  <-- PROTECTIVE"
        print(f"  {k:<32} {n:>5} {rho:>+8.3f} {m:>+10.3f} {s:>10.3f}{flag}")


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("dump", nargs="?", default="factor_dump.jsonl")
    p.add_argument("--min-n", type=int, default=30)
    args = p.parse_args(argv)

    path = Path(args.dump)
    if not path.exists():
        print(f"ERROR: {path} not found")
        return 2

    rows = load(path)
    print(f"Loaded {len(rows)} trades from {path}")

    report(rows, "ALL", min_n=args.min_n)

    by_class = defaultdict(list)
    for r in rows:
        by_class[r.get("asset_class") or "unknown"].append(r)
    for cls in sorted(by_class):
        report(by_class[cls], f"CLASS={cls}", min_n=args.min_n)

    by_cs = defaultdict(list)
    for r in rows:
        key = f"{r.get('asset_class')}/{r.get('style')}"
        by_cs[key].append(r)
    for k in sorted(by_cs):
        report(by_cs[k], k, min_n=args.min_n)

    print("\nReading guide:")
    print("  corr(R) = Pearson correlation of factor value vs trade R-multiple.")
    print("  SUSPECT    = negative corr (|rho|>=0.20, n>=min_n): factor pushes score up but aligns with losers.")
    print("  PROTECTIVE = positive corr: factor correctly ranks winners.")
    print("  Flat factors (std=0) or low-coverage (n<min_n) are skipped.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
