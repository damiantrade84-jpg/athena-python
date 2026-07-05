#!/usr/bin/env python
"""EdgeLab patch evaluator CLI (research-only)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.edgelab.audit import append_jsonl
from research.edgelab.config_loader import load_config
from research.edgelab.database import connect, init_db, insert_candidate_patch, insert_suggestion
from research.edgelab.git_worktree import (
    ensure_worktree_safe,
    git_apply,
    git_apply_check,
    git_checkout_files,
    git_diff_summary,
)
from research.edgelab.paths import ACCEPTED_DIR, DB_PATH, LEDGERS_DIR, REJECTED_DIR, REPO_ROOT
from research.edgelab.safety import check_patch_safety, extract_files_from_patch_file
from research.edgelab.scoring import compute_score, evaluate_candidate, parse_command_output


def _run_cmd(command: str, cwd: Path) -> tuple[int, str, str]:
    if not command.strip():
        return 0, "", ""
    proc = subprocess.run(command, shell=True, cwd=str(cwd), capture_output=True, text=True)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _restore_backups(repo: Path, backups: dict[str, str | None]) -> None:
    for rel, content in backups.items():
        target = repo / rel
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def evaluate_patch(
    patch_path: Path,
    *,
    config: dict | None = None,
    repo: Path | None = None,
    dry_run: bool = False,
    data_freshness_ok: bool = True,
    created_by: str = "daemon",
) -> dict:
    repo = repo or REPO_ROOT
    config = config or load_config()
    started = time.monotonic()
    init_db(DB_PATH)

    changed_files = extract_files_from_patch_file(patch_path)
    safe, safety_reasons = check_patch_safety(
        changed_files,
        allowed_paths=config.get("allowed_paths", []),
        denied_keywords=config.get("denied_keywords", []),
    )

    result: dict = {
        "candidate_patch": str(patch_path),
        "changed_files": changed_files,
        "accepted": False,
        "rejection_reasons": [],
        "commands_run": [],
    }

    if not safe:
        result["rejection_reasons"] = safety_reasons
        _persist_result(result, config, created_by=created_by)
        return result

    tree_ok, tree_msg = ensure_worktree_safe(repo)
    if not tree_ok and not dry_run:
        result["rejection_reasons"] = [f"unsafe_worktree:{tree_msg}"]
        _persist_result(result, config, created_by=created_by)
        return result

    baseline_cmd = str(config.get("baseline_command") or "")
    eval_cmd = str(config.get("evaluation_command") or "")
    test_cmd = str(config.get("test_command") or "")

    if dry_run:
        baseline_metrics = {"expR": 0.12, "trade_count": 45, "oos_score": 0.1, "max_drawdown_R": 1.2}
        baseline_score = compute_score(baseline_metrics, config)
        candidate_metrics = dict(baseline_metrics)
        candidate_score = baseline_score + float(config.get("min_score_delta", 0.05)) + 0.01
        result.update(
            {
                "baseline_metrics": baseline_metrics,
                "candidate_metrics": candidate_metrics,
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
                "accepted": True,
                "git_diff_summary": "[dry-run]",
            }
        )
        _persist_result(result, config, created_by=created_by)
        return result

    rc, out, err = _run_cmd(baseline_cmd, repo)
    result["commands_run"].append(baseline_cmd)
    baseline_metrics = parse_command_output(out, err, metrics_file=config.get("metrics_output_path") or None)
    baseline_score = compute_score(baseline_metrics, config) if baseline_metrics else 0.0

    ok, err_msg = git_apply_check(patch_path, repo)
    if not ok:
        result["rejection_reasons"] = [f"git_apply_check_failed:{err_msg}"]
        _persist_result(result, config, created_by=created_by)
        return result

    backups: dict[str, str | None] = {}
    for rel in changed_files:
        target = repo / rel
        backups[rel] = target.read_text(encoding="utf-8") if target.exists() else None

    ok, err_msg = git_apply(patch_path, repo)
    if not ok:
        result["rejection_reasons"] = [f"git_apply_failed:{err_msg}"]
        _persist_result(result, config, created_by=created_by)
        return result

    tests_passed = True
    if test_cmd and config.get("require_tests_pass", True):
        result["commands_run"].append(test_cmd)
        trc, tout, terr = _run_cmd(test_cmd, repo)
        tests_passed = trc == 0

    result["commands_run"].append(eval_cmd)
    erc, eout, eerr = _run_cmd(eval_cmd, repo)
    candidate_metrics = parse_command_output(eout, eerr, metrics_file=config.get("metrics_output_path") or None)
    candidate_score = compute_score(candidate_metrics, config) if candidate_metrics else 0.0
    evaluation_ok = erc == 0 and bool(candidate_metrics)

    accepted, reasons = evaluate_candidate(
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        baseline_score=baseline_score,
        candidate_score=candidate_score,
        config=config,
        safety_ok=True,
        safety_reasons=[],
        tests_passed=tests_passed,
        evaluation_ok=evaluation_ok,
        metrics_found=bool(candidate_metrics),
        data_freshness_ok=data_freshness_ok,
    )

    diff_summary = git_diff_summary(changed_files, repo)
    patch_saved = ""

    if accepted:
        ACCEPTED_DIR.mkdir(parents=True, exist_ok=True)
        dest = ACCEPTED_DIR / patch_path.name
        dest.write_text(patch_path.read_text(encoding="utf-8"), encoding="utf-8")
        patch_saved = str(dest)
    else:
        ok_rev, _ = git_checkout_files(changed_files, repo)
        if not ok_rev:
            _restore_backups(repo, backups)
        REJECTED_DIR.mkdir(parents=True, exist_ok=True)
        dest = REJECTED_DIR / patch_path.name
        dest.write_text(patch_path.read_text(encoding="utf-8"), encoding="utf-8")
        patch_saved = str(dest)

    result.update(
        {
            "baseline_metrics": baseline_metrics,
            "candidate_metrics": candidate_metrics,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "score_delta": round(candidate_score - baseline_score, 4),
            "accepted": accepted,
            "rejection_reasons": reasons,
            "git_diff_summary": diff_summary,
            "patch_saved_to": patch_saved,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    )
    _persist_result(result, config, created_by=created_by)
    return result


def _persist_result(result: dict, config: dict, *, created_by: str) -> None:
    LEDGERS_DIR.mkdir(parents=True, exist_ok=True)
    append_jsonl(LEDGERS_DIR / "patch_evaluations.jsonl", result)
    with connect(DB_PATH) as conn:
        insert_candidate_patch(
            conn,
            {
                "patch_path": result.get("candidate_patch"),
                "changed_files": result.get("changed_files"),
                "accepted": result.get("accepted"),
                "rejection_reasons": result.get("rejection_reasons"),
                "baseline_score": result.get("baseline_score"),
                "candidate_score": result.get("candidate_score"),
            },
        )
        status = "accepted_research" if result.get("accepted") else "rejected"
        insert_suggestion(
            conn,
            {
                "engine": "edgelab",
                "suggestion_type": "patch_evaluation",
                "title": "Accepted research patch" if result.get("accepted") else "Rejected research patch",
                "summary": ", ".join(result.get("rejection_reasons") or []) or "patch accepted",
                "baseline_metrics": result.get("baseline_metrics") or {},
                "candidate_metrics": result.get("candidate_metrics") or {},
                "score_delta": result.get("score_delta"),
                "status": status,
                "patch_path": result.get("patch_saved_to") or result.get("candidate_patch"),
                "rejection_reason": ", ".join(result.get("rejection_reasons") or []),
                "created_by": created_by,
                "risk_flags": [] if result.get("accepted") else ["research_rejected"],
            },
        )
        conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description="EdgeLab patch evaluator")
    ap.add_argument("--candidate-patch", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default="")
    args = ap.parse_args()

    cfg = load_config(Path(args.config)) if args.config else load_config()
    result = evaluate_patch(Path(args.candidate_patch), config=cfg, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
