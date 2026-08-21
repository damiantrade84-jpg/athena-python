"""Append-only JSONL journal for OX Alpha signals and execution outcomes."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("ox_alpha.journal")

_DIR = Path(__file__).parent / "_journal"
_PATH = _DIR / "ox_alpha_journal.jsonl"
_LOCK = threading.Lock()


def append(record: dict[str, Any], *, path: Path | None = None) -> None:
    target = path or _PATH
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    try:
        with _LOCK:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:  # journaling must never break trading paths
        log.debug("OX Alpha journal append failed: %s", exc)
