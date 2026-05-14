#!/usr/bin/env python3
"""
Claude Code PreToolUse hook: notice when editing execution-critical Python modules.

Does not block edits; injects systemMessage only. Cross-platform (Windows-safe).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_EXEC_FILES = re.compile(
    r"(?:^|[\\/])(execution|risk_engine|mt5_executor|bybit_executor|auto_trader)\.py$",
    re.IGNORECASE,
)


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
    if not fp or not _EXEC_FILES.search(str(Path(fp))):
        _emit(suppress=True)
        return

    _emit(
        message=(
            "EXECUTION-SAFETY: This edit targets a broker/risk/execution-critical module. "
            "Confirm intentional scope: no gate bypass, SL/TP preservation, "
            "paper-only defaults, kill-switch/freshness intact, and focused tests. "
            f"File: {fp}"
        )
    )


if __name__ == "__main__":
    main()
