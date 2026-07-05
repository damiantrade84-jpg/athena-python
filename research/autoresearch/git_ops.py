"""Git safety helpers for applying and reverting research patches."""

from __future__ import annotations

import subprocess
from pathlib import Path

from research.autoresearch.safety import is_autoresearch_only_path


def _run_git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def git_status_porcelain(repo_root: Path) -> list[tuple[str, str]]:
    proc = _run_git(["status", "--porcelain", "-uall"], cwd=repo_root)
    rows: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2].strip()
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((status, path.replace("\\", "/")))
    return rows


def ensure_worktree_safe_for_patch(repo_root: Path) -> tuple[bool, str]:
    """Allow clean tree or dirty only under research/autoresearch/."""
    dirty = git_status_porcelain(repo_root)
    if not dirty:
        return True, "clean"

    outside: list[str] = []
    for _status, path in dirty:
        if not is_autoresearch_only_path(path):
            outside.append(path)

    if outside:
        return False, f"working tree dirty outside autoresearch: {', '.join(outside)}"
    return True, "autoresearch_only_dirty"


def git_apply_check(patch_path: Path, repo_root: Path) -> tuple[bool, str]:
    proc = _run_git(["apply", "--check", str(patch_path)], cwd=repo_root, check=False)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "git apply --check failed").strip()


def git_apply(patch_path: Path, repo_root: Path) -> tuple[bool, str]:
    proc = _run_git(["apply", str(patch_path)], cwd=repo_root, check=False)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "git apply failed").strip()


def git_checkout_files(files: list[str], repo_root: Path) -> tuple[bool, str]:
    if not files:
        return True, ""
    proc = _run_git(["checkout", "--", *files], cwd=repo_root, check=False)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "git checkout failed").strip()


def git_diff_summary(files: list[str], repo_root: Path) -> str:
    if not files:
        return ""
    proc = _run_git(["diff", "--stat", "--", *files], cwd=repo_root, check=False)
    return (proc.stdout or proc.stderr or "").strip()


def save_patch_copy(patch_path: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(patch_path.read_text(encoding="utf-8"), encoding="utf-8")
