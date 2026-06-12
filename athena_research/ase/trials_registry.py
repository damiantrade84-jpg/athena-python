"""Append-only ASE trials registry (ASE v2.1 §10)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
TRIALS_PATH = OUTPUT_DIR / "trials.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def append_trial(
    *,
    family: str,
    horizon: str,
    config: dict[str, Any],
    results: dict[str, Any],
    notes: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    out = path or TRIALS_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "trial_id": str(uuid.uuid4()),
        "timestamp": _now_iso(),
        "family": family,
        "horizon": horizon,
        "config_hash": config_hash(config),
        "config": config,
        "results": results,
        "notes": notes,
    }
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row


def registry_hash(path: Path | None = None) -> str:
    p = path or TRIALS_PATH
    if not p.exists():
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(p.read_bytes()).hexdigest()
