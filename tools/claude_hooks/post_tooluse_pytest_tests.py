#!/usr/bin/env python3
"""
Claude Code PostToolUse hook: run pytest on the edited file when it is tests/test_*.py.

Reads hook JSON from stdin; prints hook JSON to stdout. Cross-platform (Windows-safe).

Triggered on every Write|Edit (.claude/settings.json); pytest runs only when file_path matches
tests/test_*.py (see _TEST_FILE). Success: one short system message. Failure: output capped.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_TEST_FILE = re.compile(r"(?:^|[\\/])tests[\\/]test_.+\.py$", re.IGNORECASE)
_OUT_MAX_LINES = 50


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cap_tail_lines(text: str, max_lines: int, *, omit_prefix: bool) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    omitted = len(lines) - max_lines
    head = ""
    if omit_prefix:
        head = f"... ({omitted} lines omitted)\n"
    return head + "\n".join(lines[-max_lines:])


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
            [
                sys.executable,
                "-m",
                "pytest",
                str(path),
                "-q",
                "--tb=line",
                "--disable-warnings",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        _emit(message=f"[pytest hook] Timeout after 120s: {path.name}")
        return

    out = proc.stdout or ""
    err = proc.stderr or ""

    if proc.returncode == 0:
        tail = "\n".join(out.strip().splitlines()[-2:])
        if tail:
            tail = _cap_tail_lines(tail, min(8, _OUT_MAX_LINES), omit_prefix=False)
        summary = tail or "passed"
        if len(summary) > 400:
            summary = summary[:397] + "..."
        _emit(message=f"[pytest hook] {path.name} exit 0 — {summary}")
        return

    combined = (out + err).strip() or "(no output)"
    combined = _cap_tail_lines(combined, _OUT_MAX_LINES, omit_prefix=True)
    _emit(message=f"[pytest hook] {path.name} exit {proc.returncode}\n{combined}")


if __name__ == "__main__":
    main()
