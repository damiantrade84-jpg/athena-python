"""Patch safety checks: whitelist paths and denylist keywords."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath


_DIFF_FILE_RE = re.compile(
    r"^(?:---|\+\+\+|diff --git)\s+(?:a/|b/)?(.+?)(?:\s|$)",
    re.MULTILINE,
)
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def _normalize_path(path: str) -> str:
    p = path.strip().replace("\\", "/")
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    return p.lstrip("./")


def extract_files_from_patch(patch_text: str) -> list[str]:
    """Return unique file paths touched by a unified diff."""
    found: set[str] = set()
    for match in _DIFF_GIT_RE.finditer(patch_text):
        found.add(_normalize_path(match.group(1)))
        found.add(_normalize_path(match.group(2)))
    for match in _DIFF_FILE_RE.finditer(patch_text):
        raw = match.group(1)
        if raw in ("/dev/null", "dev/null"):
            continue
        found.add(_normalize_path(raw))
    return sorted(found)


def extract_files_from_patch_file(patch_path: Path) -> list[str]:
    text = patch_path.read_text(encoding="utf-8", errors="replace")
    return extract_files_from_patch(text)


def _path_matches_allowed(normalized: str, allowed_paths: list[str]) -> bool:
    posix = PurePosixPath(normalized)
    for allowed in allowed_paths:
        prefix = allowed.rstrip("/") + "/"
        if normalized == allowed.rstrip("/") or normalized.startswith(prefix):
            return True
        if posix.parts and posix.parts[0] == allowed.rstrip("/"):
            return True
    return False


def _path_matches_denied(normalized: str, denied_keywords: list[str]) -> list[str]:
    lower = normalized.lower().replace("\\", "/")
    hits = []
    for kw in denied_keywords:
        if kw.lower() in lower:
            hits.append(kw)
    return hits


def check_patch_safety(
    files: list[str],
    *,
    allowed_paths: list[str],
    denied_keywords: list[str],
) -> tuple[bool, list[str]]:
    """Return (is_safe, rejection_reasons)."""
    reasons: list[str] = []
    if not files:
        reasons.append("patch_contains_no_file_paths")
        return False, reasons

    for f in files:
        norm = _normalize_path(f)
        denied = _path_matches_denied(norm, denied_keywords)
        if denied:
            reasons.append(f"denied_keyword_in_path:{norm}:{'+'.join(denied)}")
            continue
        if not _path_matches_allowed(norm, allowed_paths):
            reasons.append(f"not_whitelisted:{norm}")

    return len(reasons) == 0, reasons


def is_autoresearch_only_path(path: str) -> bool:
    """Paths that may be dirty while AutoResearch runs (bootstrap + autoresearch tree)."""
    norm = _normalize_path(path)
    allowed = (
        "research/autoresearch/",
        "research/__init__.py",
        "tests/research/",
    )
    return any(norm == prefix.rstrip("/") or norm.startswith(prefix) for prefix in allowed)
