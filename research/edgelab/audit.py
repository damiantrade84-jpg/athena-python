"""JSONL audit logging for EdgeLab cycles."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if "timestamp" not in entry:
        entry["timestamp"] = _utcnow()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def cycle_ledger_path(ledgers_dir: Path, cycle: int) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return ledgers_dir / f"cycle_{day}.jsonl"
