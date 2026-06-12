"""Research/runtime parity checks (ASE v2.1 §8, §12)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def parity_payload(records: list[dict[str, Any]]) -> str:
    return json.dumps(records, sort_keys=True, default=str)


def parity_hash(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(parity_payload(records).encode()).hexdigest()


def check_parity(research: list[dict[str, Any]], runtime: list[dict[str, Any]]) -> bool:
    return parity_hash(research) == parity_hash(runtime)
