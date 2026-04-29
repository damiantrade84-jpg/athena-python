"""audit_engine_d_funnel.py — Engine D funnel JSONL summarizer.

Implements §7.1 of plans/engine-d-scoring-ai-review-audit-8577d0.md.

Reads logs/scalp_audit/engine_d_funnel.jsonl and reports:
  - Top skip reasons by count
  - Skip-reason × asset_type matrix
  - Pass / Watchlist / Blocked / NO_SETUP counts
  - Per-pair fail-reason distribution

Read-only.  Does NOT touch live engine state, thresholds, or signals.

Usage:
  python tools/audit_engine_d_funnel.py
  python tools/audit_engine_d_funnel.py --since 2026-04-28T00:00:00+00:00
  python tools/audit_engine_d_funnel.py --symbol BTC/USDT
  python tools/audit_engine_d_funnel.py --json   # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "logs" / "scalp_audit" / "engine_d_funnel.jsonl"


def _iter_rows(path: Path, since: str | None, symbol: str | None) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if since and str(row.get("timestamp", "")) < since:
                continue
            if symbol and row.get("symbol") != symbol:
                continue
            rows.append(row)
    return rows


def _primary_skip_reason(row: dict) -> str:
    fails = row.get("fail_reasons") or []
    if isinstance(fails, list) and fails:
        return str(fails[0])
    return str(row.get("gate_result", "unknown"))


def _summarize(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"total": 0}

    by_gate: Counter = Counter()
    by_asset: Counter = Counter()
    by_skip: Counter = Counter()
    by_skip_asset: dict[tuple[str, str], int] = defaultdict(int)
    grade_dist: Counter = Counter()
    setup_dist: Counter = Counter()
    rr_below_min = 0
    no_data = 0
    rows_per_symbol: Counter = Counter()

    for r in rows:
        gate = str(r.get("gate_result", "unknown"))
        asset = str(r.get("asset_type", "unknown"))
        by_gate[gate] += 1
        by_asset[asset] += 1
        rows_per_symbol[str(r.get("symbol", "?"))] += 1

        if gate not in ("PASS",):
            reason = _primary_skip_reason(r)
            by_skip[reason] += 1
            by_skip_asset[(reason, asset)] += 1

        g = r.get("setup_grade")
        if g:
            grade_dist[str(g)] += 1
        s = r.get("setup_type")
        if s:
            setup_dist[str(s)] += 1
        if r.get("rr_ok") is False:
            rr_below_min += 1
        if r.get("data_available") is False:
            no_data += 1

    return {
        "total": len(rows),
        "by_gate_result": dict(by_gate.most_common()),
        "by_asset_type": dict(by_asset.most_common()),
        "top_skip_reasons": dict(by_skip.most_common(15)),
        "skip_reason_x_asset": {
            f"{reason} | {asset}": count
            for (reason, asset), count in sorted(
                by_skip_asset.items(), key=lambda kv: -kv[1]
            )[:30]
        },
        "setup_grade_distribution": dict(grade_dist.most_common()),
        "setup_type_distribution": dict(setup_dist.most_common()),
        "rr_below_min_count": rr_below_min,
        "data_unavailable_count": no_data,
        "unique_symbols": len(rows_per_symbol),
        "top_symbols_by_rows": dict(rows_per_symbol.most_common(10)),
    }


def _print_human(summary: dict[str, Any], path: Path) -> None:
    if summary.get("total", 0) == 0:
        print(f"[audit_engine_d_funnel] no rows found at {path}")
        return

    print(f"\n=== Engine D Funnel Audit — {summary['total']} rows from {path.name} ===\n")

    print("--- Gate Result ---")
    for k, v in summary["by_gate_result"].items():
        print(f"  {k:30s} {v:6d}")

    print("\n--- Asset Type ---")
    for k, v in summary["by_asset_type"].items():
        print(f"  {k:30s} {v:6d}")

    print("\n--- Top Skip Reasons ---")
    for k, v in summary["top_skip_reasons"].items():
        print(f"  {k:60s} {v:6d}")

    print("\n--- Skip Reason × Asset Type (top 30) ---")
    for k, v in summary["skip_reason_x_asset"].items():
        print(f"  {k:80s} {v:6d}")

    print("\n--- Setup Grade Distribution (where present) ---")
    for k, v in summary["setup_grade_distribution"].items():
        print(f"  {k:5s} {v:6d}")

    print("\n--- Setup Type Distribution (where present) ---")
    for k, v in summary["setup_type_distribution"].items():
        print(f"  {k:25s} {v:6d}")

    print(f"\nrr_below_min count: {summary['rr_below_min_count']}")
    print(f"data_unavailable count: {summary['data_unavailable_count']}")
    print(f"unique symbols: {summary['unique_symbols']}")

    print("\n--- Top symbols by row count ---")
    for k, v in summary["top_symbols_by_rows"].items():
        print(f"  {k:25s} {v:6d}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH,
                        help="Path to engine_d_funnel.jsonl")
    parser.add_argument("--since", type=str, default=None,
                        help="ISO-8601 timestamp lower bound (e.g. 2026-04-28T00:00:00+00:00)")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Filter to a single symbol (e.g. BTC/USDT)")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON summary")
    args = parser.parse_args(argv)

    rows = _iter_rows(args.path, args.since, args.symbol)
    summary = _summarize(rows)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_human(summary, args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
