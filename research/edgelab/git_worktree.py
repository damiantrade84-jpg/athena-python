"""Git worktree safety for EdgeLab patch application."""

from __future__ import annotations

import subprocess
from pathlib import Path

from research.edgelab.safety import is_edgelab_bootstrap_path


def _run_git(args: list[str], *, cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
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
    for line in (proc.stdout or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((line[:2].strip(), path.replace("\\", "/")))
    return rows


def ensure_worktree_safe(repo_root: Path) -> tuple[bool, str]:
    dirty = git_status_porcelain(repo_root)
    if not dirty:
        return True, "clean"
    outside = [p for _s, p in dirty if not is_edgelab_bootstrap_path(p)]
    if outside:
        return False, f"working tree dirty outside edgelab research paths: {', '.join(outside)}"
    return True, "edgelab_bootstrap_dirty"


def git_apply_check(patch_path: Path, repo_root: Path) -> tuple[bool, str]:
    proc = _run_git(["apply", "--check", str(patch_path)], cwd=repo_root)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "git apply --check failed").strip()


def git_apply(patch_path: Path, repo_root: Path) -> tuple[bool, str]:
    proc = _run_git(["apply", str(patch_path)], cwd=repo_root)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "git apply failed").strip()


def git_checkout_files(files: list[str], repo_root: Path) -> tuple[bool, str]:
    if not files:
        return True, ""
    proc = _run_git(["checkout", "--", *files], cwd=repo_root)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "git checkout failed").strip()


def git_diff_summary(files: list[str], repo_root: Path) -> str:
    if not files:
        return ""
    proc = _run_git(["diff", "--stat", "--", *files], cwd=repo_root)
    return (proc.stdout or proc.stderr or "").strip()
