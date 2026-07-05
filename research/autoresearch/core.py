"""Core evaluation logic shared by run_autoresearch and autorun_loop."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research.autoresearch.config_loader import load_config
from research.autoresearch.git_ops import (
    ensure_worktree_safe_for_patch,
    git_apply,
    git_apply_check,
    git_checkout_files,
    git_diff_summary,
    save_patch_copy,
)
from research.autoresearch.ledger import append_ledger_entry
from research.autoresearch.metrics import (
    compute_score,
    evaluate_acceptance,
    parse_command_output,
)
from research.autoresearch.safety import (
    check_patch_safety,
    extract_files_from_patch_file,
)


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass
class EvaluationResult:
    metrics: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    command_results: list[CommandResult] = field(default_factory=list)
    ok: bool = False
    error: str = ""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_shell_command(command: str, *, cwd: Path, dry_run: bool = False) -> CommandResult:
    start = time.monotonic()
    if dry_run:
        return CommandResult(command, 0, "[dry-run]", "", 0.0)

    if not command.strip():
        return CommandResult(command, 1, "", "empty command", 0.0)

    proc = subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    duration = time.monotonic() - start
    return CommandResult(
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        duration_seconds=round(duration, 3),
    )


def run_evaluation_command(
    command: str,
    *,
    cwd: Path,
    config: dict[str, Any],
    dry_run: bool = False,
) -> EvaluationResult:
    if dry_run:
        stub = {
            "expR": 0.15,
            "profit_factor": 1.4,
            "sqn": 1.2,
            "max_drawdown_R": 1.0,
            "trade_count": 50,
            "oos_score": 0.12,
            "stability_score": 0.05,
            "symbol_concentration": 0.25,
        }
        score = compute_score(stub, config)
        return EvaluationResult(metrics=stub, score=score, ok=True)

    cmd_result = run_shell_command(command, cwd=cwd, dry_run=False)
    metrics_path = str(config.get("metrics_output_path") or "").strip() or None
    metrics = parse_command_output(
        cmd_result.stdout,
        cmd_result.stderr,
        metrics_file=metrics_path,
    )
    found = bool(metrics)
    score = compute_score(metrics, config) if found else 0.0
    ok = cmd_result.returncode == 0 and found
    error = "" if ok else "metrics_not_found_or_command_failed"
    return EvaluationResult(
        metrics=metrics,
        score=score,
        command_results=[cmd_result],
        ok=ok,
        error=error,
    )


def save_baseline_metrics(path: Path, metrics: dict[str, float], score: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metrics": metrics, "score": score}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_baseline_metrics(path: Path) -> tuple[dict[str, float], float]:
    if not path.exists():
        return {}, 0.0
    data = json.loads(path.read_text(encoding="utf-8"))
    metrics = data.get("metrics") or {}
    score = float(data.get("score") or 0.0)
    return metrics, score


def process_candidate(
    *,
    patch_path: Path,
    config: dict[str, Any],
    repo: Path,
    ledger_path: Path,
    baseline_metrics: dict[str, float],
    baseline_score: float,
    iteration: int = 0,
    mode: str = "single",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evaluate one candidate patch; accept or reject deterministically."""
    started = time.monotonic()
    commands_run: list[str] = []
    command_results: list[CommandResult] = []
    rejection_reasons: list[str] = []
    changed_files: list[str] = []
    accepted = False
    patch_saved_to = ""
    file_backups: dict[str, str | None] = {}

    print(f"\n=== Candidate: {patch_path.name} ===")

    if not patch_path.exists():
        rejection_reasons.append("patch_file_missing")
        entry = _build_ledger_entry(
            iteration=iteration,
            mode=mode,
            candidate_patch=str(patch_path),
            changed_files=changed_files,
            baseline_metrics=baseline_metrics,
            candidate_metrics={},
            baseline_score=baseline_score,
            candidate_score=0.0,
            accepted=False,
            rejection_reasons=rejection_reasons,
            commands_run=commands_run,
            git_diff_summary="",
            patch_saved_to=patch_saved_to,
            duration_seconds=time.monotonic() - started,
        )
        append_ledger_entry(ledger_path, entry)
        return entry

    changed_files = extract_files_from_patch_file(patch_path)
    safe, safety_reasons = check_patch_safety(
        changed_files,
        allowed_paths=config.get("allowed_paths", []),
        denied_keywords=config.get("denied_keywords", []),
    )

    if not safe:
        print(f"REJECTED (unsafe patch): {', '.join(safety_reasons)}")
        entry = _build_ledger_entry(
            iteration=iteration,
            mode=mode,
            candidate_patch=str(patch_path),
            changed_files=changed_files,
            baseline_metrics=baseline_metrics,
            candidate_metrics={},
            baseline_score=baseline_score,
            candidate_score=0.0,
            accepted=False,
            rejection_reasons=safety_reasons,
            commands_run=commands_run,
            git_diff_summary="",
            patch_saved_to=patch_saved_to,
            duration_seconds=time.monotonic() - started,
        )
        append_ledger_entry(ledger_path, entry)
        return entry

    tree_ok, tree_msg = ensure_worktree_safe_for_patch(repo)
    if not tree_ok and not dry_run:
        rejection_reasons.append(f"unsafe_worktree:{tree_msg}")
        print(f"STOP: {tree_msg}")
        entry = _build_ledger_entry(
            iteration=iteration,
            mode=mode,
            candidate_patch=str(patch_path),
            changed_files=changed_files,
            baseline_metrics=baseline_metrics,
            candidate_metrics={},
            baseline_score=baseline_score,
            candidate_score=0.0,
            accepted=False,
            rejection_reasons=rejection_reasons,
            commands_run=commands_run,
            git_diff_summary="",
            patch_saved_to=patch_saved_to,
            duration_seconds=time.monotonic() - started,
        )
        append_ledger_entry(ledger_path, entry)
        return entry

    if dry_run:
        print("[dry-run] Skipping git apply, tests, and evaluation.")
        candidate_metrics = dict(baseline_metrics)
        candidate_score = baseline_score + float(config.get("min_score_delta", 0.05)) + 0.01
        accepted = True
        rejection_reasons = []
    else:
        apply_ok, apply_err = git_apply_check(patch_path, repo)
        if not apply_ok:
            rejection_reasons.append(f"git_apply_check_failed:{apply_err}")
            print(f"REJECTED: {apply_err}")
            entry = _build_ledger_entry(
                iteration=iteration,
                mode=mode,
                candidate_patch=str(patch_path),
                changed_files=changed_files,
                baseline_metrics=baseline_metrics,
                candidate_metrics={},
                baseline_score=baseline_score,
                candidate_score=0.0,
                accepted=False,
                rejection_reasons=rejection_reasons,
                commands_run=commands_run,
                git_diff_summary="",
                patch_saved_to=patch_saved_to,
                duration_seconds=time.monotonic() - started,
            )
            append_ledger_entry(ledger_path, entry)
            return entry

        for rel in changed_files:
            target = repo / rel
            if target.exists():
                file_backups[rel] = target.read_text(encoding="utf-8")
            else:
                file_backups[rel] = None

        apply_ok, apply_err = git_apply(patch_path, repo)
        if not apply_ok:
            rejection_reasons.append(f"git_apply_failed:{apply_err}")
            entry = _build_ledger_entry(
                iteration=iteration,
                mode=mode,
                candidate_patch=str(patch_path),
                changed_files=changed_files,
                baseline_metrics=baseline_metrics,
                candidate_metrics={},
                baseline_score=baseline_score,
                candidate_score=0.0,
                accepted=False,
                rejection_reasons=rejection_reasons,
                commands_run=commands_run,
                git_diff_summary="",
                patch_saved_to=patch_saved_to,
                duration_seconds=time.monotonic() - started,
            )
            append_ledger_entry(ledger_path, entry)
            return entry

        test_cmd = str(config.get("test_command") or "").strip()
        tests_passed = True
        if test_cmd:
            commands_run.append(test_cmd)
            test_result = run_shell_command(test_cmd, cwd=repo)
            command_results.append(test_result)
            tests_passed = test_result.returncode == 0

        eval_cmd = str(config.get("evaluation_command") or "").strip()
        commands_run.append(eval_cmd)
        eval_result = run_evaluation_command(eval_cmd, cwd=repo, config=config)
        command_results.extend(eval_result.command_results)
        candidate_metrics = eval_result.metrics
        candidate_score = eval_result.score
        evaluation_ok = eval_result.ok

        accepted, accept_reasons = evaluate_acceptance(
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            config=config,
            safety_ok=safe,
            safety_reasons=[],
            tests_passed=tests_passed,
            evaluation_ok=evaluation_ok,
            metrics_found=bool(candidate_metrics),
        )
        if not accepted:
            rejection_reasons.extend(accept_reasons)

        diff_summary = git_diff_summary(changed_files, repo)

        if accepted:
            dest = repo / "research" / "autoresearch" / "accepted" / patch_path.name
            save_patch_copy(patch_path, dest)
            patch_saved_to = str(dest)
            print(f"ACCEPTED score={candidate_score:.4f} (baseline={baseline_score:.4f})")
        else:
            revert_ok, revert_err = git_checkout_files(changed_files, repo)
            if not revert_ok:
                _restore_file_backups(repo, file_backups)
            else:
                revert_err = ""
            if not revert_ok and not file_backups:
                rejection_reasons.append(f"revert_failed:{revert_err}")
            dest = repo / "research" / "autoresearch" / "rejected" / patch_path.name
            save_patch_copy(patch_path, dest)
            patch_saved_to = str(dest)
            print(f"REJECTED score={candidate_score:.4f} reasons={rejection_reasons}")
            if revert_ok:
                print(f"Reverted: {', '.join(changed_files)}")

        entry = _build_ledger_entry(
            iteration=iteration,
            mode=mode,
            candidate_patch=str(patch_path),
            changed_files=changed_files,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            accepted=accepted,
            rejection_reasons=rejection_reasons,
            commands_run=commands_run,
            git_diff_summary=diff_summary,
            patch_saved_to=patch_saved_to,
            duration_seconds=time.monotonic() - started,
        )
        append_ledger_entry(ledger_path, entry)
        return entry

    entry = _build_ledger_entry(
        iteration=iteration,
        mode=mode,
        candidate_patch=str(patch_path),
        changed_files=changed_files,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        baseline_score=baseline_score,
        candidate_score=candidate_score,
        accepted=accepted,
        rejection_reasons=rejection_reasons,
        commands_run=commands_run,
        git_diff_summary="[dry-run]",
        patch_saved_to=patch_saved_to,
        duration_seconds=time.monotonic() - started,
    )
    append_ledger_entry(ledger_path, entry)
    return entry


def run_baseline(
    config: dict[str, Any],
    *,
    repo: Path,
    dry_run: bool = False,
) -> EvaluationResult:
    cmd = str(config.get("baseline_command") or "").strip()
    print(f"Running baseline: {cmd}")
    return run_evaluation_command(cmd, cwd=repo, config=config, dry_run=dry_run)


def _build_ledger_entry(**kwargs: Any) -> dict[str, Any]:
    baseline_score = float(kwargs.get("baseline_score") or 0.0)
    candidate_score = float(kwargs.get("candidate_score") or 0.0)
    entry = dict(kwargs)
    entry["score_delta"] = round(candidate_score - baseline_score, 4)
    return entry


def _restore_file_backups(repo: Path, backups: dict[str, str | None]) -> None:
    for rel, content in backups.items():
        target = repo / rel
        if content is None:
            if target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
