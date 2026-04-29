"""audit_ai_review_engine_d_state.py — Quantify Engine D blindness in AI reviews.

Implements §7.2 of plans/engine-d-scoring-ai-review-audit-8577d0.md.

The audit claim: every `log_ai_review` call passes `engine_d_state=None`, so
Marcus / Engine B AI / Vision / Signal Debate never see Engine D context.
This script verifies the claim against the actual log.

Reads logs/ai_review/ai_review_audit.jsonl and reports, per review_type:
  - total reviews
  - engine_d_state=None percentage  (expected: 100% per audit §4.1)
  - engine_a/b/c_state availability (sanity check)
  - parse_success / schema_valid rates
  - ai_changed_execution_permission rate

Read-only.

Usage:
  python tools/audit_ai_review_engine_d_state.py
  python tools/audit_ai_review_engine_d_state.py --since 2026-04-01T00:00:00+00:00
  python tools/audit_ai_review_engine_d_state.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "logs" / "ai_review" / "ai_review_audit.jsonl"


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


def _pct(num: int, denom: int) -> float:
    return round(100.0 * num / denom, 2) if denom else 0.0


def _summarize(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"total": 0}

    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[str(r.get("review_type", "unknown"))].append(r)

    per_type: dict[str, Any] = {}
    overall_d_none = 0
    overall_a_none = 0
    overall_b_none = 0
    overall_c_none = 0
    overall_changed = 0
    overall_parse_ok = 0
    overall_schema_ok = 0

    for rt, sub in by_type.items():
        n = len(sub)
        d_none = sum(1 for r in sub if r.get("engine_d_state") is None)
        a_none = sum(1 for r in sub if r.get("engine_a_state") is None)
        b_none = sum(1 for r in sub if r.get("engine_b_state") is None)
        c_none = sum(1 for r in sub if r.get("engine_c_state") is None)
        changed = sum(1 for r in sub if r.get("ai_changed_execution_permission") is True)
        parse_ok = sum(1 for r in sub if r.get("parse_success") is True)
        schema_ok = sum(1 for r in sub if r.get("schema_valid") is True)

        # Final action distribution
        action_dist: Counter = Counter()
        for r in sub:
            action_dist[str(r.get("final_action", "unknown"))] += 1
        review_state: Counter = Counter()
        for r in sub:
            review_state[str(r.get("ai_review_state", "unknown"))] += 1

        per_type[rt] = {
            "total": n,
            "engine_d_state_none_pct": _pct(d_none, n),
            "engine_d_state_none_count": d_none,
            "engine_a_state_none_pct": _pct(a_none, n),
            "engine_b_state_none_pct": _pct(b_none, n),
            "engine_c_state_none_pct": _pct(c_none, n),
            "ai_changed_execution_pct": _pct(changed, n),
            "parse_success_pct": _pct(parse_ok, n),
            "schema_valid_pct": _pct(schema_ok, n),
            "final_action_distribution": dict(action_dist.most_common()),
            "ai_review_state_distribution": dict(review_state.most_common()),
        }

        overall_d_none += d_none
        overall_a_none += a_none
        overall_b_none += b_none
        overall_c_none += c_none
        overall_changed += changed
        overall_parse_ok += parse_ok
        overall_schema_ok += schema_ok

    return {
        "total_rows": len(rows),
        "review_types_count": len(by_type),
        "overall": {
            "engine_d_state_none_pct": _pct(overall_d_none, len(rows)),
            "engine_a_state_none_pct": _pct(overall_a_none, len(rows)),
            "engine_b_state_none_pct": _pct(overall_b_none, len(rows)),
            "engine_c_state_none_pct": _pct(overall_c_none, len(rows)),
            "ai_changed_execution_pct": _pct(overall_changed, len(rows)),
            "parse_success_pct": _pct(overall_parse_ok, len(rows)),
            "schema_valid_pct": _pct(overall_schema_ok, len(rows)),
        },
        "by_review_type": per_type,
    }


def _print_human(summary: dict[str, Any], path: Path) -> None:
    if summary.get("total_rows", 0) == 0:
        print(f"[audit_ai_review_engine_d_state] no rows found at {path}")
        return

    print(f"\n=== AI Review Audit — Engine D State Visibility ({summary['total_rows']} rows from {path.name}) ===\n")

    o = summary["overall"]
    print("--- Overall ---")
    print(f"  engine_d_state = None : {o['engine_d_state_none_pct']:6.2f}%   <-- audit §4.1 expectation: 100%")
    print(f"  engine_a_state = None : {o['engine_a_state_none_pct']:6.2f}%")
    print(f"  engine_b_state = None : {o['engine_b_state_none_pct']:6.2f}%")
    print(f"  engine_c_state = None : {o['engine_c_state_none_pct']:6.2f}%")
    print(f"  ai_changed_execution  : {o['ai_changed_execution_pct']:6.2f}%")
    print(f"  parse_success         : {o['parse_success_pct']:6.2f}%")
    print(f"  schema_valid          : {o['schema_valid_pct']:6.2f}%")

    print("\n--- Per review_type ---")
    for rt, s in summary["by_review_type"].items():
        print(f"\n  [{rt}]  n={s['total']}")
        print(f"    engine_d_state = None  : {s['engine_d_state_none_pct']:6.2f}%  ({s['engine_d_state_none_count']}/{s['total']})")
        print(f"    engine_a/b/c None pcts : {s['engine_a_state_none_pct']:.1f}% / {s['engine_b_state_none_pct']:.1f}% / {s['engine_c_state_none_pct']:.1f}%")
        print(f"    ai_changed_execution   : {s['ai_changed_execution_pct']:.2f}%")
        print(f"    parse_success          : {s['parse_success_pct']:.2f}%")
        print(f"    schema_valid           : {s['schema_valid_pct']:.2f}%")
        print(f"    final_action           : {s['final_action_distribution']}")
        print(f"    ai_review_state        : {s['ai_review_state_distribution']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH,
                        help="Path to ai_review_audit.jsonl")
    parser.add_argument("--since", type=str, default=None,
                        help="ISO-8601 timestamp lower bound")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Filter to a single symbol")
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
