#!/usr/bin/env python
"""CLI entry point for single baseline or candidate AutoResearch evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.autoresearch.config_loader import load_config
from research.autoresearch.core import (
    load_baseline_metrics,
    process_candidate,
    repo_root,
    run_baseline,
    save_baseline_metrics,
)


def _default_config_path() -> Path:
    return ROOT / "research" / "autoresearch" / "autoresearch_config.yaml"


def _default_ledger_path() -> Path:
    return ROOT / "research" / "autoresearch" / "results_ledger.jsonl"


def _default_baseline_marker() -> Path:
    return ROOT / "research" / "autoresearch" / "logs" / "baseline_metrics.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoResearch single-run evaluator (research-only)")
    ap.add_argument("--baseline-only", action="store_true", help="Run and save baseline metrics only")
    ap.add_argument("--candidate-patch", type=str, default="", help="Path to candidate .patch file")
    ap.add_argument("--dry-run", action="store_true", help="Skip git apply and subprocess evaluation")
    ap.add_argument("--max-iterations", type=int, default=1, help="Reserved for loop compatibility")
    ap.add_argument("--config", type=str, default=str(_default_config_path()))
    ap.add_argument("--ledger", type=str, default=str(_default_ledger_path()))
    ap.add_argument("--iteration", type=int, default=0)
    ap.add_argument("--mode", type=str, default="single")
    args = ap.parse_args()

    config_path = Path(args.config)
    ledger_path = Path(args.ledger)
    baseline_marker = _default_baseline_marker()
    repo = repo_root()

    config = load_config(config_path)
    dry_run = args.dry_run or bool(config.get("dry_run_default"))

    baseline_result = run_baseline(config, repo=repo, dry_run=dry_run)
    if not baseline_result.ok and not dry_run:
        print("ERROR: baseline evaluation failed or produced no metrics.", file=sys.stderr)
        if baseline_result.command_results:
            cr = baseline_result.command_results[0]
            print(cr.stderr or cr.stdout, file=sys.stderr)
        return 1

    baseline_metrics = baseline_result.metrics
    baseline_score = baseline_result.score
    save_baseline_metrics(baseline_marker, baseline_metrics, baseline_score)

    print(f"Baseline score: {baseline_score:.4f}")
    print(f"Baseline metrics: {json.dumps(baseline_metrics, indent=2)}")

    if args.baseline_only:
        print(f"Saved baseline to {baseline_marker}")
        return 0

    if not args.candidate_patch:
        print("No --candidate-patch supplied; baseline complete.")
        return 0

    patch_path = Path(args.candidate_patch)
    entry = process_candidate(
        patch_path=patch_path,
        config=config,
        repo=repo,
        ledger_path=ledger_path,
        baseline_metrics=baseline_metrics,
        baseline_score=baseline_score,
        iteration=args.iteration,
        mode=args.mode,
        dry_run=dry_run,
    )

    if entry.get("accepted"):
        save_baseline_metrics(baseline_marker, entry["candidate_metrics"], entry["candidate_score"])
        print("Baseline marker updated from accepted candidate.")

    return 0 if entry.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
