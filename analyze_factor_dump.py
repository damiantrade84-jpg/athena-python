"""Analyze the instrumented-backtest factor dump.

Input: JSONL from instrumented_backtest.py. Each row has:
  {pair, asset_class, style, direction, resultR, factors:{k:v}, factor_weights:{k:w}}

Computes Pearson correlation of each numeric factor value vs resultR, overall
and per (asset_class, style). Flags SUSPECT factors (negative corr, |rho|>=0.2,
n>=30) and PROTECTIVE factors (positive corr).

Direction-normalised view: for LONG trades, factor correlates as-is; for SHORT
trades, signed (directional) factors have their sign flipped so "bearish factor
value" maps to "factor aligned with direction." Non-directional factors (timing,
volatility) are left as-is. Signed factors are auto-detected: a factor is signed
if its mean in LONG trades and mean in SHORT trades have opposite signs (threshold
±0.05). Use --no-normalize to disable and see raw correlations.

WARNING: Without direction normalization, directional factors in SHORT slices
show spurious SUSPECT flags. e.g. trend=-1.0 (bearish) on a winning SHORT
(R=+1.0) appears as negative correlation → SUSPECT, even though the factor
is working correctly.

Usage:
    python analyze_factor_dump.py [factor_dump.jsonl]
    python analyze_factor_dump.py --split direction,class_direction
    python analyze_factor_dump.py --no-normalize   # raw correlations only
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


def identify_signed_factors(all_rows: list) -> set:
    """Auto-detect directional (signed) factors.

    A factor is signed if its mean in LONG trades and mean in SHORT trades have
    opposite signs (both must exceed the ±0.05 threshold to avoid noise triggers).
    Non-directional factors (timing ints, volatility ratios that are always
    positive) will have the same sign in both subsets and are excluded.
    """
    long_rows = [r for r in all_rows if (r.get("direction") or "").upper() == "LONG"]
    short_rows = [r for r in all_rows if (r.get("direction") or "").upper() == "SHORT"]
    if not long_rows or not short_rows:
        return set()

    all_keys: set = set()
    for r in all_rows:
        all_keys.update((r.get("factors") or {}).keys())

    signed: set = set()
    for key in all_keys:
        def _vals(rset):
            out = []
            for r in rset:
                v = (r.get("factors") or {}).get(key)
                if isinstance(v, bool):
                    v = 1.0 if v else 0.0
                if isinstance(v, (int, float)) and math.isfinite(float(v)):
                    out.append(float(v))
            return out

        lv = _vals(long_rows)
        sv = _vals(short_rows)
        if len(lv) < 5 or len(sv) < 5:
            continue
        lm = sum(lv) / len(lv)
        sm = sum(sv) / len(sv)
        if lm > 0.05 and sm < -0.05:
            signed.add(key)
        elif lm < -0.05 and sm > 0.05:
            signed.add(key)
    return signed


def normalize_rows(rows: list, signed_factors: set) -> list:
    """Return new rows with directional factor signs flipped for SHORT trades."""
    out = []
    for r in rows:
        direction = (r.get("direction") or "").upper()
        if direction == "SHORT" and signed_factors:
            new_f = {}
            for k, v in (r.get("factors") or {}).items():
                if k in signed_factors and isinstance(v, (int, float)) and math.isfinite(float(v)):
                    new_f[k] = -float(v)
                else:
                    new_f[k] = v
            out.append({**r, "factors": new_f})
        else:
            out.append(r)
    return out


def _wr_hit(rows):
    if not rows:
        return 0.0, 0
    r_vals = [float(r.get("resultR")) for r in rows if r.get("resultR") is not None]
    if not r_vals:
        return 0.0, 0
    wins = sum(1 for v in r_vals if v > 0)
    return wins / len(r_vals), len(r_vals)


def _summary(rows, label):
    wr, n = _wr_hit(rows)
    r_vals = [float(r.get("resultR")) for r in rows if r.get("resultR") is not None]
    if not r_vals:
        print(f"  {label:<32} n={n:>4} (no resultR)")
        return
    avg = sum(r_vals) / len(r_vals)
    print(
        f"  {label:<32} n={n:>4}  WR={wr*100:>5.1f}%  avgR={avg:+.3f}"
    )


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("dump", nargs="?", default="factor_dump.jsonl")
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument(
        "--split",
        default="class,class_style",
        help=(
            "Comma-separated splits to report. Options: class, class_style, "
            "direction, regime, class_direction, class_regime, direction_regime."
        ),
    )
    p.add_argument(
        "--no-normalize",
        action="store_true",
        help="Disable direction normalization (show raw correlations). "
             "Without normalization, directional factors in SHORT slices show "
             "spurious SUSPECT flags — see module docstring.",
    )
    args = p.parse_args(argv)

    path = Path(args.dump)
    if not path.exists():
        print(f"ERROR: {path} not found")
        return 2

    rows = load(path)
    print(f"Loaded {len(rows)} trades from {path}")

    # Direction normalization setup
    normalize = not args.no_normalize
    signed_factors: set = set()
    if normalize:
        signed_factors = identify_signed_factors(rows)
        if signed_factors:
            print(f"\nAuto-detected signed (directional) factors: {sorted(signed_factors)}")
            print("  These will have sign flipped for SHORT trades in direction splits.")
        else:
            print("\nNo signed factors detected (or insufficient LONG/SHORT split). "
                  "Direction normalization inactive.")

    def _report(subset, title):
        report(subset, title, min_n=args.min_n)

    def _report_normalized(subset, title):
        """Report with direction normalization applied."""
        if normalize and signed_factors:
            norm_subset = normalize_rows(subset, signed_factors)
            report(norm_subset, title + " [dir-normalized]", min_n=args.min_n)
        else:
            report(subset, title, min_n=args.min_n)

    # Top-line WR / avgR sanity per split dimension — cheap and extremely useful
    print("\n=== WR / avgR summary ===")
    _summary(rows, "ALL")
    for cls in sorted({r.get("asset_class") or "unknown" for r in rows}):
        _summary([r for r in rows if (r.get("asset_class") or "unknown") == cls], f"class={cls}")
    for d in sorted({r.get("direction") or "UNKNOWN" for r in rows}):
        _summary([r for r in rows if (r.get("direction") or "UNKNOWN") == d], f"direction={d}")
    for reg in sorted({r.get("regime") or "UNKNOWN" for r in rows}):
        _summary([r for r in rows if (r.get("regime") or "UNKNOWN") == reg], f"regime={reg}")
    for cls in sorted({r.get("asset_class") or "unknown" for r in rows}):
        for d in sorted({r.get("direction") or "UNKNOWN" for r in rows}):
            subset = [
                r for r in rows
                if (r.get("asset_class") or "unknown") == cls
                and (r.get("direction") or "UNKNOWN") == d
            ]
            if subset:
                _summary(subset, f"{cls}/{d}")

    # Overall report — direction-normalized (direction is mixed, normalization applies on SHORT)
    _report_normalized(rows, "ALL")

    splits = [s.strip() for s in args.split.split(",") if s.strip()]

    if "class" in splits:
        by_class = defaultdict(list)
        for r in rows:
            by_class[r.get("asset_class") or "unknown"].append(r)
        for cls in sorted(by_class):
            _report_normalized(by_class[cls], f"CLASS={cls}")

    if "class_style" in splits:
        by_cs = defaultdict(list)
        for r in rows:
            key = f"{r.get('asset_class')}/{r.get('style')}"
            by_cs[key].append(r)
        for k in sorted(by_cs):
            _report_normalized(by_cs[k], k)

    if "direction" in splits:
        by_dir = defaultdict(list)
        for r in rows:
            by_dir[r.get("direction") or "UNKNOWN"].append(r)
        # Direction splits: both raw and normalized side-by-side so artifacts are visible
        for d in sorted(by_dir):
            _report(by_dir[d], f"DIRECTION={d} [raw]")
            if normalize and signed_factors and d == "SHORT":
                _report_normalized(by_dir[d], f"DIRECTION={d}")

    if "regime" in splits:
        by_reg = defaultdict(list)
        for r in rows:
            by_reg[r.get("regime") or "UNKNOWN"].append(r)
        for reg in sorted(by_reg):
            _report_normalized(by_reg[reg], f"REGIME={reg}")

    if "class_direction" in splits:
        by_cd = defaultdict(list)
        for r in rows:
            key = f"{r.get('asset_class')}/{r.get('direction')}"
            by_cd[key].append(r)
        for k in sorted(by_cd):
            is_short = k.endswith("/SHORT")
            if is_short:
                _report(by_cd[k], f"CLASS/DIR={k} [raw]")
                _report_normalized(by_cd[k], f"CLASS/DIR={k}")
            else:
                _report(by_cd[k], f"CLASS/DIR={k}")

    if "class_regime" in splits:
        by_cr = defaultdict(list)
        for r in rows:
            key = f"{r.get('asset_class')}/{r.get('regime')}"
            by_cr[key].append(r)
        for k in sorted(by_cr):
            _report_normalized(by_cr[k], f"CLASS/REGIME={k}")

    if "direction_regime" in splits:
        by_dr = defaultdict(list)
        for r in rows:
            key = f"{r.get('direction')}/{r.get('regime')}"
            by_dr[key].append(r)
        for k in sorted(by_dr):
            is_short = k.startswith("SHORT/")
            if is_short:
                _report_normalized(by_dr[k], f"DIR/REGIME={k}")
            else:
                _report(by_dr[k], f"DIR/REGIME={k}")

    print("\nReading guide:")
    print("  corr(R) = Pearson correlation of factor value vs trade R-multiple.")
    print("  SUSPECT    = negative corr (|rho|>=0.20, n>=min_n): factor pushes score up but aligns with losers.")
    print("  PROTECTIVE = positive corr: factor correctly ranks winners.")
    print("  [dir-normalized] = signed factors have sign flipped for SHORT trades.")
    print("  [raw] = raw values without normalization (shown for SHORT slices for comparison).")
    print("  Flat factors (std=0) or low-coverage (n<min_n) are skipped.")
    print("  WR summary is unfiltered by min_n — use it to see if a split is worth reading.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
