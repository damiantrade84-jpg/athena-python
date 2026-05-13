"""Session-scrape NDJSON for debug mode — no secrets."""

from __future__ import annotations

import json
import time
from pathlib import Path


def append_agent_ndjson(payload: dict) -> None:
    """Append one NDJSON line under repo root debug-e50c7f.log."""
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "debug-e50c7f.log"
    row = {
        "sessionId": "e50c7f",
        "timestamp": int(time.time() * 1000),
        **payload,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
    except OSError:
        pass
