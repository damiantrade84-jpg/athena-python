"""audit_score_scale_split.py — Verify Engine A vs Engine D score-scale split.

Implements §7.3 of plans/engine-d-scoring-ai-review-audit-8577d0.md.

Audit claim (§1.9):
  - Engine A `confluenceScore` ranges 0-3 (`maxScore=3.0`); the run_full_scan
    path writes scores in this range.
  - Engine D `confluenceScore = quality.score / 100.0` and `maxScore = 1.0`
    (scalp_engine.py:2495-2496) — different scale, same field name.
  - The debate AI (`signal_debate.py:36-43`) infers scale from the value, so
    a 0.55 Engine D normalised score is presented as "0.55/1.0=55%" while
    a 0.95 Engine A raw score is presented as "0.95/1.0=95%" — same prompt
    string template, two different rubrics.

This tool reads:
  - logs/threshold_audit/signal_funnel.jsonl  (Engine A/B/C run_full_scan path)
  - logs/scalp_audit/engine_d_funnel.jsonl    (Engine D scalp path)

and reports the empirical score distributions per engine, so the user can see
whether the implied scales match what the audit predicts.

Read-only.

Usage:
  python tools/audit_score_scale_split.py
  python tools/audit_score_scale_split.py --since 2026-04-01T00:00:00+00:00
  python tools/audit_score_scale_split.py --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FUNNEL = ROOT / "logs" / "threshold_audit" / "signal_funnel.jsonl"
ENGINE_D_FUNNEL = ROOT / "logs" / "scalp_audit" / "engine_d_funnel.jsonl"


def _iter_rows(path: Path, since: str | None) -> Iterable[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
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
            out.append(row)
    return out


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "p95": round(sorted(values)[max(0, int(len(values) * 0.95) - 1)], 4),
    }


def _summarize(signal_rows: list[dict], engine_d_rows: list[dict]) -> dict[str, Any]:
    # Engine A — raw score lives under thresholds + scan_tier_reason text;
    # the per-engine fields available in signal_funnel.jsonl are the score
    # threshold & shadow values, plus engine_b_* numbers.  The directly
    # normalisable scoring in this log is engine_b_normalized_score (0-1)
    # and engine_b_raw_score / engine_b_max_score.
    eng_a_thr_current: list[float] = []
    eng_b_raw: list[float] = []
    eng_b_norm: list[float] = []
    eng_b_max: list[float] = []
    eng_c_conviction: list[float] = []
    final_results: dict[str, int] = {}

    for r in signal_rows:
        thresholds = r.get("thresholds") or {}
        a = thresholds.get("engine_a")
        if isinstance(a, (int, float)):
            eng_a_thr_current.append(float(a))
        for key, bucket in (
            ("engine_b_raw_score", eng_b_raw),
            ("engine_b_normalized_score", eng_b_norm),
            ("engine_b_max_score", eng_b_max),
            ("engine_c_final_conviction", eng_c_conviction),
        ):
            v = r.get(key)
            if isinstance(v, (int, float)):
                bucket.append(float(v))
        fr = str(r.get("final_scan_result", "unknown"))
        final_results[fr] = final_results.get(fr, 0) + 1

    # Engine D — setup_score 0-100 and grade letter
    eng_d_setup_score: list[float] = []
    eng_d_grade_dist: dict[str, int] = {}
    eng_d_pass_count = 0

    for r in engine_d_rows:
        s = r.get("setup_score")
        if isinstance(s, (int, float)):
            eng_d_setup_score.append(float(s))
        g = r.get("setup_grade")
        if g:
            key = str(g)
            eng_d_grade_dist[key] = eng_d_grade_dist.get(key, 0) + 1
        if r.get("gate_result") == "PASS":
            eng_d_pass_count += 1

    # Implied "confluenceScore" each engine writes onto a signal:
    #   Engine A: confluenceScore = engine_a_raw_score (0..3 range)
    #   Engine D: confluenceScore = setup_score / 100.0 (0..1 range)
    # Build implied 0-1 normalised distributions for direct comparison:
    eng_a_norm_implied = [s / 3.0 for s in eng_a_thr_current if s is not None]
    eng_d_norm_implied = [s / 100.0 for s in eng_d_setup_score]

    return {
        "engine_a_threshold_current": _stats(eng_a_thr_current),
        "engine_b_raw_score":         _stats(eng_b_raw),
        "engine_b_normalized_score":  _stats(eng_b_norm),
        "engine_b_max_score":         _stats(eng_b_max),
        "engine_c_final_conviction":  _stats(eng_c_conviction),
        "engine_d_setup_score":       _stats(eng_d_setup_score),
        "engine_d_grade_distribution": dict(sorted(eng_d_grade_dist.items())),
        "engine_d_pass_count":        eng_d_pass_count,
        "final_scan_result_distribution": final_results,
        "implied_confluence_norm": {
            "engine_a_norm_implied": _stats(eng_a_norm_implied),
            "engine_d_norm_implied": _stats(eng_d_norm_implied),
            "note": (
                "Engine A confluenceScore is raw (0-3 nominal); divide by maxScore=3 "
                "to compare against Engine D's quality.score/100 (already 0-1)."
            ),
        },
        "row_counts": {
            "signal_funnel_rows": len(signal_rows),
            "engine_d_funnel_rows": len(engine_d_rows),
        },
    }


def _print_human(summary: dict[str, Any]) -> None:
    rc = summary["row_counts"]
    if rc["signal_funnel_rows"] == 0 and rc["engine_d_funnel_rows"] == 0:
        print("[audit_score_scale_split] no rows in either log")
        return

    print(f"\n=== Score Scale Split — Engine A/B/C vs Engine D ===")
    print(f"  signal_funnel rows : {rc['signal_funnel_rows']}")
    print(f"  engine_d_funnel rows: {rc['engine_d_funnel_rows']}")

    def _fmt(label: str, s: dict[str, Any]) -> None:
        if s.get("n", 0) == 0:
            print(f"  {label:32s} (no data)")
            return
        print(
            f"  {label:32s} n={s['n']:5d}  min={s['min']:>8}  median={s['median']:>8}  "
            f"mean={s['mean']:>8}  p95={s['p95']:>8}  max={s['max']:>8}"
        )

    print("\n--- Engine A/B/C (run_full_scan path) ---")
    _fmt("engine_a threshold current",  summary["engine_a_threshold_current"])
    _fmt("engine_b_raw_score",          summary["engine_b_raw_score"])
    _fmt("engine_b_normalized_score",   summary["engine_b_normalized_score"])
    _fmt("engine_b_max_score",          summary["engine_b_max_score"])
    _fmt("engine_c_final_conviction",   summary["engine_c_final_conviction"])

    print("\n--- Engine D (scalp path) ---")
    _fmt("engine_d_setup_score (0-100)", summary["engine_d_setup_score"])
    print(f"  engine_d_grade_distribution    : {summary['engine_d_grade_distribution']}")
    print(f"  engine_d_pass_count            : {summary['engine_d_pass_count']}")

    print("\n--- Implied confluenceScore (normalised to 0-1 for direct comparison) ---")
    _fmt("engine_a norm (raw / 3.0)",   summary["implied_confluence_norm"]["engine_a_norm_implied"])
    _fmt("engine_d norm (raw / 100.0)", summary["implied_confluence_norm"]["engine_d_norm_implied"])
    print(f"  note: {summary['implied_confluence_norm']['note']}")

    print("\n--- final_scan_result distribution ---")
    for k, v in sorted(summary["final_scan_result_distribution"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:35s} {v:6d}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-funnel", type=Path, default=SIGNAL_FUNNEL)
    parser.add_argument("--engine-d-funnel", type=Path, default=ENGINE_D_FUNNEL)
    parser.add_argument("--since", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    sig_rows = list(_iter_rows(args.signal_funnel, args.since))
    eng_d_rows = list(_iter_rows(args.engine_d_funnel, args.since))
    summary = _summarize(sig_rows, eng_d_rows)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_human(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
