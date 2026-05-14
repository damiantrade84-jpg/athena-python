#!/usr/bin/env python3
"""
Claude Code PostToolUse hook: run pytest on the edited file when it is tests/test_*.py.

Reads hook JSON from stdin; prints hook JSON to stdout. Cross-platform (Windows-safe).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_TEST_FILE = re.compile(r"(?:^|[\\/])tests[\\/]test_.+\.py$", re.IGNORECASE)
_MAX_MSG = 12_000


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _emit(continue_: bool = True, suppress: bool = False, message: str | None = None) -> None:
    out: dict = {"continue": continue_, "suppressOutput": suppress}
    if message:
        out["systemMessage"] = message
    print(json.dumps(out), flush=True)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        _emit(suppress=True)
        return

    ti = data.get("tool_input") or {}
    fp = (ti.get("file_path") or "").strip()
    if not fp or not _TEST_FILE.search(str(Path(fp))):
        _emit(suppress=True)
        return

    root = _repo_root()
    path = Path(fp)
    if not path.is_absolute():
        path = root / path

    if not path.is_file():
        _emit(message=f"[pytest hook] Skipped — not a file: {path}")
        return

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "-q", "--tb=short"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        _emit(message=f"[pytest hook] Timeout after 120s: {path.name}")
        return

    combined = (proc.stdout or "") + (proc.stderr or "")
    if len(combined) > _MAX_MSG:
        combined = "...[truncated]\n" + combined[-_MAX_MSG :]

    _emit(
        message=f"[pytest hook] {path.name} exit {proc.returncode}\n{combined or '(no output)'}"
    )


if __name__ == "__main__":
    main()
