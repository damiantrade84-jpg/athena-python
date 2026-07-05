"""Patch safety for EdgeLab (wraps AutoResearch checks + edgelab paths)."""

from __future__ import annotations

from research.autoresearch.safety import (
    check_patch_safety,
    extract_files_from_patch,
    extract_files_from_patch_file,
)


def is_edgelab_bootstrap_path(path: str) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    allowed = (
        "research/edgelab/",
        "research/autoresearch/",
        "research/__init__.py",
        "tests/research/",
    )
    return any(norm == p.rstrip("/") or norm.startswith(p) for p in allowed)


__all__ = [
    "check_patch_safety",
    "extract_files_from_patch",
    "extract_files_from_patch_file",
    "is_edgelab_bootstrap_path",
]
