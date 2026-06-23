"""Append-only JSONL journal for TSMOM live signals and execution outcomes.
Mirrors the ASE shadow-journal intent: every decision and every order outcome is
recorded so demo behaviour is auditable and accumulates an OOS forward record."""
from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.Lock()
_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_journal", "tsmom_live_journal.jsonl")


def journal_path() -> str:
    return os.environ.get("TSMOM_LIVE_JOURNAL", _DEFAULT)


def append(row: dict) -> None:
    """Best-effort append; journaling must never break the decision path."""
    rec = {"ts_ms": int(time.time() * 1000), **row}
    try:
        path = journal_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _LOCK, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass
